"""Write hand-made fixtures for the scenarios. They test the rules, not real accuracy.

record.py replaces a fixture with evidence from a real incident. Never overwrites a recorded one.
"""

import copy
import json
from pathlib import Path

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
STABLE, CANARY = "stable111", "canary222"


def ev(evidence_id, data):
    return {"id": evidence_id, "source": evidence_id.split(".")[0], "data": data}


def pod(name, app, **extra):
    base = {"name": name, "app": app, "phase": "Running", "ready": True, "restarts": 0}
    return base | extra


def scalar(value):
    return [{"labels": {}, "value": value}]


def by_revision(**rates):
    return [
        {"labels": {"rollouts_pod_template_hash": k}, "value": v} for k, v in rates.items()
    ]


def healthy():
    evidence = [
        ev("k8s.pods", [pod("api-1", "api", revision=STABLE), pod("worker-1", "worker"),
                        pod("postgres-1", "postgres"), pod("redis-1", "redis")]),
        ev("k8s.events", []),
        ev("k8s.deployments", [{"name": "postgres", "desired": 1, "ready": 1},
                               {"name": "redis", "desired": 1, "ready": 1},
                               {"name": "worker", "desired": 1, "ready": 1}]),
        ev("k8s.rollouts", [{"name": "api", "phase": "Healthy", "aborted": False,
                             "stable_revision": STABLE, "current_revision": STABLE}]),
        ev("k8s.analysisruns", []),
        ev("k8s.system_pods", [pod("coredns-1", "kube-dns"), pod("coredns-2", "kube-dns")]),
        ev("k8s.nodes", [{"name": "node-1", "ready": True, "pressure": []}]),
        ev("prom.error_rate", scalar(0.0)),
        ev("prom.errors_by_revision", []),
        ev("prom.requests_by_revision", by_revision(**{STABLE: 40.0})),
        ev("prom.latency_p95", scalar(0.02)),
        ev("prom.api_targets_up", scalar(5.0)),
        ev("prom.worker_error_rate", []),
        ev("loki.errors", {"matched_lines": 0, "by_app": {}, "signatures": {}, "samples": []}),
        ev("changes.deploys", {"application": "demo-kind", "deploys": [
            {"revision": "aaaaaaaaaaaa", "deployed_at": "2026-11-01T10:00:00Z",
             "age_seconds": 90000}]}),
        ev("changes.commits", []),
    ]
    return {"trigger": "manual", "alerts": [], "evidence": evidence}


def patch(bundle, evidence_id, data):
    for item in bundle["evidence"]:
        if item["id"] == evidence_id:
            item["data"] = data
            return bundle
    raise KeyError(evidence_id)


def build(*changes, alerts=("ApiHighErrorRate",)):
    bundle = copy.deepcopy(healthy())
    bundle["alerts"] = list(alerts)
    for evidence_id, data in changes:
        patch(bundle, evidence_id, data)
    return bundle


def logs(**signatures):
    return {"matched_lines": sum(signatures.values()), "by_app": {"api": sum(signatures.values())},
            "signatures": signatures, "samples": []}


def deps(postgres=1, redis=1):
    return [{"name": "postgres", "desired": 1, "ready": postgres},
            {"name": "redis", "desired": 1, "ready": redis},
            {"name": "worker", "desired": 1, "ready": 1}]


