"""Replay scenarios through a real LLM and compare it with the rules.

    python tests/eval/run_llm.py --dry-run           # sizes and estimated cost, no calls
    python tests/eval/run_llm.py --provider ollama --runs 3 --out reports.json   # free and local
    ANTHROPIC_API_KEY=... python tests/eval/run_llm.py --runs 3 --budget-usd 3   # spends money

Output varies between runs, so each scenario runs several times and reports a pass count.
"""

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app" / "ops-agent"))

from agent import llm
from agent.models import Bundle, LLMAnalysis
from agent.rules import evaluate

SCENARIOS = ROOT / "tests" / "scenarios"
RUNBOOK = (ROOT / "docs" / "runbooks" / "api-high-error-rate.md").read_text()
OUTPUT_TOKENS_ESTIMATE = 1500
CONFIDENCE = {"low": 1, "medium": 2, "high": 3}
MAX_CITATIONS = 5  # citing nearly everything would pass the id check while saying nothing
MAX_HYPOTHESES = 3


def score(outcome, expected) -> list[str]:
    if outcome.status != "ok":
        return [f"status {outcome.status}"]
    return score_analysis(outcome.analysis, expected)


def score_analysis(analysis, expected) -> list[str]:
    text = " ".join(
        [analysis.summary, *analysis.unknowns, *analysis.next_steps]
        + [h.cause for h in analysis.hypotheses]
    ).lower()
    problems = []
    if expected.get("no_hypotheses") and analysis.hypotheses:
        problems.append(f"invented {len(analysis.hypotheses)} hypotheses on a healthy system")
    for group in expected.get("any", []):
        if not any(word in text for word in group):
            problems.append(f"mentioned none of {group}")
    ranks = [CONFIDENCE[h.confidence] for h in analysis.hypotheses]
    top = max(ranks, default=0)
    leading = [h.cause.lower() for h in analysis.hypotheses if CONFIDENCE[h.confidence] == top]
    if "lead" in expected and not any(w in c for w in expected["lead"] for c in leading):
        problems.append(f"leading hypothesis names none of {expected['lead']}")
    if expected.get("not_high") and "high" in [h.confidence for h in analysis.hypotheses]:
        problems.append("high confidence without evidence of a cause")
    if any(len(set(h.evidence_ids)) > MAX_CITATIONS for h in analysis.hypotheses):
        problems.append(f"a hypothesis cites more than {MAX_CITATIONS} evidence items")
    if len(analysis.hypotheses) > MAX_HYPOTHESES:
        problems.append(f"more than {MAX_HYPOTHESES} hypotheses")
    steps = " ".join(analysis.next_steps).lower()
    for phrase in expected.get("forbidden_next_steps", []):
        if phrase in steps:
            problems.append(f"recommended {phrase!r}")
    return problems


def rescore(path: str) -> int:
    """Score saved reports again, for when the scoring changes. Calls no model."""
    expected = {name: e for name, _, _, e in scenarios()}
    totals: dict[str, list[int]] = {}
    for row in json.loads(Path(path).read_text()):
        analysis = LLMAnalysis.model_validate(row["analysis"]) if row["analysis"] else None
        name = row["scenario"]
        problems = score_analysis(analysis, expected[name]) if analysis else [row["status"]]
        totals.setdefault(name, []).append(not problems)
        print(f"{'ok  ' if not problems else 'FAIL'} {name} run {row['run']} {problems or ''}")
    good = sum(sum(v) for v in totals.values())
    print(f"\n{good}/{sum(len(v) for v in totals.values())} passed")
    return 0


def scenarios():
    for directory in sorted(p for p in SCENARIOS.iterdir() if (p / "expected.yaml").exists()):
        expected = yaml.safe_load((directory / "expected.yaml").read_text()).get("llm")
        fixture_path = directory / "fixture.json"
        if expected and fixture_path.exists():
            fixture = json.loads(fixture_path.read_text())
            bundle = Bundle.model_validate(fixture["bundle"])
            yield directory.name, fixture["provenance"], bundle, expected


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--provider", choices=["anthropic", "ollama"], default="anthropic")
    parser.add_argument("--model", default="")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--out", help="save every report here, so they can be read")
    parser.add_argument("--budget-usd", type=float, default=1.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rescore", help="score a saved --out file again, with no model calls")
    args = parser.parse_args()

    if args.rescore:
        return rescore(args.rescore)
    if args.dry_run:
        sizes = [
            len(llm.build_prompt(bundle, evaluate(bundle), RUNBOOK))
            for _, _, bundle, _ in scenarios()
        ]
        tokens = (sum(sizes) + len(sizes) * len(llm.SYSTEM_PROMPT)) // 4
        print(f"{len(sizes)} scenarios x {args.runs} runs = {len(sizes) * args.runs} requests")
        print(f"Largest prompt {max(sizes)} chars; Ollama limit {llm.OLLAMA_MAX_PROMPT_CHARS}")
        if args.provider == "anthropic":
            model = args.model or llm.DEFAULT_MODEL
            output = len(sizes) * OUTPUT_TOKENS_ESTIMATE
            cost = llm.estimate_cost(model, args.runs * tokens, args.runs * output)
            print(f"About {tokens} input tokens per pass; rough total ${cost:.2f} on {model}")
        return 0

    provider = llm.make_provider(
        args.provider, args.model, os.getenv("ANTHROPIC_API_KEY", ""), args.ollama_url
    )
    if provider is None:
        print("Set ANTHROPIC_API_KEY, or use --provider ollama", file=sys.stderr)
        return 2
    saved = []
    with tempfile.TemporaryDirectory() as directory:
        guard = llm.SpendGuard(f"{directory}/spend.json", args.budget_usd)
        passed, total, spent = 0, 0, 0.0
        for name, provenance, bundle, expected in scenarios():
            findings = evaluate(bundle)
            results = []
            started = time.monotonic()
            for run in range(args.runs):
                outcome = llm.analyze(provider, bundle, findings, RUNBOOK, guard)
                spent += outcome.usage.estimated_usd if outcome.usage else 0.0
                problems = score(outcome, expected)
                results.append(problems)
                saved.append(
                    {
                        "scenario": name,
                        "run": run,
                        "status": outcome.status,
                        "problems": problems,
                        "analysis": outcome.analysis.model_dump() if outcome.analysis else None,
                        "usage": outcome.usage.model_dump() if outcome.usage else None,
                    }
                )
            seconds = (time.monotonic() - started) / args.runs
            good = sum(not r for r in results)
            passed, total = passed + good, total + len(results)
            print(
                f"{good}/{len(results)}  {name} [{provenance}] "
                f"rules: {findings[0].category} ({seconds:.0f}s per run)",
                flush=True,
            )
            for problem in sorted({p for r in results for p in r}):
                print(f"      {problem}")
    if args.out:
        Path(args.out).write_text(json.dumps(saved, indent=1))
    print(f"\n{passed}/{total} passed. Estimated cost ${spent:.2f}.")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
