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
ALERT_MEMORY_SECONDS = 86400
# A symptom can sharpen into a cause once logs arrive, so these are worth a second look
INCONCLUSIVE = {"unknown", "errors_unexplained", "crashloop"}
# These look back hours and keep firing long after a fault, so they can't say an incident is open
LONG_WINDOW_ALERTS = {"ApiErrorBudgetFastBurn", "ApiErrorBudgetSlowBurn"}


class IncidentTracker:
    """Decides what is worth repeating. Alertmanager resends firing alerts, and one incident fires
    several, so an investigation runs per new alert and a notice goes out per new finding. Once
    every short-window alert has resolved the incident is over, so the next fault is reported. The
    window remains as a backstop for repeats, in case a resolved event is lost."""

    def __init__(self, window_seconds: float, clock: Callable[[], float] = time.monotonic):
        self._window = window_seconds
        self._clock = clock
        self._alerts: dict[tuple[str, str], float] = {}
        self._findings: dict[str, float] = {}
        self._active: set[str] = set()
        self._lock = threading.Lock()

    def is_new_alert(self, fingerprint: str, starts_at: str, name: str = "") -> bool:
        key = (fingerprint, starts_at)
        with self._lock:
            now = self._clock()
            self._alerts = {
                k: seen for k, seen in self._alerts.items() if now - seen < ALERT_MEMORY_SECONDS
            }
            is_new = key not in self._alerts
            self._alerts[key] = now
            if name not in LONG_WINDOW_ALERTS:
                self._active.add(fingerprint)
            return is_new

    def alert_resolved(self, fingerprint: str) -> None:
        with self._lock:
            self._active.discard(fingerprint)
            if not self._active:
                self._findings.clear()

    def should_notify(self, category: str) -> bool:
        with self._lock:
            now = self._clock()
            last = self._findings.get(category)
            if self._active:  # with none, the incident ended during this run, so don't remember it
                self._findings[category] = now
            return last is None or now - last >= self._window


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


def _changes_line(report: Report) -> str:
    if report.revert.pr_url:
        return f"Proposed revert, needs your review: {report.revert.pr_url} Nothing else changed."
    return "No changes were made."


def _render_analysis(report: Report, primary: Finding) -> str:
    """The rules lead, because the model can contradict them. Its notes are cut first if long."""
    analysis = report.analysis
    footer = (
        f"AI notes can be wrong; check the cited evidence. Report id: {report.id}. "
        f"{_changes_line(report)}"
    )
    lines = [
        f"**{primary.title}** ({primary.category}, {primary.confidence} confidence)",
        f"Trigger: {report.bundle.trigger}",
        primary.explanation,
        "**AI notes**",
        analysis.summary,
    ]
    for hypothesis in analysis.hypotheses[:3]:
        lines.append(
            f"- ({hypothesis.confidence}) {hypothesis.cause} "
            f"[{', '.join(hypothesis.evidence_ids)}]"
        )
    if analysis.unknowns:
        lines.append("Unknown: " + " ".join(analysis.unknowns))
    if analysis.next_steps:
        lines.append("Next: " + " ".join(analysis.next_steps))
    return "\n".join(lines)[: DISCORD_LIMIT - len(footer) - 1] + "\n" + footer


def render(report: Report) -> str:
    primary: Finding = report.findings[0]
    if report.analysis:
        return _render_analysis(report, primary)
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
    footer = f"Report id: {report.id}. {_changes_line(report)}"
    return "\n".join(lines)[: DISCORD_LIMIT - len(footer) - 1] + "\n" + footer


def notify(client: httpx.Client, webhook_url: str, report: Report) -> None:
    if not webhook_url:
        return
    # allowed_mentions: text derived from logs must not be able to ping anyone
    client.post(
        webhook_url, json={"content": render(report), "allowed_mentions": {"parse": []}}
    ).raise_for_status()


def investigate(collect: Callable[[], list[Evidence]], trigger: str, alerts: list[str]) -> Report:
    bundle = Bundle(trigger=trigger, alerts=alerts, evidence=collect())
    return Report(id=uuid.uuid4().hex, findings=evaluate(bundle), bundle=bundle)


def investigate_alert(
    collect: Callable[[], list[Evidence]],
    trigger: str,
    alerts: list[str],
    settle_seconds: float,
    recheck_seconds: float,
    max_passes: int,
    sleep: Callable[[float], None] = time.sleep,
) -> Report:
    """Let logs and metrics catch up with the alert, then recheck while it is inconclusive."""
    sleep(settle_seconds)
    report = investigate(collect, trigger, alerts)
    passes = 1
    while report.findings[0].category in INCONCLUSIVE and passes < max_passes:
        sleep(recheck_seconds)
        report = investigate(collect, trigger, alerts)
        passes += 1
    report.passes = passes
    return report
