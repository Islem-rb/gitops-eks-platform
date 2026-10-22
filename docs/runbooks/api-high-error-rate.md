# Runbook: ApiHighErrorRate / SLO burn-rate alert

**Alert:** `ApiHighErrorRate` (5xx > 5% for 1 min, critical) or the fast/slow SLO burn-rate alerts. Delivered to Discord.
**Impact:** users get failed requests; the 99% availability SLO error budget is burning.

## 1. Triage (2 min)
1. Open Grafana, dashboard **Platform SLO**: is the 5xx ratio high on one revision or on all?
2. Was there a release in the last 30 min? `kubectl argo rollouts get rollout api -n demo` (or `kubectl -n demo get rollout api`).
3. Check the logs panel (Loki) or `kubectl -n demo logs -l app=api --tail=50`.

## 2. Decide
| Situation | Action |
|---|---|
| Errors only on the canary revision, rollout still progressing | Wait: the AnalysisRun aborts automatically (first failing measurement ~45 s). Confirm status `Degraded`. |
| Rollout already promoted and errors persist | Roll back via Git (below). This is the source of truth. |
| Errors with no recent release | Check dependencies: Postgres/Redis pods, `readyz`, NetworkPolicies, Kyverno denials. |

## 3. Roll back (GitOps way)

git revert <bad-commit> && git push
kubectl -n argocd annotate app demo-kind argocd.argoproj.io/refresh=hard --overwrite

Do not `kubectl edit` live objects; ArgoCD self-heal will undo it.

## 4. Verify and close
5xx ratio back under 1%, alert resolved in Discord, write a short note (what, why, fix) in the incident log.
