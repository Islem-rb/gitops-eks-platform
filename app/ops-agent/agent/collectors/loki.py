import re
import time

import httpx

from ..models import Evidence
from ..queries import (
    LOG_LINE_LIMIT,
    LOG_LINE_MAX_CHARS,
    LOG_SAMPLE_LIMIT,
    LOG_WINDOW_SECONDS,
    LOGQL_ERRORS,
)
from . import safely

# Log lines are untrusted input. They are only matched against these patterns and stored truncated.
# Each line gets its first match, so "could not translate host name" counts as DNS, not database.
SIGNATURES = [
    (
        "dns",
        re.compile(
            r"could not translate host name|host not found in upstream"
            r"|temporary failure in name resolution|name or service not known",
            re.I,
        ),
    ),
    ("db_auth", re.compile(r"password authentication failed", re.I)),
    (
        "db_unreachable",
        re.compile(r"PoolTimeout|OperationalError|connection .*5432|psycopg\.\w*error", re.I),
    ),
    ("redis_unreachable", re.compile(r"redis\.exceptions|connecting to redis|:6379", re.I)),
]


def summarize(lines: list[tuple[str, str]]) -> dict:
    signatures: dict[str, int] = {}
    by_app: dict[str, int] = {}
    for app, line in lines:
        by_app[app] = by_app.get(app, 0) + 1
        for name, pattern in SIGNATURES:
            if pattern.search(line):
                signatures[name] = signatures.get(name, 0) + 1
                break
    return {
        "matched_lines": len(lines),
        "by_app": by_app,
        "signatures": signatures,
        "samples": [
            {"app": app, "line": line[:LOG_LINE_MAX_CHARS]}
            for app, line in lines[:LOG_SAMPLE_LIMIT]
        ],
    }


def fetch_errors(client: httpx.Client) -> dict:
    now = time.time_ns()
    response = client.get(
        "/loki/api/v1/query_range",
        params={
            "query": LOGQL_ERRORS,
            "start": now - LOG_WINDOW_SECONDS * 1_000_000_000,
            "end": now,
            "limit": LOG_LINE_LIMIT,
            "direction": "backward",
        },
    )
    response.raise_for_status()
    lines = [
        (stream["stream"].get("app", "unknown"), value[1])
        for stream in response.json()["data"]["result"]
        for value in stream["values"]
    ]
    return summarize(lines)


def collect(client: httpx.Client) -> list[Evidence]:
    return [safely("loki.errors", "loki", lambda: fetch_errors(client))]
