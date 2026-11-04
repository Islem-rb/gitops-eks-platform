"""Deterministic diagnosis. A pure function of the evidence bundle, so it can be replayed in CI.

Rules are tried in order and every match is kept; the first finding is the primary one. Rules only
state what the evidence supports. When it does not explain the problem, the answer is "unknown".
"""

from collections.abc import Callable

from .models import Bundle, Finding

ERROR_RATE_THRESHOLD = 0.05  # same as the ApiHighErrorRate alert and the canary analysis
P95_THRESHOLD_SECONDS = 0.5  # same as the ApiHighLatencyP95 alert
RECENT_DEPLOY_SECONDS = 1800  # the runbook's "release in the last 30 min"
DEPENDENCIES = ("postgres", "redis")
CRASH_RESTARTS = 2
SERVING_APPS = ("api", "frontend")

ROLLBACK = "Roll back through Git: git revert the commit, then let Argo CD sync."
RUNBOOK = "Follow docs/runbooks/api-high-error-rate.md."


def _scalar(bundle: Bundle, evidence_id: str) -> float | None:
    series = bundle.data(evidence_id, [])
    return series[0]["value"] if series else None


def _serving_pods_unready(bundle: Bundle) -> bool:
    return any(p.get("app") in SERVING_APPS and not p.get("ready") for p in _pods(bundle))


def _signatures(bundle: Bundle) -> dict:
    """Log failures that describe now. Crash-looping pods log rarely, so unready pods count too."""
    logs = bundle.data("loki.errors", {})
    if "recent_signatures" not in logs or _serving_pods_unready(bundle):
        return logs.get("signatures", {})
    return logs["recent_signatures"]


def _pods(bundle: Bundle) -> list[dict]:
    return bundle.data("k8s.pods", [])


def _rollout(bundle: Bundle) -> dict | None:
    return next((r for r in bundle.data("k8s.rollouts", []) if r["name"] == "api"), None)


def _error_rate(bundle: Bundle) -> float | None:
    return _scalar(bundle, "prom.error_rate")


def _errors_high(bundle: Bundle) -> bool:
    rate = _error_rate(bundle)
    return rate is not None and rate >= ERROR_RATE_THRESHOLD


def _deps_not_ready(bundle: Bundle) -> list[str]:
    return [
        d["name"]
        for d in bundle.data("k8s.deployments", [])
        if d["name"] in DEPENDENCIES and d["ready"] == 0
    ]


def _revision_rates(bundle: Bundle) -> dict[str, float]:
    key = "rollouts_pod_template_hash"
    errors = {
        s["labels"].get(key): s["value"] or 0 for s in bundle.data("prom.errors_by_revision", [])
    }
    rates = {}
    for series in bundle.data("prom.requests_by_revision", []):
        revision, total = series["labels"].get(key), series["value"]
        if revision and total:
            rates[revision] = errors.get(revision, 0) / total
    return rates


def dns_failure(bundle: Bundle) -> Finding | None:
    count = _signatures(bundle).get("dns", 0)
    if not count:
        return None
    system = bundle.data("k8s.system_pods", [])
    coredns = [p for p in system if p["app"] == "kube-dns"]
    ready = sum(p["ready"] for p in coredns)
    uses_vpc_cni = any(p["app"] == "aws-node" for p in system)
    steps = ["Test name resolution from a pod in the namespace, using the real answer in the "
             "output and not the nslookup exit code."]
    if uses_vpc_cni:
        steps.append(
            "ADR 0004 documents this after a namespace restore on EKS. Restarting the aws-node "
            "pod on the affected node is a known mitigation."
        )
    return Finding(
        category="dns_failure",
        title="Pods are failing to resolve DNS",
        explanation=f"{count} log lines show name-resolution failures. "
        f"CoreDNS pods ready: {ready}/{len(coredns)}.",
        confidence="medium",
        evidence_ids=["loki.errors", "k8s.system_pods"],
        unknowns=["Why resolution fails. The root cause is not established by this evidence."],
        next_steps=steps,
    )


