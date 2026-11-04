import httpx

from agent.collectors import kubernetes, loki, prometheus, safely
from agent.queries import LOG_LINE_MAX_CHARS, PROMQL


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


def test_log_signatures():
    lines = [
        ("api", "psycopg_pool.PoolTimeout: couldn't get a connection after 30.00 sec"),
        ("api", 'FATAL: password authentication failed for user "app"'),
        ("api", "redis.exceptions.ConnectionError: Error connecting to redis:6379"),
        ("frontend", 'host not found in upstream "api"'),
        ("api", 'could not translate host name "postgres" to address'),
    ]
    assert loki.summarize(lines)["signatures"] == {
        "db_unreachable": 1,
        "db_auth": 1,
        "redis_unreachable": 1,
        "dns": 2,
    }


def test_log_samples_are_truncated_and_capped():
    summary = loki.summarize([("api", "x" * 5000)] * 100)
    assert summary["matched_lines"] == 100
    assert len(summary["samples"]) == 20
    assert all(len(s["line"]) == LOG_LINE_MAX_CHARS for s in summary["samples"])
