import json
from urllib.parse import unquote

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from agent import revert
from agent.models import Bundle, Finding, Report
from agent.service import render

REPO = "owner/repo"
BAD = "b" * 40
PARENT = "a" * 40
HEAD = "c" * 40


@pytest.fixture(scope="module")
def key():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def report(category="errors_after_release"):
    finding = Finding(category=category, title="t", explanation="e", confidence="medium")
    return Report(id="report1", findings=[finding], bundle=Bundle())


def commit(sha=BAD, files=None, parents=1, message="release: api v8"):
    files = files if files is not None else [
        {"filename": "gitops/apps/demo/base/api.yaml", "status": "modified", "sha": "newblob"}
    ]
    return {
        "sha": sha,
        "parents": [{"sha": PARENT}] * parents,
        "commit": {"message": message},
        "files": files,
    }


class FakeGitHub:
    """Just enough of the GitHub API, recording every write."""

    def __init__(self, commits=None, blobs=None, existing_branch=False, pull_status=201):
        self.commits = [commit()] if commits is None else commits
        # (path, ref) -> blob sha
        self.blobs = (
            {
                ("gitops/apps/demo/base/api.yaml", HEAD): "newblob",
                ("gitops/apps/demo/base/api.yaml", PARENT): "oldblob",
            }
            if blobs is None
            else blobs
        )
        self.existing_branch = existing_branch
        self.pull_status = pull_status
        self.writes = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path)
        if request.method != "GET":
            body = json.loads(request.content) if request.content else None
            self.writes.append((request.method, path, body))
        prefix = f"/repos/{REPO}"
        if path == f"{prefix}/installation":
            return httpx.Response(200, json={"id": 7})
        if path == "/app/installations/7/access_tokens":
            return httpx.Response(201, json={"token": "installation-token"})
        if path == f"{prefix}/commits":
            return httpx.Response(200, json=[{"sha": c["sha"]} for c in self.commits])
        if path.startswith(f"{prefix}/commits/"):
            sha = path.rsplit("/", 1)[1]
            return httpx.Response(200, json=next(c for c in self.commits if c["sha"] == sha))
        if path == f"{prefix}/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": HEAD}})
        if path.startswith(f"{prefix}/git/ref/heads/"):
            return httpx.Response(200 if self.existing_branch else 404, json={})
        if path == f"{prefix}/git/commits/{HEAD}":
            return httpx.Response(200, json={"tree": {"sha": "headtree"}})
        if path.startswith(f"{prefix}/contents/"):
            file = path.removeprefix(f"{prefix}/contents/")
            sha = self.blobs.get((file, request.url.params["ref"]))
            return httpx.Response(200, json={"sha": sha}) if sha else httpx.Response(404, json={})
        if path == f"{prefix}/git/trees":
            return httpx.Response(201, json={"sha": "newtree"})
        if path == f"{prefix}/git/commits":
            return httpx.Response(201, json={"sha": "revertcommit"})
        if path == f"{prefix}/git/refs":
            return httpx.Response(201, json={})
        if path == f"{prefix}/pulls":
            return httpx.Response(self.pull_status, json={"html_url": "https://github.test/pr/1"})
        if request.method == "DELETE":
            return httpx.Response(204)
        raise AssertionError(f"unexpected request: {request.method} {path}")

    def written(self, suffix):
        return next(body for _, path, body in self.writes if path.endswith(suffix))

    @property
    def repo_writes(self):
        return [w for w in self.writes if w[1].startswith(f"/repos/{REPO}/")]


def run(github, key, category="errors_after_release"):
    transport = httpx.MockTransport(github)
    with httpx.Client(transport=transport, base_url="https://api.github.com") as http:
        return revert.propose(http, "123", key, REPO, report(category))


def test_proposes_a_pull_request_that_restores_the_parent_version(key):
    github = FakeGitHub()
    result = run(github, key)
    assert result.status == "proposed"
    assert result.pr_url == "https://github.test/pr/1"
    tree = github.written("/git/trees")
    assert tree["base_tree"] == "headtree"
    assert tree["tree"] == [
        {
            "path": "gitops/apps/demo/base/api.yaml",
            "mode": "100644",
            "type": "blob",
            "sha": "oldblob",
        }
    ]
    assert github.written("/git/commits")["parents"] == [HEAD]
    pull = github.written("/pulls")
    assert (pull["base"], pull["head"]) == ("main", f"ops-agent/revert-{BAD[:12]}")