def db_auth_failure(bundle: Bundle) -> Finding | None:
    count = _signatures(bundle).get("db_auth", 0)
    if not count:
        return None
    return Finding(
        category="db_auth_failure",
        title="The database is rejecting the application's credentials",
        explanation=f"{count} log lines show password authentication failures.",
        confidence="high",
        evidence_ids=["loki.errors", "k8s.pods"],
        unknowns=["Whether the Secret or the database password is the one that changed."],
        next_steps=["Compare the app-db Secret source with the credentials Postgres expects."],
    )


def dependency_down(bundle: Bundle) -> Finding | None:
    down = _deps_not_ready(bundle)
    if not down:
        return None
    return Finding(
        category="dependency_down",
        title=f"Dependency not ready: {', '.join(down)}",
        explanation=f"Deployment(s) {', '.join(down)} have no ready replicas.",
        confidence="high",
        evidence_ids=["k8s.deployments", "k8s.pods", "k8s.events"],
        unknowns=["Why the dependency has no ready replicas, if events do not show it."],
        next_steps=[f"Inspect the {down[0]} pods, events and replica count."],
    )


def dependency_unreachable(bundle: Bundle) -> Finding | None:
    signatures = _signatures(bundle)
    down = _deps_not_ready(bundle)
    candidates = {"db_unreachable": "postgres", "redis_unreachable": "redis"}
    names = [n for n, dep in candidates.items() if signatures.get(n) and dep not in down]
    if not names:
        return None
    target = " and ".join("Postgres" if n == "db_unreachable" else "Redis" for n in names)
    return Finding(
        category="dependency_unreachable",
        title=f"The application cannot reach {target}, which reports ready",
        explanation=f"Logs show connection failures to {target} while its pods are ready, "
        "which points to the network path or connection handling, not the dependency.",
        confidence="medium",
        evidence_ids=["loki.errors", "k8s.deployments"],
        unknowns=["Whether a NetworkPolicy, the node network or connection pooling is the cause."],
        next_steps=["Check NetworkPolicies and recent changes under gitops/, then test the "
                    "connection from an api pod."],
    )


def recovered(bundle: Bundle) -> Finding | None:
    """Failures were logged but have stopped and the pods are ready: an alert arrived late."""
    logs = bundle.data("loki.errors", {})
    if "recent_signatures" not in logs or _serving_pods_unready(bundle) or _deps_not_ready(bundle):
        return None
    older = sorted(set(logs.get("signatures", {})) - set(logs["recent_signatures"]))
    if not older:
        return None
    return Finding(
        category="recovered",
        title="Failures in the logs have stopped",
        explanation=f"Logged in the last few minutes but not recently: {', '.join(older)}. "
        "The serving pods and dependencies are ready now.",
        confidence="medium",
        evidence_ids=["loki.errors", "k8s.pods", "k8s.deployments"],
        unknowns=["What caused the failures and whether they will return."],
        next_steps=["Confirm the error rate is falling, then review recent changes."],
    )


def _pods_waiting(bundle: Bundle, reasons: set[str]) -> list[dict]:
    return [p for p in _pods(bundle) if p.get("waiting_reason") in reasons]


def image_pull_failure(bundle: Bundle) -> Finding | None:
    pods = _pods_waiting(bundle, {"ImagePullBackOff", "ErrImagePull"})
    if not pods:
        return None
    return Finding(
        category="image_pull_failure",
        title=f"Image cannot be pulled for {pods[0]['app']}",
        explanation=f"{len(pods)} pod(s) are waiting on an image pull.",
        confidence="high",
        evidence_ids=["k8s.pods", "k8s.events"],
        next_steps=["Check the image tag in the overlay exists in the registry."],
    )


