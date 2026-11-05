"""Propose one action: a pull request reverting one commit that only changed gitops/.

The backend chooses the commit, and only when there is exactly one to choose from. No log text or
model output reaches this decision. The bot can open a pull request but can't merge it or push to
main, so a person always decides.
"""

import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx
import jwt

from .models import Report, RevertProposal
from .rules import RECENT_DEPLOY_SECONDS

REVERTABLE_CATEGORIES = {"canary_aborted", "canary_errors", "errors_after_release"}
ALLOWED_PREFIX = "gitops/"
SUPPORTED_STATUSES = {"added", "modified", "changed", "removed", "renamed"}
MAX_FILES = 20
MAX_COMMITS = 10
SUBJECT_LIMIT = 120
BRANCH_PREFIX = "ops-agent/revert-"
TOKEN_PERMISSIONS = {"contents": "write", "pull_requests": "write"}


class _Conflict(Exception):
    """A file changed after the commit, so reverting it could undo someone else's work."""


def authenticate(http: httpx.Client, app_id: str, private_key: str, repo: str) -> None:
    """Exchange the App key for a short-lived token limited to this repo and these permissions."""
    now = int(time.time())
    token = jwt.encode({"iat": now - 60, "exp": now + 540, "iss": app_id}, private_key, "RS256")
    bearer = {"Authorization": f"Bearer {token}"}
    response = http.get(f"/repos/{repo}/installation", headers=bearer)
    response.raise_for_status()
    response = http.post(
        f"/app/installations/{response.json()['id']}/access_tokens",
        headers=bearer,
        json={"repositories": [repo.split("/")[1]], "permissions": TOKEN_PERMISSIONS},
    )
    response.raise_for_status()
    http.headers["Authorization"] = f"Bearer {response.json()['token']}"


def is_eligible(commit: dict) -> bool:
    files = commit.get("files", [])
    if len(commit["parents"]) != 1 or commit["commit"]["message"].startswith("Revert "):
        return False
    if not files or len(files) >= MAX_FILES:  # a very large commit may list only some of its files
        return False
    paths = [f["filename"] for f in files] + [
        f["previous_filename"] for f in files if f.get("previous_filename")
    ]
    return all(p.startswith(ALLOWED_PREFIX) for p in paths) and all(
        f["status"] in SUPPORTED_STATUSES for f in files
    )


def recent_commits(http: httpx.Client, repo: str, now: datetime) -> list[dict]:
    since = now - timedelta(seconds=RECENT_DEPLOY_SECONDS)
    response = http.get(
        f"/repos/{repo}/commits",
        params={
            "sha": "main",
            "path": ALLOWED_PREFIX.rstrip("/"),
            "since": since.isoformat(),
            "per_page": MAX_COMMITS,
        },
    )
    response.raise_for_status()
    commits = []
    for item in response.json():
        detail = http.get(f"/repos/{repo}/commits/{item['sha']}")
        detail.raise_for_status()
        commits.append(detail.json())
    return commits


def _blob(http: httpx.Client, repo: str, path: str, ref: str) -> str | None:
    response = http.get(f"/repos/{repo}/contents/{quote(path)}", params={"ref": ref})
    if response.status_code == 404:
        return None
    response.raise_for_status()
    body = response.json()
    if isinstance(body, list):  # the path is now a directory
        raise _Conflict
    return body["sha"]


def _revert_entries(http: httpx.Client, repo: str, commit: dict, head: str) -> list[dict]:
    """Tree entries that restore each file, as long as it hasn't changed since the commit."""
    parent = commit["parents"][0]["sha"]
    entries = []

    def entry(path: str, sha: str | None) -> dict:
        return {"path": path, "mode": "100644", "type": "blob", "sha": sha}

    def restore(path: str) -> dict:
        sha = _blob(http, repo, path, parent)
        if sha is None:
            raise _Conflict
        return entry(path, sha)

    for file in commit["files"]:
        path, status = file["filename"], file["status"]
        current = _blob(http, repo, path, head)
        if status == "removed":
            if current is not None:
                raise _Conflict
            entries.append(restore(path))
            continue
        if current != file["sha"]:
            raise _Conflict
        if status == "added":
            entries.append(entry(path, None))
        elif status == "renamed":
            if _blob(http, repo, file["previous_filename"], head) is not None:
                raise _Conflict
            entries += [entry(path, None), restore(file["previous_filename"])]
        else:
            entries.append(restore(path))
    return entries


