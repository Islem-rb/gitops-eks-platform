import httpx
import pytest
from fastapi.testclient import TestClient

import main
from agent.models import Bundle, Finding, Report
from agent.service import DISCORD_LIMIT, Deduper, ReportStore, notify

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def alert(name, namespace="demo", app="api", status="firing"):
    labels = {"alertname": name, "namespace": namespace, "app": app}
    return {"status": status, "labels": labels}


@pytest.fixture
def api(tmp_path, monkeypatch):
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN + "\n")
    runs = []
    monkeypatch.setattr(main, "WEBHOOK_TOKEN_FILE", str(token_file))
    monkeypatch.setattr(main, "deduper", Deduper(600))
    monkeypatch.setattr(main, "store", ReportStore(str(tmp_path / "reports")))
    monkeypatch.setattr(main, "run_investigation", lambda t, a: runs.append((t, a)))
    client = TestClient(main.app)
    client.runs = runs
    return client


def test_healthz_needs_no_token(api):
    assert api.get("/healthz").status_code == 200


def test_webhook_rejects_a_bad_token(api):
    response = api.post("/webhook/alertmanager", json={"alerts": []})
    assert response.status_code == 401
    bad = {"Authorization": "Bearer wrong"}
    assert api.post("/webhook/alertmanager", json={"alerts": []}, headers=bad).status_code == 401


def test_unconfigured_token_refuses_everything(api, monkeypatch):
    monkeypatch.setattr(main, "WEBHOOK_TOKEN_FILE", "/does/not/exist")
    assert api.post("/investigations", headers={"Authorization": "Bearer "}).status_code == 401


def test_alerts_for_one_incident_start_one_investigation(api):
    payload = {"alerts": [alert("ApiHighErrorRate"), alert("ApiErrorBudgetFastBurn")]}
    first = api.post("/webhook/alertmanager", json=payload, headers=AUTH)
    second = api.post("/webhook/alertmanager", json=payload, headers=AUTH)
    assert first.status_code == 202
    assert first.json()["started"] == ["demo/api"]
    assert second.json() == {"started": [], "deduplicated": ["demo/api"]}
    assert api.runs == [("alert:demo/api", ["ApiErrorBudgetFastBurn", "ApiHighErrorRate"])]


def test_resolved_and_other_namespace_alerts_are_ignored(api):
    payload = {"alerts": [alert("A", status="resolved"), alert("B", namespace="kube-system")]}
    response = api.post("/webhook/alertmanager", json=payload, headers=AUTH)
    assert response.json() == {"started": [], "deduplicated": []}
    assert api.runs == []


def test_report_id_cannot_traverse_paths(api):
    for bad in ["..%2F..%2Fetc%2Fpasswd", "abc", "A" * 32]:
        assert api.get(f"/investigations/{bad}", headers=AUTH).status_code == 404


def test_reading_a_report_needs_the_token(api):
    assert api.get(f"/investigations/{'a' * 32}").status_code == 401


def report(**finding):
    values = {"category": "x", "title": "t", "explanation": "e", "confidence": "low"} | finding
    return Report(id="a" * 32, findings=[Finding(**values)], bundle=Bundle())


def test_store_round_trip(tmp_path):
    store = ReportStore(str(tmp_path))
    store.save(report())
    assert store.load("a" * 32).findings[0].category == "x"
    assert store.load("b" * 32) is None


def discord_client(sent):
    def handler(request):
        sent.append(request)
        return httpx.Response(204)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_discord_message_cannot_ping_and_stays_within_the_limit():
    sent = []
    text = "@everyone " + "x" * 5000
    notify(discord_client(sent), "http://discord.test/hook", report(explanation=text))
    body = sent[0].read().decode()
    assert '"allowed_mentions":{"parse":[]}' in body.replace(" ", "")
    assert len(httpx.Response(200, content=body).json()["content"]) <= DISCORD_LIMIT


def test_no_discord_url_sends_nothing():
    sent = []
    notify(discord_client(sent), "", report())
    assert sent == []


def test_deduper_runs_again_after_the_window():
    now = [0.0]
    deduper = Deduper(10, clock=lambda: now[0])
    assert deduper.should_run("k") is True
    assert deduper.should_run("k") is False
    now[0] = 11
    assert deduper.should_run("k") is True