def _crashing(bundle: Bundle) -> list[dict]:
    # A container that crashes quickly is not always in the waiting state when it is sampled
    return [
        p
        for p in _pods(bundle)
        if p.get("waiting_reason") == "CrashLoopBackOff"
        or (
            not p.get("ready")
            and p.get("restarts", 0) >= CRASH_RESTARTS
            and p.get("last_exit_code") not in (None, 0)
        )
    ]


def _crashloop_finding(pods: list[dict]) -> Finding | None:
    if not pods:
        return None
    pod = pods[0]
    return Finding(
        category="crashloop",
        title=f"{pod['app']} is crash looping",
        explanation=f"{len(pods)} pod(s) crash looping. {pod['name']} has restarted "
        f"{pod['restarts']} times; last termination {pod.get('last_termination_reason')}, "
        f"exit code {pod.get('last_exit_code')}.",
        confidence="high",
        evidence_ids=["k8s.pods", "k8s.events", "changes.deploys"],
        unknowns=["Why the process exits, unless the logs show it."],
        next_steps=[f"Read the previous container logs for {pod['name']}."],
    )


def crashloop_serving(bundle: Bundle) -> Finding | None:
    return _crashloop_finding([p for p in _crashing(bundle) if p.get("app") in SERVING_APPS])


def crashloop_other(bundle: Bundle) -> Finding | None:
    """Ranks last: a crashing background pod must not hide a problem users can see."""
    return _crashloop_finding([p for p in _crashing(bundle) if p.get("app") not in SERVING_APPS])


def pending_pods(bundle: Bundle) -> Finding | None:
    pods = [p for p in _pods(bundle) if p.get("phase") == "Pending" and not p.get("waiting_reason")]
    if not pods:
        return None
    events = [e for e in bundle.data("k8s.events", []) if e.get("reason") == "FailedScheduling"]
    detail = events[0]["message"] if events else "No scheduling event was found."
    return Finding(
        category="pod_pending",
        title=f"{pods[0]['app']} cannot be scheduled",
        explanation=f"{len(pods)} pod(s) are Pending. {detail}",
        confidence="high" if events else "medium",
        evidence_ids=["k8s.pods", "k8s.events", "k8s.nodes"],
        next_steps=["Compare the pod's requests with node capacity and volume constraints."],
    )


def canary_aborted(bundle: Bundle) -> Finding | None:
    rollout = _rollout(bundle)
    if not rollout or not (rollout.get("aborted") or rollout.get("phase") == "Degraded"):
        return None
    runs = bundle.data("k8s.analysisruns", [])
    failed = next((r for r in runs if r.get("phase") in ("Failed", "Error")), None)
    value = None
    if failed and failed.get("metrics"):
        value = failed["metrics"][0].get("last_value")
    return Finding(
        category="canary_aborted",
        title="The api canary was aborted; the stable revision keeps serving",
        explanation="The Rollout is aborted or Degraded."
        + (f" AnalysisRun {failed['name']} failed, last value {value}." if failed else ""),
        confidence="high" if failed else "medium",
        evidence_ids=["k8s.rollouts", "k8s.analysisruns", "changes.deploys", "changes.commits"],
        unknowns=["What in the release caused the failures."],
        next_steps=["Follow docs/runbooks/canary-aborted.md: read the canary logs, then fix "
                    "forward or git revert the release commit."],
    )


def canary_errors(bundle: Bundle) -> Finding | None:
    rollout = _rollout(bundle)
    if not rollout or rollout.get("phase") == "Healthy":
        return None
    stable = rollout.get("stable_revision")
    rates = _revision_rates(bundle)
    canary = {r: v for r, v in rates.items() if r != stable and v >= ERROR_RATE_THRESHOLD}
    if not canary or rates.get(stable, 0) >= ERROR_RATE_THRESHOLD:
        return None
    revision, value = next(iter(canary.items()))
    return Finding(
        category="canary_errors",
        title="Errors are limited to the canary revision",
        explanation=f"Revision {revision} has a {value:.1%} error rate; stable {stable} does not.",
        confidence="high",
        evidence_ids=["prom.errors_by_revision", "prom.requests_by_revision", "k8s.rollouts"],
        next_steps=["Wait for the AnalysisRun to abort it and confirm the status is Degraded."],
    )


