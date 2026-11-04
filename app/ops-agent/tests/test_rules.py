from agent.models import Bundle, Evidence
from agent.rules import evaluate


def bundle(*evidence, alerts=()):
    return Bundle(alerts=list(alerts), evidence=list(evidence))


def ev(evidence_id, data):
    return Evidence(id=evidence_id, source="test", data=data)


CLEAN = [
    ev("k8s.pods", []),
    ev("k8s.rollouts", [{"name": "api", "phase": "Healthy"}]),
    ev("prom.error_rate", [{"labels": {}, "value": 0.0}]),
    ev("loki.errors", {"signatures": {}}),
]


def test_clean_evidence_is_healthy():
    assert evaluate(bundle(*CLEAN))[0].category == "healthy"


def test_missing_evidence_is_never_healthy():
    failed = Evidence(id="prom.error_rate", source="test", ok=False, error="ConnectError")
    findings = evaluate(bundle(CLEAN[0], CLEAN[1], CLEAN[3], failed))
    assert findings[0].category == "unknown"
    assert "prom.error_rate" in findings[0].explanation


def test_alert_with_no_matching_rule_is_unknown_not_healthy():
    findings = evaluate(bundle(*CLEAN, alerts=["ApiDown"]))
    assert findings[0].category == "unknown"
    assert "ApiDown" in findings[0].explanation


def test_failed_collection_is_listed_as_an_unknown():
    failed = Evidence(id="k8s.nodes", source="test", ok=False, error="ReadTimeout")
    finding = evaluate(bundle(*CLEAN, failed))[0]
    assert any("k8s.nodes" in note for note in finding.unknowns)


def test_broken_log_pipeline_is_never_healthy():
    # Seen on a real cluster: Loki was still starting, and an empty result looked like no errors
    silent = Evidence(id="loki.errors", source="test", ok=False, error="NoLogsInWindow")
    findings = evaluate(bundle(*CLEAN[:3], silent))
    assert findings[0].category == "unknown"
    assert "loki.errors" in findings[0].explanation


def test_nan_error_rate_does_not_raise():
    nan = ev("prom.error_rate", [{"value": None}])
    findings = evaluate(bundle(CLEAN[0], CLEAN[1], CLEAN[3], nan))
    assert findings[0].category == "healthy"


def test_dependency_that_is_down_is_not_also_called_reachable():
    deployments = ev("k8s.deployments", [{"name": "redis", "desired": 0, "ready": 0}])
    logs = ev("loki.errors", {"signatures": {"redis_unreachable": 5}})
    categories = [f.category for f in evaluate(bundle(*CLEAN[:3], deployments, logs))]
    assert categories == ["dependency_down"]


def test_unreachable_names_only_the_affected_dependency():
    deployments = ev("k8s.deployments", [{"name": "postgres", "desired": 1, "ready": 1}])
    logs = ev("loki.errors", {"signatures": {"db_unreachable": 5}})
    finding = evaluate(bundle(*CLEAN[:3], deployments, logs))[0]
    assert finding.category == "dependency_unreachable"
    assert "Postgres" in finding.title and "Redis" not in finding.title


def test_stable_revision_errors_are_not_called_canary_errors():
    rollouts = ev(
        "k8s.rollouts",
        [{"name": "api", "phase": "Paused", "stable_revision": "s", "current_revision": "c"}],
    )
    key = "rollouts_pod_template_hash"
    errors = ev(
        "prom.errors_by_revision",
        [{"labels": {key: "s"}, "value": 5.0}, {"labels": {key: "c"}, "value": 5.0}],
    )
    requests = ev(
        "prom.requests_by_revision",
        [{"labels": {key: "s"}, "value": 10.0}, {"labels": {key: "c"}, "value": 10.0}],
    )
    findings = evaluate(bundle(*CLEAN[:1], CLEAN[3], rollouts, errors, requests))
    categories = [f.category for f in findings]
    assert "canary_errors" not in categories


def crash_pod(**extra):
    base = {"name": "w", "app": "worker", "phase": "Running", "ready": False}
    return base | {"restarts": 3, "last_exit_code": 1, "waiting_reason": None} | extra


def test_crashloop_is_found_when_the_pod_is_sampled_between_backoffs():
    # Seen on a real cluster: restarts 3, exit code 1, not ready, no waiting state when sampled
    findings = evaluate(bundle(ev("k8s.pods", [crash_pod()]), *CLEAN[1:]))
    assert findings[0].category == "crashloop"


def test_restarted_pod_that_recovered_is_not_a_crashloop():
    pod = crash_pod(ready=True)
    assert evaluate(bundle(ev("k8s.pods", [pod]), *CLEAN[1:]))[0].category == "healthy"


def test_clean_exit_is_not_a_crashloop():
    pod = crash_pod(last_exit_code=0)
    assert evaluate(bundle(ev("k8s.pods", [pod]), *CLEAN[1:]))[0].category == "healthy"


def test_crashing_worker_does_not_outrank_an_api_outage():
    # Seen on a real cluster: the worker crash came first during a 38.6% api error rate
    pods = ev("k8s.pods", [crash_pod()])
    errors = ev("prom.error_rate", [{"labels": {}, "value": 0.386}])
    categories = [f.category for f in evaluate(bundle(pods, CLEAN[1], errors, CLEAN[3]))]
    assert categories == ["errors_unexplained", "crashloop"]


def test_crashing_worker_does_not_hide_an_aborted_canary():
    pods = ev("k8s.pods", [crash_pod()])
    rollouts = ev("k8s.rollouts", [{"name": "api", "phase": "Degraded", "aborted": True}])
    findings = evaluate(bundle(pods, rollouts, CLEAN[2], CLEAN[3]))
    assert findings[0].category == "canary_aborted"


def test_crashing_api_still_ranks_first():
    pods = ev("k8s.pods", [crash_pod(app="api")])
    errors = ev("prom.error_rate", [{"labels": {}, "value": 0.386}])
    assert evaluate(bundle(pods, CLEAN[1], errors, CLEAN[3]))[0].category == "crashloop"
