import json
import re
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import httpx

from .models import Bundle, Evidence, Finding, Report
from .rules import evaluate

ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
DISCORD_LIMIT = 1900


class Deduper:
    """One incident fires several alerts, so only the first starts an investigation."""

    def __init__(self, window_seconds: float, clock: Callable[[], float] = time.monotonic):
        self._window = window_seconds
        self._clock = clock
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()

    def should_run(self, key: str) -> bool:
        with self._lock:
            now = self._clock()
            last = self._seen.get(key)
            if last is not None and now - last < self._window:
                return False
            self._seen[key] = now
            return True


class ReportStore:
    def __init__(self, directory: str):
        self._dir = Path(directory)

    def save(self, report: Report) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        (self._dir / f"{report.id}.json").write_text(report.model_dump_json())

    def load(self, report_id: str) -> Report | None:
        if not ID_PATTERN.match(report_id):  # the id becomes a file name
            return None
        path = self._dir / f"{report_id}.json"
        return Report.model_validate(json.loads(path.read_text())) if path.exists() else None


def render(report: Report) -> str:
    primary: Finding = report.findings[0]
    lines = [
        f"**Investigation: {primary.title}**",
        f"Trigger: {report.bundle.trigger} | Category: {primary.category} | "
        f"Confidence: {primary.confidence}",
        primary.explanation,
    ]
    if primary.unknowns:
        lines.append("Unknown: " + " ".join(primary.unknowns))
    if primary.next_steps:
        lines.append("Next: " + " ".join(primary.next_steps))
    lines.append(f"Evidence: {', '.join(primary.evidence_ids)}")
    if len(report.findings) > 1:
        lines.append("Also: " + ", ".join(f.category for f in report.findings[1:]))
    lines.append(f"Report id: {report.id}. No changes were made.")
    return "\n".join(lines)[:DISCORD_LIMIT]


def notify(client: httpx.Client, webhook_url: str, report: Report) -> None:
    if not webhook_url:
        return
    # allowed_mentions: text derived from logs must not be able to ping anyone
    client.post(
        webhook_url, json={"content": render(report), "allowed_mentions": {"parse": []}}
    ).raise_for_status()


def investigate(
    collect: Callable[[], list[Evidence]], trigger: str, alerts: list[str]
) -> Report:
    bundle = Bundle(trigger=trigger, alerts=alerts, evidence=collect())
    return Report(id=uuid.uuid4().hex, findings=evaluate(bundle), bundle=bundle)