def errors_after_release(bundle: Bundle) -> Finding | None:
    if not _errors_high(bundle):
        return None
    deploys = bundle.data("changes.deploys", {}).get("deploys", [])
    recent = next(
        (
            d
            for d in deploys
            if d.get("age_seconds") is not None and d["age_seconds"] <= RECENT_DEPLOY_SECONDS
        ),
        None,
    )
    if not recent:
        return None
    return Finding(
        category="errors_after_release",
        title="API errors began around a recent deployment",
        explanation=f"The error rate is {_error_rate(bundle):.1%} and revision "
        f"{str(recent['revision'])[:12]} deployed {recent['age_seconds']} s ago. "
        "The timing is a correlation, not proof.",
        confidence="medium",
        evidence_ids=["prom.error_rate", "changes.deploys", "changes.commits"],
        unknowns=["Whether the deployment caused the errors."],
        next_steps=[RUNBOOK, ROLLBACK],
    )


def errors_unexplained(bundle: Bundle) -> Finding | None:
    if not _errors_high(bundle):
        return None
    return Finding(
        category="errors_unexplained",
        title="API errors with no cause in the evidence",
        explanation=f"The error rate is {_error_rate(bundle):.1%}. There is no recent deployment, "
        "unready dependency or known log signature to explain it.",
        confidence="low",
        evidence_ids=["prom.error_rate", "k8s.pods", "changes.deploys", "loki.errors"],
        unknowns=["The cause."],
        next_steps=[RUNBOOK],
    )


def latency_high(bundle: Bundle) -> Finding | None:
    p95 = _scalar(bundle, "prom.latency_p95")
    if p95 is None or p95 <= P95_THRESHOLD_SECONDS:
        return None
    return Finding(
        category="latency_high",
        title="API latency is above its threshold",
        explanation=f"p95 latency is {p95 * 1000:.0f} ms against a "
        f"{P95_THRESHOLD_SECONDS * 1000:.0f} ms threshold.",
        confidence="medium",
        evidence_ids=["prom.latency_p95", "k8s.nodes"],
        unknowns=["The cause."],
        next_steps=["Check dependency health and resource use on the node."],
    )


RULES: list[Callable[[Bundle], Finding | None]] = [
    dns_failure,
    db_auth_failure,
    dependency_down,
    dependency_unreachable,
    recovered,
    image_pull_failure,
    crashloop_serving,
    pending_pods,
    canary_aborted,
    canary_errors,
    errors_after_release,
    errors_unexplained,
    latency_high,
    crashloop_other,
]


def evaluate(bundle: Bundle) -> list[Finding]:
    findings = [f for f in (rule(bundle) for rule in RULES) if f]
    failed = [e.id for e in bundle.evidence if not e.ok]

    if not findings:
        required = ("k8s.pods", "k8s.rollouts", "prom.error_rate", "loki.errors")
        missing = [i for i in required if bundle.get(i) is None or not bundle.get(i).ok]
        if missing or bundle.alerts:
            reason = (
                f"Required evidence is unavailable: {', '.join(missing)}."
                if missing
                else f"Alert(s) {', '.join(bundle.alerts)} fired, but no rule matched."
            )
            findings = [
                Finding(
                    category="unknown",
                    title="The evidence does not explain this",
                    explanation=reason,
                    confidence="low",
                    evidence_ids=[e.id for e in bundle.evidence if e.ok],
                    next_steps=[RUNBOOK],
                )
            ]
        else:
            findings = [
                Finding(
                    category="healthy",
                    title="No problem found",
                    explanation="No rule matched and the required evidence was collected.",
                    confidence="high",
                    evidence_ids=list(required),
                )
            ]

    if failed:
        note = f"These collections failed, so this result may be incomplete: {', '.join(failed)}."
        for finding in findings:
            finding.unknowns.append(note)
    return findings
