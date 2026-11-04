"""Save what the agent sees right now as a scenario's fixture, while the fault is active.

    kubectl -n ops-agent port-forward svc/ops-agent 8080:8080 &
    python tests/eval/record.py redis-down --token "$(kubectl -n ops-agent get secret \
        ops-agent-webhook -o jsonpath='{.data.token}' | base64 -d)"

Read the result before committing it: fixtures contain log samples.
"""

import argparse
import json
from pathlib import Path

import httpx

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario")
    parser.add_argument("--url", default="http://localhost:8080")
    parser.add_argument("--token", required=True)
    args = parser.parse_args()

    directory = SCENARIOS / args.scenario
    if not (directory / "expected.yaml").exists():
        raise SystemExit(f"unknown scenario: {args.scenario}")
    response = httpx.post(
        f"{args.url}/investigations",
        headers={"Authorization": f"Bearer {args.token}"},
        timeout=120,
    )
    response.raise_for_status()
    report = response.json()
    payload = {"provenance": "recorded", "bundle": report["bundle"]}
    (directory / "fixture.json").write_text(json.dumps(payload, indent=1) + "\n")
    print(f"recorded {args.scenario}: the rules said {report['findings'][0]['category']}")


if __name__ == "__main__":
    main()
