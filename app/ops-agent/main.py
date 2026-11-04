import hmac
import os
import threading
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from pydantic import BaseModel

from agent.collectors import changes, kubernetes, loki, prometheus
from agent.models import Evidence
from agent.queries import NAMESPACE
from agent.service import IncidentTracker, ReportStore, investigate, investigate_alert, notify

PROMETHEUS_URL = os.getenv(
    "PROMETHEUS_URL", "http://kps-prometheus.monitoring.svc.cluster.local:9090"
)
LOKI_URL = os.getenv("LOKI_URL", "http://loki.monitoring.svc.cluster.local:3100")
ARGO_APP = os.getenv("ARGO_APP", "demo-kind")
GITHUB_REPO = os.getenv("GITHUB_REPO", "Islem-rb/gitops-eks-platform")
REPORT_DIR = os.getenv("REPORT_DIR", "/data/investigations")
NOTIFY_WINDOW_SECONDS = float(os.getenv("NOTIFY_WINDOW_SECONDS", "1800"))
ALERT_SETTLE_SECONDS = float(os.getenv("ALERT_SETTLE_SECONDS", "60"))
ALERT_RECHECK_SECONDS = float(os.getenv("ALERT_RECHECK_SECONDS", "60"))
ALERT_MAX_PASSES = int(os.getenv("ALERT_MAX_PASSES", "3"))
WEBHOOK_TOKEN_FILE = os.getenv("WEBHOOK_TOKEN_FILE", "/etc/ops-agent/webhook/token")
DISCORD_URL_FILE = os.getenv("DISCORD_URL_FILE", "/etc/ops-agent/discord/url")

INVESTIGATIONS = Counter("ops_agent_investigations_total", "Investigations", ["category"])
REPEATED = Counter("ops_agent_alerts_repeated_total", "Resent alerts that were ignored")
SUPPRESSED = Counter("ops_agent_notifications_suppressed_total", "Findings already reported")

app = FastAPI(title="ops-agent")
store = ReportStore(REPORT_DIR)
tracker = IncidentTracker(NOTIFY_WINDOW_SECONDS)
_pending_alerts: set[str] = set()
_pending_lock = threading.Lock()
_run_lock = threading.Lock()


def _read_secret(path: str) -> str:
    file = Path(path)
    return file.read_text().strip() if file.exists() else ""


def require_token(authorization: str = Header(default="")) -> None:
    expected = _read_secret(WEBHOOK_TOKEN_FILE)
    # With no token configured, refuse everything rather than run open
    if not expected or not hmac.compare_digest(authorization, f"Bearer {expected}"):
        raise HTTPException(status_code=401, detail="invalid token")


def collect_all() -> list[Evidence]:
    timeout = httpx.Timeout(10)
    kube = kubernetes.KubeReader.in_cluster()
    with (
        httpx.Client(base_url=PROMETHEUS_URL, timeout=timeout) as prom,
        httpx.Client(base_url=LOKI_URL, timeout=timeout) as logs,
        httpx.Client(base_url="https://api.github.com", timeout=timeout) as github,
    ):
        return [
            *kubernetes.collect(kube),
            *prometheus.collect(prom),
            *loki.collect(logs),
            *changes.collect(kube, github, ARGO_APP, GITHUB_REPO),
        ]


def process_pending_alerts() -> None:
    """One at a time. Alerts that arrive during a run are handled together by the next one."""
    with _run_lock:
        with _pending_lock:
            alerts = sorted(_pending_alerts)
            _pending_alerts.clear()
        if not alerts:
            return
        report = investigate_alert(
            collect_all,
            f"alert:{NAMESPACE}",
            alerts,
            ALERT_SETTLE_SECONDS,
            ALERT_RECHECK_SECONDS,
            ALERT_MAX_PASSES,
        )
        store.save(report)
        category = report.findings[0].category
        INVESTIGATIONS.labels(category).inc()
        notified = tracker.should_notify(category)
        if notified:
            try:
                with httpx.Client(timeout=10) as client:
                    notify(client, _read_secret(DISCORD_URL_FILE), report)
            except httpx.HTTPError as exc:  # the report is saved; Discord must not lose it
                print(f"discord notification failed: {type(exc).__name__}", flush=True)
        else:
            SUPPRESSED.inc()
        print(f"investigation {report.id}: {category} notified={notified}", flush=True)


class Alert(BaseModel):
    status: str = "firing"
    labels: dict[str, str] = {}
    fingerprint: str = ""
    startsAt: str = ""


class AlertmanagerPayload(BaseModel):
    alerts: list[Alert] = []


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/webhook/alertmanager", status_code=202, dependencies=[Depends(require_token)])
def alertmanager_webhook(payload: AlertmanagerPayload, tasks: BackgroundTasks):
    accepted, repeated = [], []
    for alert in payload.alerts:
        # Alert content is untrusted: labels only select an incident, and only in our namespace
        if alert.status != "firing" or alert.labels.get("namespace") != NAMESPACE:
            continue
        name = alert.labels.get("alertname", "unknown")
        if tracker.is_new_alert(alert.fingerprint or name, alert.startsAt):
            accepted.append(name)
        else:
            REPEATED.inc()
            repeated.append(name)
    if accepted:
        with _pending_lock:
            _pending_alerts.update(accepted)
        tasks.add_task(process_pending_alerts)
    return {"accepted": sorted(set(accepted)), "repeated": sorted(set(repeated))}


@app.post("/investigations", dependencies=[Depends(require_token)])
def run_now():
    """Run an investigation with no alert and return it. Used by the evaluation harness."""
    report = investigate(collect_all, "manual", [])
    store.save(report)
    INVESTIGATIONS.labels(report.findings[0].category).inc()
    return report


@app.get("/investigations/{report_id}", dependencies=[Depends(require_token)])
def get_investigation(report_id: str):
    report = store.load(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="not found")
    return report
