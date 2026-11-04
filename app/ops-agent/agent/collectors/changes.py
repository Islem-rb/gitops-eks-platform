from datetime import datetime, timezone

import httpx

from ..models import Evidence
from . import safely
from .kubernetes import KubeReader


def _age_seconds(timestamp: str | None, now: datetime) -> int | None:
    if not timestamp:
        return None
    return int((now - datetime.fromisoformat(timestamp.replace("Z", "+00:00"))).total_seconds())


def deploy_history(kube: KubeReader, argo_app: str) -> dict:
    app = kube.get(f"/apis/argoproj.io/v1alpha1/namespaces/argocd/applications/{argo_app}")
    history = app.get("status", {}).get("history", [])[-5:]
    now = datetime.now(timezone.utc)
    return {
        "application": argo_app,
        "sync_status": app.get("status", {}).get("sync", {}).get("status"),
        "health": app.get("status", {}).get("health", {}).get("status"),
        "deploys": [
            {
                "revision": h.get("revision"),
                "deployed_at": h.get("deployedAt"),
                "age_seconds": _age_seconds(h.get("deployedAt"), now),
            }
            for h in reversed(history)
        ],
    }


def recent_commits(github: httpx.Client, repo: str) -> list[dict]:
    response = github.get(f"/repos/{repo}/commits", params={"path": "gitops", "per_page": 5})
    response.raise_for_status()
    return [
        {
            "sha": c["sha"][:12],
            "date": c["commit"]["committer"]["date"],
            "subject": c["commit"]["message"].splitlines()[0][:120],
        }
        for c in response.json()
    ]


def collect(kube: KubeReader, github: httpx.Client, argo_app: str, repo: str) -> list[Evidence]:
    return [
        safely("changes.deploys", "argocd", lambda: deploy_history(kube, argo_app)),
        safely("changes.commits", "github", lambda: recent_commits(github, repo)),
    ]
