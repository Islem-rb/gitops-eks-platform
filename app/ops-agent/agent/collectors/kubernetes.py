import os
import ssl
from pathlib import Path

import httpx

from ..models import Evidence
from ..queries import NAMESPACE
from . import safely

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
ROLLOUTS = "/apis/argoproj.io/v1alpha1"
MAX_EVENTS = 50


class KubeReader:
    """Kubernetes API access. It can only send GET requests, whatever RBAC would allow."""

    def __init__(self, client: httpx.Client):
        self._client = client

    @classmethod
    def in_cluster(cls) -> "KubeReader":
        host = os.environ["KUBERNETES_SERVICE_HOST"]
        port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
        token = (SA_DIR / "token").read_text().strip()
        return cls(
            httpx.Client(
                base_url=f"https://{host}:{port}",
                headers={"Authorization": f"Bearer {token}"},
                verify=ssl.create_default_context(cafile=str(SA_DIR / "ca.crt")),
                timeout=10,
            )
        )

    def get(self, path: str, **params) -> dict:
        response = self._client.get(path, params=params)
        response.raise_for_status()
        return response.json()


def _pod(item: dict) -> dict:
    status = item.get("status", {})
    containers = status.get("containerStatuses", [])
    waiting = [c["state"]["waiting"].get("reason") for c in containers if "waiting" in c["state"]]
    last = [
        c["lastState"]["terminated"]
        for c in containers
        if "terminated" in c.get("lastState", {})
    ]
    labels = item["metadata"].get("labels", {})
    return {
        "name": item["metadata"]["name"],
        "app": labels.get("app") or labels.get("k8s-app"),
        "revision": labels.get("rollouts-pod-template-hash"),
        "phase": status.get("phase"),
        "ready": bool(containers) and all(c.get("ready") for c in containers),
        "restarts": sum(c.get("restartCount", 0) for c in containers),
        "node": item.get("spec", {}).get("nodeName"),
        "waiting_reason": waiting[0] if waiting else None,
        "last_termination_reason": last[0].get("reason") if last else None,
        "last_exit_code": last[0].get("exitCode") if last else None,
    }


def pods(kube: KubeReader, namespace: str = NAMESPACE) -> list[dict]:
    return [_pod(i) for i in kube.get(f"/api/v1/namespaces/{namespace}/pods")["items"]]


def warning_events(kube: KubeReader) -> list[dict]:
    items = kube.get(f"/api/v1/namespaces/{NAMESPACE}/events", fieldSelector="type=Warning")
    ordered = sorted(
        items["items"],
        key=lambda e: e.get("lastTimestamp") or e.get("eventTime") or "",
        reverse=True,
    )
    return [
        {
            "reason": e.get("reason"),
            "object": f'{e["involvedObject"].get("kind")}/{e["involvedObject"].get("name")}',
            "message": (e.get("message") or "")[:300],
            "count": e.get("count", 1),
            "last_seen": e.get("lastTimestamp") or e.get("eventTime"),
        }
        for e in ordered[:MAX_EVENTS]
    ]


def deployments(kube: KubeReader) -> list[dict]:
    items = kube.get(f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments")["items"]
    return [
        {
            "name": d["metadata"]["name"],
            "desired": d.get("spec", {}).get("replicas", 0),
            "ready": d.get("status", {}).get("readyReplicas", 0),
        }
        for d in items
    ]


def rollouts(kube: KubeReader) -> list[dict]:
    items = kube.get(f"{ROLLOUTS}/namespaces/{NAMESPACE}/rollouts")["items"]
    result = []
    for r in items:
        status = r.get("status", {})
        result.append(
            {
                "name": r["metadata"]["name"],
                "phase": status.get("phase"),
                "aborted": bool(status.get("abort")),
                "stable_revision": status.get("stableRS"),
                "current_revision": status.get("currentPodHash"),
                "message": (status.get("message") or "")[:300],
            }
        )
    return result


def analysis_runs(kube: KubeReader) -> list[dict]:
    items = kube.get(f"{ROLLOUTS}/namespaces/{NAMESPACE}/analysisruns")["items"]
    newest = sorted(items, key=lambda a: a["metadata"]["creationTimestamp"], reverse=True)[:3]
    result = []
    for run in newest:
        metrics = []
        for metric in run.get("status", {}).get("metricResults", []):
            measurements = metric.get("measurements", [])
            metrics.append(
                {
                    "name": metric.get("name"),
                    "phase": metric.get("phase"),
                    "failed": metric.get("failed", 0),
                    "successful": metric.get("successful", 0),
                    "last_value": measurements[-1].get("value") if measurements else None,
                }
            )
        result.append(
            {
                "name": run["metadata"]["name"],
                "created_at": run["metadata"]["creationTimestamp"],
                "revision": run["metadata"].get("labels", {}).get("rollouts-pod-template-hash"),
                "phase": run.get("status", {}).get("phase"),
                "metrics": metrics,
            }
        )
    return result


def system_pods(kube: KubeReader) -> list[dict]:
    items = kube.get(
        "/api/v1/namespaces/kube-system/pods", labelSelector="k8s-app in (aws-node,kube-dns)"
    )["items"]
    return [_pod(i) for i in items]


def nodes(kube: KubeReader) -> list[dict]:
    result = []
    for node in kube.get("/api/v1/nodes")["items"]:
        conditions = {c["type"]: c["status"] for c in node.get("status", {}).get("conditions", [])}
        result.append(
            {
                "name": node["metadata"]["name"],
                "ready": conditions.get("Ready") == "True",
                "pressure": sorted(
                    k for k, v in conditions.items() if k.endswith("Pressure") and v == "True"
                ),
            }
        )
    return result


def collect(kube: KubeReader) -> list[Evidence]:
    return [
        safely("k8s.pods", "kubernetes", lambda: pods(kube)),
        safely("k8s.events", "kubernetes", lambda: warning_events(kube)),
        safely("k8s.deployments", "kubernetes", lambda: deployments(kube)),
        safely("k8s.rollouts", "kubernetes", lambda: rollouts(kube)),
        safely("k8s.analysisruns", "kubernetes", lambda: analysis_runs(kube)),
        safely("k8s.system_pods", "kubernetes", lambda: system_pods(kube)),
        safely("k8s.nodes", "kubernetes", lambda: nodes(kube)),
    ]
