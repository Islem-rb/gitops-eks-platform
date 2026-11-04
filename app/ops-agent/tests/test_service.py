import httpx
import pytest
from fastapi.testclient import TestClient

import main
from agent.models import Bundle, Evidence, Finding, Report
from agent.service import (
    DISCORD_LIMIT,
    IncidentTracker,
    ReportStore,
    investigate_alert,
    notify,
)

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def alert(name, namespace="demo", app="api", status="firing", starts="2026-11-04T10:00:00Z"):
    labels = {"alertname": name, "namespace": namespace, "app": app}
    return {"status": status, "labels": labels, "fingerprint": f"fp-{name}", "startsAt": starts}


@pytest.fixture
def api(tmp_path, monkeypatch):
    token_file = tmp_path / "token"
    token_file.write_text(TOKEN + "\n")
    monkeypatch.setattr(main, "WEBHOOK_TOKEN_FILE", str(token_file))
    monkeypatch.setattr(main, "tracker", IncidentTracker(600))
    monkeypatch.setattr(main, "ALERT_SETTLE_SECONDS", 0)
    monkeypatch.setattr(main, "ALERT_RECHECK_SECONDS", 0)
    main._pending_alerts.clear()
    monkeypatch.setattr(main, "store", ReportStore(str(tmp_path / "reports")))
    collections = []

    def collect():
        collections.append(1)
        return [
            Evidence(id="k8s.pods", source="t", data=[]),
            Evidence(id="k8s.rollouts", source="t", data=[{"name": "api", "phase": "Healthy"}]),
            Evidence(id="prom.error_rate", source="t", data=[{"labels": {}, "value": 0.0}]),
            Evidence(id="loki.errors", source="t", data={"signatures": {}}),
        ]

    monkeypatch.setattr(main, "collect_all", collect)
    client = TestClient(main.app)
    client.collections = collections
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


def test_alerts_arriving_together_start_one_investigation(api):
    payload = {"alerts": [alert("ApiHighErrorRate"), alert("ApiErrorBudgetFastBurn")]}
    response = api.post("/webhook/alertmanager", json=payload, headers=AUTH)
    assert response.status_code == 202
    assert response.json()["accepted"] == ["ApiErrorBudgetFastBurn", "ApiHighErrorRate"]
    # 3 passes, because an alert the rules cannot explain is inconclusive
    assert len(api.collections) == 3


def test_resent_alert_is_ignored(api):
    payload = {"alerts": [alert("ApiHighErrorRate")]}
    api.post("/webhook/alertmanager", json=payload, headers=AUTH)
    collected = len(api.collections)
    response = api.post("/webhook/alertmanager", json=payload, headers=AUTH)
    assert response.json() == {"accepted": [], "repeated": ["ApiHighErrorRate"]}
    assert len(api.collections) == collected


def test_same_alert_firing_again_later_is_a_new_investigation(api):
    # Seen on a real cluster: a separate fault minutes later got no investigation at all
    api.post("/webhook/alertmanager", json={"alerts": [alert("ApiDown")]}, headers=AUTH)
    collected = len(api.collections)
    later = alert("ApiDown", starts="2026-11-04T10:08:00Z")
    response = api.post("/webhook/alertmanager", json={"alerts": [later]}, headers=AUTH)
    assert response.json()["accepted"] == ["ApiDown"]
    assert len(api.collections) > collected


def test_resolved_and_other_namespace_alerts_are_ignored(api):
    payload = {"alerts": [alert("A", status="resolved"), alert("B", namespace="kube-system")]}
    response = api.post("/webhook/alertmanager", json=payload, headers=AUTH)
    assert response.json() == {"accepted": [], "repeated": []}
    assert api.collections == []


def test_same_finding_is_notified_once_but_a_different_one_gets_through():
    now = [0.0]
    tracker = IncidentTracker(600, clock=lambda: now[0])
    assert tracker.should_notify("dependency_down") is True
    now[0] = 60
    assert tracker.should_notify("dependency_down") is False
    assert tracker.should_notify("db_auth_failure") is True
    now[0] = 2000
    assert tracker.should_notify("dependency_down") is True


def passes(*categories):
    """A fake collection whose pod states make the rules return each category in turn."""
    remaining = list(categories)

    def collect():
        category = remaining.pop(0)
        crash = {"name": "w", "app": "worker", "ready": False, "restarts": 3, "last_exit_code": 1}
        pods = [crash] if category == "crashloop" else []
        healthy = [{"name": "api", "phase": "Healthy"}]
        rollout = healthy if category != "canary_aborted" else [
            {"name": "api", "phase": "Degraded", "aborted": True}
        ]
        return [
            Evidence(id="k8s.pods", source="t", data=pods),
            Evidence(id="k8s.rollouts", source="t", data=rollout),
            Evidence(id="prom.error_rate", source="t", data=[{"labels": {}, "value": 0.0}]),
            Evidence(id="loki.errors", source="t", data={"signatures": {}}),
        ]

    return collect


def run_alert(collect, slept):
    return investigate_alert(collect, "alert:demo", ["A"], 60, 30, 3, sleep=slept.append)


def test_alert_waits_before_collecting_and_stops_when_conclusive():
    slept = []
    report = run_alert(passes("canary_aborted"), slept)
    assert slept == [60]
    assert report.passes == 1
    assert report.findings[0].category == "canary_aborted"


def test_symptom_is_rechecked_and_the_sharper_finding_wins():
    # Seen on a real cluster: crashloop came first and the cause only appeared in later logs
    slept = []
    report = run_alert(passes("crashloop", "canary_aborted"), slept)
    assert slept == [60, 30]
    assert report.passes == 2
    assert report.findings[0].category == "canary_aborted"


def test_rechecking_stops_at_the_limit():
    slept = []
    report = run_alert(passes("crashloop", "crashloop", "crashloop"), slept)
    assert report.passes == 3
    assert report.findings[0].category == "crashloop"


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
