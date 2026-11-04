"""The only queries the agent can run.

Prometheus and Loki have no authentication in this platform, so this fixed catalog is the access
control: namespace, time window and line limits are set here and never come from an alert or an LLM.
"""

NAMESPACE = "demo"
RECENT_SECONDS = 45  # what counts as "now" for logs and the short error rate
_API = 'namespace="demo",app="api"'
_API_5XX = f'{_API},status=~"5.."'

PROMQL = {
    "error_rate": (
        f"(sum(rate(http_requests_total{{{_API_5XX}}}[2m])) or vector(0))"
        f" / sum(rate(http_requests_total{{{_API}}}[2m]))"
    ),
    # Same window as the recent log check. A 2-minute rate still shows a fault that was just fixed.
    "error_rate_recent": (
        f"(sum(rate(http_requests_total{{{_API_5XX}}}[{RECENT_SECONDS}s])) or vector(0))"
        f" / sum(rate(http_requests_total{{{_API}}}[{RECENT_SECONDS}s]))"
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
# Known causes, first match wins, so "could not translate host name" counts as DNS, not database.
# Access logs of failed requests are far more common than these and would crowd them out of
# LOGQL_ERRORS, so they get their own query.
SIGNATURE_PATTERNS = {
    "dns": (
        r"could not translate host name|host not found in upstream"
        r"|temporary failure in name resolution|name or service not known"
    ),
    "db_auth": r"password authentication failed",
    "db_unreachable": r"PoolTimeout|OperationalError|connection .*5432|psycopg\.\w*error",
    "redis_unreachable": r"redis\.exceptions|connecting to redis|:6379",
}
LOGQL_SIGNATURES = (
    '{namespace="demo",app=~"api|worker|frontend"} |~ `(?i)('
    + "|".join(SIGNATURE_PATTERNS.values())
    + ")`"
)
LOGQL_API_ANY = '{namespace="demo",app="api"}'
LOG_WINDOW_SECONDS = 300
# Shorter than the wait before an alert's first look, so lines from before a fix aren't "recent"
LOG_LINE_LIMIT = 200
SIGNATURE_LINE_LIMIT = 500
LOG_SAMPLE_LIMIT = 20
LOG_LINE_MAX_CHARS = 300
