# Runbook: Canary release aborted (Rollout Degraded)

**Symptom:** `kubectl -n demo get rollout api` shows `Degraded`; the AnalysisRun is `Failed`.
**What it means:** the automatic safety net worked. At most 1 of 5 pods (20%) ever ran the bad version.

## Investigate

kubectl -n demo get analysisrun --sort-by=.metadata.creationTimestamp | tail -3
kubectl -n demo describe analysisrun <name> | sed -n ‘/Metric Results/,$p’

Look at the measured value (threshold is 5% 5xx on the canary pods) and when the first measurement failed.

## Fix
1. Find the cause in the canary pods' logs (Loki or `kubectl logs`).
2. Fix forward with a new commit, or `git revert` the release commit.
3. The stable version keeps serving traffic throughout; no user action needed.
