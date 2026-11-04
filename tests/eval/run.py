"""Replay each scenario's evidence through the rules and score the result. No cluster, no LLM."""

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app" / "ops-agent"))

from agent.models import Bundle
from agent.rules import evaluate

SCENARIOS = ROOT / "tests" / "scenarios"


def score(findings, expected) -> list[str]:
    primary = findings[0]
    text = " ".join(
        " ".join([f.title, f.explanation, *f.unknowns, *f.next_steps]) for f in findings
    ).lower()
    problems = []
    if primary.category != expected["category"]:
        problems.append(f"category {primary.category}, expected {expected['category']}")
    for evidence_id in expected.get("required_evidence", []):
        if evidence_id not in primary.evidence_ids:
            problems.append(f"did not cite {evidence_id}")
    for phrase in expected.get("must_mention", []):
        if phrase.lower() not in text:
            problems.append(f"did not say: {phrase!r}")
    for phrase in expected.get("forbidden", []):
        if phrase.lower() in text:
            problems.append(f"forbidden claim: {phrase!r}")
    return problems


def main() -> int:
    passed, failed, counts = 0, 0, {"synthetic": 0, "recorded": 0}
    for directory in sorted(p for p in SCENARIOS.iterdir() if (p / "expected.yaml").exists()):
        fixture_path = directory / "fixture.json"
        if not fixture_path.exists():
            print(f"SKIP  {directory.name}: no fixture yet")
            continue
        fixture = json.loads(fixture_path.read_text())
        counts[fixture["provenance"]] += 1
        findings = evaluate(Bundle.model_validate(fixture["bundle"]))
        problems = score(findings, yaml.safe_load((directory / "expected.yaml").read_text()))
        passed += not problems
        failed += bool(problems)
        result = "PASS" if not problems else "FAIL"
        print(f"{result}  {directory.name} [{fixture['provenance']}] -> {findings[0].category}")
        for problem in problems:
            print(f"      {problem}")
    print(f"\n{passed} passed, {failed} failed. Fixtures: {counts}.")
    if counts["synthetic"]:
        print("Synthetic fixtures check the rules' logic. They are not a measure of real accuracy.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
