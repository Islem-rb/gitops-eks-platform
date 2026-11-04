# Evaluation scenarios

Each directory holds `expected.yaml` (what a correct report says), `fixture.json` (the evidence) and
`inject.sh` / `revert.sh` (the fault itself). `python tests/eval/run.py` replays every fixture and
needs no cluster.

**`provenance` matters.** Fixtures start as `synthetic`: hand-written by `tests/eval/make_synthetic.py`
to test the rules' logic. Only `recorded` fixtures, taken from a real fault with
`tests/eval/record.py`, say anything about real accuracy. Publish scores from those alone.

## Recording on kind

Secrets must exist first, because they are not in Git and Alertmanager will not start without its copy:

```bash
TOKEN=$(openssl rand -hex 32)
kubectl create namespace ops-agent
kubectl -n ops-agent  create secret generic ops-agent-webhook --from-literal=token="$TOKEN"
kubectl -n monitoring create secret generic ops-agent-webhook --from-literal=token="$TOKEN"
kind load docker-image platform/ops-agent:local   # after docker build -t platform/ops-agent:local app/ops-agent
kubectl apply -f gitops/argocd/ops-agent-kind.yaml
```

Then, for each scenario: run `inject.sh`, wait for the fault to show (about 3 minutes, so the 2-minute
rate windows fill), run `tests/eval/record.py <name>`, then `revert.sh`. Run `healthy` first.

## Special cases

- `canary-in-progress` has a short window: record before the analysis aborts the canary.
- `errors-after-release` has no injector yet. It needs a real commit that deploys and then fails.
- `dns-after-restore` only reproduces on EKS (ADR 0004), and `scripts/restore-drill.sh` applies its
  mitigation automatically, so recording needs a pause in the drill before that step.
