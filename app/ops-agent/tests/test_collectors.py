import httpx

from agent.collectors import kubernetes, loki, prometheus, safely
from agent.queries import (
    LOG_LINE_MAX_CHARS,
    LOGQL_SIGNATURES,
    PROMQL,
    RECENT_SECONDS,
    SIGNATURE_PATTERNS,
)


def client(handler):
    return httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler))


def test_kube_reader_exposes_no_write_method():
    reader = kubernetes.KubeReader(client(lambda r: httpx.Response(200, json={})))
    public = {name for name in dir(reader) if not name.startswith("_")}
    assert public == {"get", "in_cluster"}


def test_pod_fields_including_crash_details():
    body = {
        "items": [
            {
                "metadata": {
                    "name": "api-1",
                    "labels": {"app": "api", "rollouts-pod-template-hash": "abc"},
                },
                "spec": {"nodeName": "n1"},
                "status": {
                    "phase": "Running",
                    "containerStatuses": [
                        {
                            "ready": False,
                            "restartCount": 4,
                            "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                            "lastState": {"terminated": {"reason": "OOMKilled", "exitCode": 137}},
                        }
                    ],
                },
            }
        ]
    }
    reader = kubernetes.KubeReader(client(lambda r: httpx.Response(200, json=body)))
    pod = kubernetes.pods(reader)[0]
    assert pod["revision"] == "abc"
    assert pod["ready"] is False
    assert pod["restarts"] == 4
    assert pod["waiting_reason"] == "CrashLoopBackOff"
    assert pod["last_termination_reason"] == "OOMKilled"


def test_unscheduled_pod_has_no_container_statuses():
    body = {"items": [{"metadata": {"name": "w"}, "status": {"phase": "Pending"}}]}
    reader = kubernetes.KubeReader(client(lambda r: httpx.Response(200, json=body)))
    pod = kubernetes.pods(reader)[0]
    assert pod["ready"] is False
    assert pod["waiting_reason"] is None


def test_prometheus_nan_becomes_none():
    body = {"data": {"result": [{"metric": {}, "value": [0, "NaN"]}]}}
    result = prometheus.run_query(client(lambda r: httpx.Response(200, json=body)), "error_rate")
    assert result == [{"labels": {}, "value": None}]


def test_prometheus_only_sends_catalog_queries():
    sent = []

    def handler(request):
        sent.append(request.url.params["query"])
        return httpx.Response(200, json={"data": {"result": []}})

    prometheus.collect(client(handler))
    assert sorted(sent) == sorted(PROMQL.values())
    assert all('namespace="demo"' in query for query in sent)


def test_failed_collection_is_recorded_not_raised():
    def boom():
        raise httpx.ConnectError("down")

    item = safely("prom.x", "prometheus", boom)
    assert item.ok is False
    assert item.error == "ConnectError"


def test_http_status_is_kept_so_missing_rbac_is_visible():
    reader = kubernetes.KubeReader(client(lambda r: httpx.Response(403, json={})))
    item = safely("k8s.pods", "kubernetes", lambda: kubernetes.pods(reader))
    assert item.error == "HTTP 403"


NOW = 1_000 * 1_000_000_000


ALERT_SETTLE_SECONDS = 60  # the default in main.py


def at(seconds_ago):
    return NOW - seconds_ago * 1_000_000_000


def test_log_signatures():
    lines = [
        ("api", "psycopg_pool.PoolTimeout: couldn't get a connection after 30.00 sec"),
        ("api", 'FATAL: password authentication failed for user "app"'),
        ("api", "redis.exceptions.ConnectionError: Error connecting to redis:6379"),
        ("frontend", 'host not found in upstream "api"'),
        ("api", 'could not translate host name "postgres" to address'),
    ]
    summary = loki.summarize([], [(app, line, at(10)) for app, line in lines], NOW)
    assert summary["signatures"] == {
        "db_unreachable": 1,
        "db_auth": 1,
        "redis_unreachable": 1,
        "dns": 2,
    }


def test_only_recent_lines_count_as_recent():
    lines = [("api", "PoolTimeout", at(30)), ("api", "PoolTimeout", at(200))]
    lines.append(("api", "redis.exceptions.ConnectionError", at(200)))
    summary = loki.summarize([], lines, NOW)
    assert summary["signatures"] == {"db_unreachable": 2, "redis_unreachable": 1}
    assert summary["recent_signatures"] == {"db_unreachable": 1}


def test_lines_from_before_a_fix_are_not_recent_at_the_first_look():
    # Seen on a real cluster: 60 s after a fix, one stale line was reported as a live network fault
    assert RECENT_SECONDS < ALERT_SETTLE_SECONDS


def test_access_logs_cannot_crowd_out_the_cause():
    # Seen on a real cluster: 200 lines of 500s left one Redis line during a Redis outage
    noise = [("api", f'"GET /jobs" 500 Internal Server Error {i}', at(1)) for i in range(200)]
    causes = [("api", "redis.exceptions.ConnectionError", at(2))] * 50
    summary = loki.summarize(noise, causes, NOW)
    assert summary["signatures"] == {"redis_unreachable": 50}
    assert summary["matched_lines"] == 200
    assert all("redis" in sample["line"] for sample in summary["samples"])


def test_signature_query_is_catalog_text_covering_every_pattern():
    assert LOGQL_SIGNATURES.count("`") == 2  # a backtick in a pattern would end the LogQL string
    for pattern in SIGNATURE_PATTERNS.values():
        assert pattern in LOGQL_SIGNATURES


def test_log_samples_are_truncated_and_capped():
    summary = loki.summarize([("api", "x" * 5000, at(1))] * 100, [], NOW)
    assert summary["matched_lines"] == 100
    assert len(summary["samples"]) == 20
    assert all(len(s["line"]) == LOG_LINE_MAX_CHARS for s in summary["samples"])


def test_no_api_logs_at_all_means_the_pipeline_failed():
    empty = {"data": {"result": []}}
    logs = client(lambda r: httpx.Response(200, json=empty))
    item = safely("loki.errors", "loki", lambda: loki.fetch_errors(logs))
    assert item.ok is False
    assert item.error == "NoLogsInWindow"


def test_api_logs_with_no_errors_is_a_clean_result():
    def handler(request):
        if "|~" in request.url.params["query"]:
            return httpx.Response(200, json={"data": {"result": []}})
        stream = {"stream": {"app": "api"}, "values": [["1", "GET /healthz 200"]]}
        return httpx.Response(200, json={"data": {"result": [stream]}})

    assert loki.fetch_errors(client(handler))["matched_lines"] == 0
