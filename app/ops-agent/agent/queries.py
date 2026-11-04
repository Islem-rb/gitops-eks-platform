"""The only queries the agent can run.

Prometheus and Loki have no authentication in this platform, so this fixed catalog is the access
control: namespace, time window and line limits are set here and never come from an alert or an LLM.
"""

NAMESPACE = "demo"
_API = 'namespace="demo",app="api"'
_API_5XX = f'{_API},status=~"5.."'

PROMQL = {
    "error_rate": (
        f"(sum(rate(http_requests_total{{{_API_5XX}}}[2m])) or vector(0))"
        f" / sum(rate(http_requests_total{{{_API}}}[2m]))"
    ),
    "errors_by_revision": (
        f"sum by (rollouts_pod_template_hash) (rate(http_requests_total{{{_API_5XX}}}[2m]))"
    ),
    "requests_by_revision": (
        f"sum by (rollouts_pod_template_hash) (rate(http_requests_total{{{_API}}}[2m]))"
    ),
    "latency_p95": (
        "histogram_quantile(0.95, sum by (le) "
        f"(rate(http_request_duration_seconds_bucket{{{_API}}}[5m])))"
    ),
    "api_targets_up": f"sum(up{{{_API}}})",
    "worker_error_rate": (
        'sum(rate(worker_jobs_processed_total{namespace="demo",app="worker",result="error"}[5m]))'
    ),
}

LOGQL_ERRORS = (
    '{namespace="demo",app=~"api|worker|frontend"}'
    ' |~ "(?i)(error|exception|timeout|traceback|failed|not found| 5[0-9][0-9] )"'
)
LOGQL_API_ANY = '{namespace="demo",app="api"}'
LOG_WINDOW_SECONDS = 300
RECENT_LOG_SECONDS = 90
LOG_LINE_LIMIT = 200
LOG_SAMPLE_LIMIT = 20
LOG_LINE_MAX_CHARS = 300