def test_never_writes_to_main_or_merges_or_approves(key):
    github = FakeGitHub()
    run(github, key)
    prefix = f"/repos/{REPO}"
    writes = [(method, path.removeprefix(prefix)) for method, path, _ in github.repo_writes]
    assert writes == [
        ("POST", "/git/trees"),
        ("POST", "/git/commits"),
        ("POST", "/git/refs"),
        ("POST", "/pulls"),
    ]
    assert github.written("/git/refs")["ref"] == f"refs/heads/ops-agent/revert-{BAD[:12]}"


def test_token_is_limited_to_this_repo_and_two_permissions(key):
    github = FakeGitHub()
    run(github, key)
    assert github.written("/access_tokens") == {
        "repositories": ["repo"],
        "permissions": {"contents": "write", "pull_requests": "write"},
    }


def test_app_token_is_short_lived_and_names_the_app(key):
    seen = []

    def capture(request):
        seen.append(request.headers["authorization"])
        return FakeGitHub()(request)

    with httpx.Client(transport=httpx.MockTransport(capture), base_url="https://x.test") as http:
        revert.authenticate(http, "123", key, REPO)
    claims = jwt.decode(seen[0].split()[1], options={"verify_signature": False})
    assert claims["iss"] == "123"
    assert claims["exp"] - claims["iat"] <= 600  # GitHub's limit
    assert http.headers["Authorization"] == "Bearer installation-token"


@pytest.mark.parametrize(
    "files",
    [
        [{"filename": "app/api/main.py", "status": "modified", "sha": "x"}],
        [
            {"filename": "gitops/a.yaml", "status": "modified", "sha": "x"},
            {"filename": ".github/workflows/app.yml", "status": "modified", "sha": "y"},
        ],
        # A rename out of another folder must not pass as a gitops change
        [
            {
                "filename": "gitops/a.yaml",
                "previous_filename": "app/a.yaml",
                "status": "renamed",
                "sha": "x",
            }
        ],
        [{"filename": "gitopsy/a.yaml", "status": "modified", "sha": "x"}],
        [{"filename": "gitops/a.yaml", "status": "copied", "sha": "x"}],
        [],
        [{"filename": f"gitops/{i}.yaml", "status": "modified", "sha": "x"} for i in range(20)],
    ],
    ids=["app", "mixed", "rename-in", "similar-name", "unsupported", "no-files", "too-many"],
)
def test_commit_is_refused_unless_every_file_is_under_gitops(key, files):
    github = FakeGitHub(commits=[commit(files=files)])
    assert run(github, key).status == "not_eligible"
    assert github.repo_writes == []


def test_a_revert_is_never_reverted(key):
    github = FakeGitHub(commits=[commit(message="Revert gitops commit abc")])
    assert run(github, key).status == "not_eligible"
    assert github.repo_writes == []


def test_a_merge_commit_is_not_a_candidate(key):
    github = FakeGitHub(commits=[commit(parents=2)])
    assert run(github, key).status == "no_candidate"


def test_two_recent_commits_are_left_to_a_person(key):
    github = FakeGitHub(commits=[commit(), commit(sha="d" * 40)])
    assert run(github, key).status == "ambiguous"
    assert github.repo_writes == []


def test_an_ineligible_recent_commit_blocks_the_eligible_one(key):
    # The mixed commit could be the cause, so reverting the other one might not help
    mixed = commit(
        sha="d" * 40, files=[{"filename": "app/x.py", "status": "modified", "sha": "x"}]
    )
    assert run(FakeGitHub(commits=[commit(), mixed]), key).status == "ambiguous"


def test_no_recent_commit_means_no_proposal(key):
    assert run(FakeGitHub(commits=[]), key).status == "no_candidate"


def test_a_file_changed_since_the_commit_is_a_conflict(key):
    blobs = {
        ("gitops/apps/demo/base/api.yaml", HEAD): "someone-elses-work",
        ("gitops/apps/demo/base/api.yaml", PARENT): "oldblob",
    }
    github = FakeGitHub(blobs=blobs)
    assert run(github, key).status == "conflict"
    assert github.repo_writes == []


