# Measured results

All numbers come from a **single-node kind cluster on one VM** with synthetic traffic (`loadgen`, k6). They show that the mechanisms work and how fast; they are not production capacity figures.

| Test | Result | Date |
|---|---|---|
| Canary promotion (release `v7`) | 20% -> 60% -> 100% in 160 s; AnalysisRun `Successful`, 8/8 measurements, canary 5xx ratio 0; 5/5 pods on the new revision | 2026-10-23 |
| Canary automatic abort (release `v8-bad`, 50% injected 5xx) | Measurements: 0, 0, **0.273 (fail)**, **0.377 (fail)** against a 0.05 threshold; Rollout `Degraded`; stable pods kept serving; 2 canary pods were running at abort; `git revert` restored v7 (5/5 healthy) | 2026-10-23 |
| Earlier abort run (previous cluster, `v6-bad`) | First failed measurement 45 s after the analysis started, value 0.414 | 2026-10-21 |
| Load test (k6, ramp to 50 virtual users, 2.5 min) | p95 **12.9 ms**, avg 5.6 ms, **0.00%** errors, 39,300 requests, about 262 req/s; thresholds (p95 < 300 ms, errors < 1%) passed | 2026-10-22 |
| Disaster recovery drill (namespace `demo` deleted, restored with Velero + `pg_dump` hooks) | Pods ready in **96 s**, marker record present in the database at **97 s** (previous cluster: 112 s / 113 s) | 2026-10-23 |
| Image signature policy (Kyverno ImageValidatingPolicy, Audit mode) | Signed image: `pass`. Unsigned image: `fail` (admitted because Audit does not block) | 2026-10-23 |
| NetworkPolicies (default deny) | Cross-namespace request to the api blocked; app flow and Prometheus scraping still work | 2026-10-22 |

## Reproduce
```bash
bash scripts/release.sh v7 0          # good release, waits for the outcome
bash scripts/release.sh v8-bad 0.5    # bad release, must end Degraded
kubectl apply -f tests/load/k6-job.yaml && kubectl -n demo logs -f job/k6-load
bash scripts/restore-drill.sh         # aborts unless the backup is clean
```

## Known limits
- Signature policy is Audit-only (see ADR 0003). Image-pull credentials for verification are a 12-hour ECR token on kind.
- Backups on kind use an in-cluster S3 store; on EKS they must go to a real bucket.