ERRORS = ("prom.error_rate", scalar(0.4))
EXPECTED = {
    "healthy": (
        {**healthy()},
        {"category": "healthy", "required_evidence": ["k8s.pods", "prom.error_rate"]},
    ),
    "post-promotion-errors": (
        build(ERRORS),
        {"category": "errors_unexplained", "required_evidence": ["prom.error_rate"],
         "forbidden": ["caused by"]},
    ),
    "errors-after-release": (
        build(ERRORS, ("changes.deploys", {"deploys": [
            {"revision": "bbbbbbbbbbbb", "deployed_at": "2026-11-04T10:00:00Z",
             "age_seconds": 300}]})),
        {"category": "errors_after_release",
         "required_evidence": ["prom.error_rate", "changes.deploys"],
         "forbidden": ["caused by"]},
    ),
    "db-credential": (
        build(ERRORS, ("loki.errors", logs(db_auth=30)),
              ("k8s.pods", [pod("api-1", "api", ready=False, phase="Running", restarts=4,
                                waiting_reason="CrashLoopBackOff", last_exit_code=3)])),
        {"category": "db_auth_failure", "required_evidence": ["loki.errors"]},
    ),
    "redis-down": (
        build(ERRORS, ("k8s.deployments", deps(redis=0)),
              ("loki.errors", logs(redis_unreachable=20))),
        {"category": "dependency_down", "required_evidence": ["k8s.deployments"],
         "forbidden": ["reports ready"]},
    ),
    "network-block": (
        build(ERRORS, ("loki.errors", logs(db_unreachable=25))),
        {"category": "dependency_unreachable", "required_evidence": ["loki.errors"],
         "forbidden": ["caused by"]},
    ),
    "crashloop": (
        build(("k8s.pods", [pod("worker-1", "worker", ready=False, restarts=6,
                                waiting_reason="CrashLoopBackOff", last_exit_code=1,
                                last_termination_reason="Error")])),
        {"category": "crashloop", "required_evidence": ["k8s.pods"]},
    ),
    "pending": (
        build(("k8s.pods", [pod("worker-2", "worker", phase="Pending", ready=False)]),
              ("k8s.events", [{"reason": "FailedScheduling", "object": "Pod/worker-2",
                               "message": "0/1 nodes are available: 1 Insufficient cpu."}])),
        {"category": "pod_pending", "required_evidence": ["k8s.pods", "k8s.events"]},
    ),
    "canary-abort": (
        build(("k8s.rollouts", [{"name": "api", "phase": "Degraded", "aborted": True,
                                 "stable_revision": STABLE, "current_revision": CANARY}]),
              ("k8s.analysisruns", [{"name": "api-run", "phase": "Failed", "metrics": [
                  {"name": "error-rate", "phase": "Failed", "last_value": "[0.41]"}]}])),
        {"category": "canary_aborted", "required_evidence": ["k8s.rollouts", "k8s.analysisruns"]},
    ),
    "canary-in-progress": (
        build(("prom.error_rate", scalar(0.1)),
              ("k8s.rollouts", [{"name": "api", "phase": "Paused", "aborted": False,
                                 "stable_revision": STABLE, "current_revision": CANARY}]),
              ("prom.errors_by_revision", by_revision(**{CANARY: 5.0})),
              ("prom.requests_by_revision", by_revision(**{STABLE: 40.0, CANARY: 10.0}))),
        {"category": "canary_errors", "required_evidence": ["prom.errors_by_revision"]},
    ),
    "dns-after-restore": (
        build(ERRORS, ("loki.errors", logs(dns=40)),
              ("k8s.system_pods", [pod("coredns-1", "kube-dns"), pod("coredns-2", "kube-dns"),
                                   pod("aws-node-1", "aws-node")])),
        {"category": "dns_failure", "required_evidence": ["loki.errors", "k8s.system_pods"],
         "must_mention": ["root cause is not established"],
         "forbidden": ["caused by", "root cause is the"]},
    ),
}


def yaml_value(value):
    return json.dumps(value)  # JSON values are valid YAML


def main():
    for name, (bundle, expected) in EXPECTED.items():
        directory = SCENARIOS / name
        directory.mkdir(parents=True, exist_ok=True)
        fixture = directory / "fixture.json"
        if fixture.exists() and json.loads(fixture.read_text())["provenance"] == "recorded":
            print(f"keeping recorded fixture: {name}")
        else:
            payload = {"provenance": "synthetic", "bundle": bundle}
            fixture.write_text(json.dumps(payload, indent=1) + "\n")
        lines = [f"{key}: {yaml_value(value)}" for key, value in expected.items()]
        (directory / "expected.yaml").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