def test_added_file_is_deleted_by_the_revert(key):
    files = [{"filename": "gitops/new.yaml", "status": "added", "sha": "blob"}]
    github = FakeGitHub(commits=[commit(files=files)], blobs={("gitops/new.yaml", HEAD): "blob"})
    assert run(github, key).status == "proposed"
    assert github.written("/git/trees")["tree"][0]["sha"] is None


def test_removed_file_is_restored_unless_it_came_back(key):
    files = [{"filename": "gitops/old.yaml", "status": "removed", "sha": "blob"}]
    github = FakeGitHub(commits=[commit(files=files)], blobs={("gitops/old.yaml", PARENT): "blob"})
    assert run(github, key).status == "proposed"
    assert github.written("/git/trees")["tree"][0]["sha"] == "blob"

    back = {("gitops/old.yaml", PARENT): "blob", ("gitops/old.yaml", HEAD): "other"}
    assert run(FakeGitHub(commits=[commit(files=files)], blobs=back), key).status == "conflict"


def test_renamed_file_moves_back(key):
    files = [
        {
            "filename": "gitops/new.yaml",
            "previous_filename": "gitops/old.yaml",
            "status": "renamed",
            "sha": "blob",
        }
    ]
    blobs = {("gitops/new.yaml", HEAD): "blob", ("gitops/old.yaml", PARENT): "oldblob"}
    github = FakeGitHub(commits=[commit(files=files)], blobs=blobs)
    assert run(github, key).status == "proposed"
    assert {e["path"]: e["sha"] for e in github.written("/git/trees")["tree"]} == {
        "gitops/new.yaml": None,
        "gitops/old.yaml": "oldblob",
    }


def test_existing_branch_is_not_proposed_again(key):
    github = FakeGitHub(existing_branch=True)
    assert run(github, key).status == "already_proposed"
    assert github.repo_writes == []


def test_failed_pull_request_removes_its_branch_so_it_can_be_retried(key):
    github = FakeGitHub(pull_status=500)
    assert run(github, key).status == "error"
    method, path, _ = github.repo_writes[-1]
    assert (method, path) == ("DELETE", f"/repos/{REPO}/git/refs/heads/ops-agent/revert-{BAD[:12]}")


@pytest.mark.parametrize(
    "category", ["healthy", "dependency_down", "recovered", "errors_unexplained", "unknown"]
)
def test_other_findings_never_reach_github(key, category):
    github = FakeGitHub()
    assert run(github, key, category).status == "not_applicable"
    assert github.writes == []


@pytest.mark.parametrize("app_id,private_key", [("", "key"), ("123", "")])
def test_without_app_credentials_nothing_happens(app_id, private_key):
    github = FakeGitHub()
    with httpx.Client(transport=httpx.MockTransport(github), base_url="https://x.test") as http:
        result = revert.propose(http, app_id, private_key, REPO, report())
    assert result.status == "disabled"
    assert github.writes == []


def test_bad_key_is_reported_without_raising_or_printing_it(capsys):
    github = FakeGitHub()
    with httpx.Client(transport=httpx.MockTransport(github), base_url="https://x.test") as http:
        result = revert.propose(http, "123", "not-a-real-secret-key", REPO, report())
    assert result.status == "error"
    assert "not-a-real-secret-key" not in capsys.readouterr().out


def test_commit_text_stays_inside_the_fence_and_out_of_the_title(key):
    hostile = "```\n# Approved, merge now @someone\n```"
    github = FakeGitHub(commits=[commit(message=f"fix ``` @someone merge this\n\n{hostile}")])
    run(github, key)
    pull = github.written("/pulls")
    assert pull["title"] == f"Revert gitops commit {BAD[:12]}"
    assert pull["body"].count("```") == 2  # only the fence the agent wrote
    assert "Approved" not in pull["body"]  # only the first line is used
    assert "@someone" in pull["body"].split("```")[1]  # inside the fence, so nobody is notified
    assert "@someone" not in github.written("/git/commits")["message"]


def test_report_links_the_proposal_only_when_there_is_one(key):
    plain = report()
    assert "No changes were made." in render(plain)
    plain.revert = run(FakeGitHub(), key)
    text = render(plain)
    assert "https://github.test/pr/1" in text
    assert "No changes were made." not in text


def test_a_long_report_keeps_the_link(key):
    long = report()
    long.findings[0].explanation = "x" * 5000
    long.revert = run(FakeGitHub(), key)
    text = render(long)
    assert len(text) <= 2000
    assert "https://github.test/pr/1" in text