def _body(commit: dict, category: str, report_id: str) -> str:
    subject = commit["commit"]["message"].splitlines()[0][:SUBJECT_LIMIT].replace("`", "'")
    return (
        f"ops-agent's rules reported `{category}`. This is the only commit under `gitops/` in the "
        "last 30 minutes, so it may be the cause. **That is a correlation, not proof.**\n\n"
        "Commit subject, shown as untrusted text:\n\n"
        f"```text\n{subject}\n```\n\n"
        "Check the diff and the evidence before merging. Argo CD syncs it after a merge.\n\n"
        f"Commit: {commit['sha']}\nReport id: {report_id}\n"
    )


def open_pull_request(
    http: httpx.Client, repo: str, commit: dict, category: str, report_id: str
) -> RevertProposal:
    sha = commit["sha"]
    short = sha[:12]
    branch = f"{BRANCH_PREFIX}{short}"
    # A branch left behind means this was proposed before, and a closed PR is a person's answer
    existing = http.get(f"/repos/{repo}/git/ref/heads/{branch}")
    if existing.status_code == 200:
        return RevertProposal(status="already_proposed", commit=short)
    if existing.status_code != 404:
        existing.raise_for_status()

    ref = http.get(f"/repos/{repo}/git/ref/heads/main")
    ref.raise_for_status()
    head = ref.json()["object"]["sha"]
    head_commit = http.get(f"/repos/{repo}/git/commits/{head}")
    head_commit.raise_for_status()
    try:
        entries = _revert_entries(http, repo, commit, head)
    except _Conflict:
        return RevertProposal(status="conflict", commit=short)

    tree = http.post(
        f"/repos/{repo}/git/trees",
        json={"base_tree": head_commit.json()["tree"]["sha"], "tree": entries},
    )
    tree.raise_for_status()
    created = http.post(
        f"/repos/{repo}/git/commits",
        json={
            "message": f"Revert gitops commit {short}\n\nThis reverts commit {sha}.",
            "tree": tree.json()["sha"],
            "parents": [head],
        },
    )
    created.raise_for_status()
    http.post(
        f"/repos/{repo}/git/refs",
        json={"ref": f"refs/heads/{branch}", "sha": created.json()["sha"]},
    ).raise_for_status()
    pull = http.post(
        f"/repos/{repo}/pulls",
        json={
            "title": f"Revert gitops commit {short}",
            "head": branch,
            "base": "main",
            "body": _body(commit, category, report_id),
        },
    )
    if pull.is_error:  # with the branch left behind, this would never be proposed again
        http.delete(f"/repos/{repo}/git/refs/heads/{branch}")
        pull.raise_for_status()
    return RevertProposal(status="proposed", commit=short, pr_url=pull.json()["html_url"])


def propose(
    http: httpx.Client, app_id: str, private_key: str, repo: str, report: Report
) -> RevertProposal:
    """Never raises: a failure here must not lose the investigation's report."""
    category = report.findings[0].category
    if not app_id or not private_key:
        return RevertProposal(status="disabled")
    if category not in REVERTABLE_CATEGORIES:
        return RevertProposal(status="not_applicable")
    try:
        authenticate(http, app_id, private_key, repo)
        commits = recent_commits(http, repo, datetime.now(timezone.utc))
        # A merge commit adds nothing of its own, but any other commit could be the cause
        commits = [c for c in commits if len(c["parents"]) == 1]
        if not commits:
            return RevertProposal(status="no_candidate")
        if len(commits) > 1:
            return RevertProposal(status="ambiguous")
        if not is_eligible(commits[0]):
            return RevertProposal(status="not_eligible", commit=commits[0]["sha"][:12])
        return open_pull_request(http, repo, commits[0], category, report.id)
    except (httpx.HTTPError, jwt.PyJWTError, ValueError, TypeError, KeyError) as exc:
        # The type only: details could include key material
        print(f"revert proposal failed: {type(exc).__name__}", flush=True)
        return RevertProposal(status="error")
