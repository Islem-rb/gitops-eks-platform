import re
import time

import httpx

from ..models import Evidence
from ..queries import (
    LOG_LINE_LIMIT,
    LOG_LINE_MAX_CHARS,
    LOG_SAMPLE_LIMIT,
    LOG_WINDOW_SECONDS,
    LOGQL_API_ANY,
    LOGQL_ERRORS,
    LOGQL_SIGNATURES,
    RECENT_SECONDS,
    SIGNATURE_LINE_LIMIT,
    SIGNATURE_PATTERNS,
)
from . import safely

# Log lines are untrusted input. They are only matched against these patterns and stored truncated.
SIGNATURES = [(name, re.compile(pattern, re.I)) for name, pattern in SIGNATURE_PATTERNS.items()]


def _signature(line: str) -> str | None:
    return next((name for name, pattern in SIGNATURES if pattern.search(line)), None)


def summarize(
    lines: list[tuple[str, str, int]], cause_lines: list[tuple[str, str, int]], now_ns: int
) -> dict:
    """Lines are (app, text, timestamp in ns). cause_lines come from the signature query."""
    signatures: dict[str, int] = {}
    recent: dict[str, int] = {}
    by_app: dict[str, int] = {}
    recent_after = now_ns - RECENT_SECONDS * 1_000_000_000
    for app, _, _ in lines:
        by_app[app] = by_app.get(app, 0) + 1
    for _, line, timestamp in cause_lines:
        name = _signature(line)
        if name:
            signatures[name] = signatures.get(name, 0) + 1
            if timestamp >= recent_after:
                recent[name] = recent.get(name, 0) + 1
    return {
        "matched_lines": len(lines),
        "by_app": by_app,
        "signatures": signatures,
        "recent_signatures": recent,
        "samples": [  # the lines that name a cause say more than repeated access logs
            {"app": app, "line": line[:LOG_LINE_MAX_CHARS]}
            for app, line, _ in (cause_lines + lines)[:LOG_SAMPLE_LIMIT]
        ],
    }


class NoLogsInWindow(Exception):
    """The api logs every probe, so silence means the log pipeline is broken, not a healthy app."""


def _query(client: httpx.Client, query: str, limit: int) -> list[tuple[str, str, int]]:
    now = time.time_ns()
    response = client.get(
        "/loki/api/v1/query_range",
        params={
            "query": query,
            "start": now - LOG_WINDOW_SECONDS * 1_000_000_000,
            "end": now,
            "limit": limit,
            "direction": "backward",
        },
    )
    response.raise_for_status()
    return [
        (stream["stream"].get("app", "unknown"), value[1], int(value[0]))
        for stream in response.json()["data"]["result"]
        for value in stream["values"]
    ]


def fetch_errors(client: httpx.Client) -> dict:
    if not _query(client, LOGQL_API_ANY, 1):
        raise NoLogsInWindow
    return summarize(
        _query(client, LOGQL_ERRORS, LOG_LINE_LIMIT),
        _query(client, LOGQL_SIGNATURES, SIGNATURE_LINE_LIMIT),
        time.time_ns(),
    )


def collect(client: httpx.Client) -> list[Evidence]:
    return [safely("loki.errors", "loki", lambda: fetch_errors(client))]
