import hmac
import os
from pathlib import Path

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from pydantic import BaseModel

from agent.collectors import changes, kubernetes, loki, prometheus
from agent.models import Evidence
from agent.queries import NAMESPACE
from agent.service import Deduper, ReportStore, investigate, notify

PROMETHEUS_URL = os.getenv(
    "PROMETHEUS_URL", "http://kps-prometheus.monitoring.svc.cluster.local:9090"
)
LOKI_URL = os.getenv("LOKI_URL", "http://loki.monitoring.svc.cluster.local:3100")
ARGO_APP = os.getenv("ARGO_APP", "demo-kind")
GITHUB_REPO = os.getenv("GITHUB_REPO", "Islem-rb/gitops-eks-platform")
REPORT_DIR = os.getenv("REPORT_DIR", "/data/investigations")
DEDUP_SECONDS = float(os.getenv("DEDUP_SECONDS", "600"))
WEBHOOK_TOKEN_FILE = os.getenv("WEBHOOK_TOKEN_FILE", "/etc/ops-agent/webhook/token")
DISCORD_URL_FILE = os.getenv("DISCORD_URL_FILE", "/etc/ops-agent/discord/url")

INVESTIGATIONS = Counter("ops_agent_investigations_total", "Investigations", ["category"])
DEDUPED = Counter("ops_agent_alerts_deduplicated_total", "Alerts covered by a running incident")

app = FastAPI(title="ops-agent")
store = ReportStore(REPORT_DIR)
deduper = Deduper(DEDUP_SECONDS)


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


def run_investigation(trigger: str, alerts: list[str]) -> None:
    report = investigate(collect_all, trigger, alerts)
    store.save(report)
    INVESTIGATIONS.labels(report.findings[0].category).inc()
    try:
        with httpx.Client(timeout=10) as client:
            notify(client, _read_secret(DISCORD_URL_FILE), report)
    except httpx.HTTPError as exc:  # the report is saved; a Discord failure must not lose it
        print(f"discord notification failed: {type(exc).__name__}", flush=True)
    print(f"investigation {report.id}: {report.findings[0].category}", flush=True)


class Alert(BaseModel):
    status: str = "firing"
    labels: dict[str, str] = {}


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
    groups: dict[str, list[str]] = {}
    for alert in payload.alerts:
        # Alert content is untrusted: labels only select an incident, and only in our namespace
        if alert.status == "firing" and alert.labels.get("namespace") == NAMESPACE:
            key = f"{NAMESPACE}/{alert.labels.get('app', 'unknown')}"
            groups.setdefault(key, []).append(alert.labels.get("alertname", "unknown"))

    started, deduplicated = [], []
    for key, alerts in groups.items():
        if deduper.should_run(key):
            tasks.add_task(run_investigation, f"alert:{key}", sorted(set(alerts)))
            started.append(key)
        else:
            DEDUPED.inc()
            deduplicated.append(key)
    return {"started": started, "deduplicated": deduplicated}


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
