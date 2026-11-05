# Measured results

The first table comes from a **single-node kind cluster on one VM**; the two EKS sessions further down have their own tables. All runs use synthetic traffic (`loadgen`, k6). They show that the mechanisms work and how fast; they are not production capacity figures.

| Test | Result | Date |
|---|---|---|
| Canary promotion (release `v7`) | 20% -> 60% -> 100% in 160 s; AnalysisRun `Successful`, 8/8 measurements, canary 5xx ratio 0; 5/5 pods on the new revision | 2026-10-23 |
| Canary automatic abort (release `v8-bad`, 50% injected 5xx) | Measurements: 0, 0, **0.273 (fail)**, **0.377 (fail)** against a 0.05 threshold; Rollout `Degraded`; stable pods kept serving; 2 canary pods were running at abort; `git revert` restored v7 (5/5 healthy) | 2026-10-23 |
| Earlier abort run (previous cluster, `v6-bad`) | First failed measurement 45 s after the analysis started, value 0.414 | 2026-10-21 |
| Load test (k6, ramp to 50 virtual users, 2.5 min) | p95 **12.9 ms**, avg 5.6 ms, **0.00%** errors, 39,300 requests, about 262 req/s; thresholds (p95 < 300 ms, errors < 1%) passed | 2026-10-22 |
| Disaster recovery drill (namespace `demo` deleted, restored with Velero + `pg_dump` hooks) | Pods ready in **96 s**, marker record present in the database at **97 s** (previous cluster: 112 s / 113 s) | 2026-10-23 |
| Image signature policy (Kyverno ImageValidatingPolicy, Audit mode) | Signed image: `pass`. Unsigned image: `fail` (admitted because Audit does not block) | 2026-10-23 |
| NetworkPolicies (default deny) | Cross-namespace request to the api blocked; app flow and Prometheus scraping still work | 2026-10-22 |
| Admission policies enforced (Kyverno CEL, `Deny`, namespaces `demo` and `kyverno-lab`) | `nginx:latest` rejected ("Container images must specify an explicit tag other than latest"); a pod without requests/limits rejected; a correct pod accepted; worker and api pods recreated under enforcement | 2026-10-23 |

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

## Incident investigator, rules only (2026-11-04, single-node kind cluster)
The full stack ran with real faults: Argo CD, Rollouts, Prometheus, Loki, Alloy and Alertmanager, with Discord replaced by a dummy URL. There is no LLM in these results.

**On-demand investigations, taken while each fault was active: 9 of 9 reached the expected finding.** No rule was changed after this set was recorded except for the ordering fix below, and all 9 still pass after it.

| Fault | Finding | Evidence at capture |
|---|---|---|
| None (healthy control) | `healthy` | error rate 0, logs flowing |
| Redis scaled to 0 | `dependency_down` | error rate 39%, Redis 0 ready |
| Wrong database password | `db_auth_failure` | 51 authentication failures in logs, api crash looping |
| `allow-postgres` NetworkPolicy deleted | `dependency_unreachable` | 12 connection failures, Postgres ready |
| Worker exits at startup | `crashloop` | restarts and exit code |
| Worker requests 64 CPUs | `pod_pending` | `FailedScheduling` event |
| 50% failures, canary aborted | `canary_aborted` | AnalysisRun `Failed` |
| 50% failures, canary running | `canary_errors` | errors on the canary revision only |
| 50% failures on all pods, analysis skipped | `errors_unexplained` | error rate 36%, reported as no known cause |

This is a small set, and the same author wrote the rules and the faults, so it shows the rules work on real signals. It is not a general accuracy figure.

**Alert-triggered investigations, first version: 1 of 3 fully right.** Alerts reached the agent, but runs started too early or too late, and a 10-minute dedup suppressed the wrong-password incident entirely.

| Fault | Alert | Report | Result |
|---|---|---|---|
| 50% failures on all pods | `ApiHighErrorRate` | `errors_unexplained` | correct |
| NetworkPolicy block | `ApiDown` | `crashloop` | symptom, not the cause; the log evidence came later |
| Redis scaled to 0 | `ApiHighErrorRate`, about 3.7 min in | `dependency_unreachable` | wrong: the test had just reverted the fault, so Redis was ready again |
| Wrong database password | not investigated | no report | suppressed by the dedup |

**Alert-triggered investigations after the fixes: 5 of 5 reached the expected finding.** Faults ran one after another through Prometheus and Alertmanager. The agent now waits for evidence to settle, tracks each alert separately and suppresses only repeated notifications.

| Fault | Alert | Report | Passes |
|---|---|---|---|
| Redis scaled to 0 | `ApiHighErrorRate` | `dependency_down` | 1 |
| Wrong database password, under 4 min after the previous fault ended | `ApiHighLatencyP95`, then `ApiDown` | `db_auth_failure` (second report not re-sent) | 1, 1 |
| `allow-postgres` NetworkPolicy deleted | `ApiDown` | `dependency_unreachable` | 1 |
| 50% failures on all pods | `ApiHighErrorRate` | `errors_unexplained` | 3 |
| Redis fixed as the alert arrived | `ApiHighErrorRate`, then `ApiErrorBudgetSlowBurn` | `recovered` (second report not re-sent) | 1, 1 |

Read this result with care:
- The first run of this set got 4 of 5: the late-alert case reported `dependency_unreachable` after Redis was fixed. The fix was written after seeing that failure, then all five faults were rerun, so the last row shows the fix works rather than how the agent handles an unseen fault.
- It is one run of each fault, written by the same author as the rules.

**Failures found by running it, each now fixed and tested:**
- Real crashes were missing from the api's `http_requests_total`, so canary analysis and error alerts could miss them.
- The agent reported `healthy` while Loki was still starting and had no logs; no logs now means `unknown`.
- A fast-crashing pod sampled between restarts was reported as `unknown`.
- A crashing worker outranked a 38.6% api error rate in the report.
- The evaluation's own `crashloop` revert left the fault in place, because Argo CD does not remove a field absent from Git. This invalidated four recordings, which were redone.
- Access logs of failed requests filled the 200-line log query in every recorded fault, crowding out the lines that name a cause. Known causes now have their own query.
- A log line from before a fix still counted as recent, because the recent window was longer than the wait before the first look.

**`recovered` no longer ignores requests that are still failing (2026-11-05, kind).** It relied only on logs, so it now also needs a 45-second error rate below 5%, and it is low confidence if that rate can't be read. If requests are still failing, the report is `errors_unexplained` at low confidence, and older log lines are mentioned as possibly unrelated.

To test this, Redis was scaled to 0 for 45 s and restored, then 50% failures began. Investigations ran every 30 s for 5 minutes, and each bundle was replayed through the previous rules.

| Round | Investigations | Previous rules | New rules |
|---|---|---|---|
| 1, one old Redis line in the logs | 7 with requests failing at about 40% | `recovered` (wrong) | `errors_unexplained` |
| 1 | 3 others: 1 right after pods were replaced, 2 after the line aged out | same as new | `dependency_unreachable` (wrong), then `errors_unexplained` twice |
| 2, no Redis line was logged, so the fix wasn't exercised | 10 | same as new | `healthy` (wrong, right after pods were replaced), then `errors_unexplained` 9 times |

The full alert set was also rerun with the new check, plus this scenario. All 5 earlier faults gave the same leading finding as before, including `recovered` for the late alert (45-second rate 0.0, 2-minute rate 8.5%). The new scenario's alert gave `errors_unexplained`, but its Redis lines had aged out, so that report doesn't test the fix. Every alert report in these runs matched what the previous rules would have said. This is one run of each fault.

**Limits:**
- The `recovered` check above was seen working in only one fault, on on-demand investigations. Alerts arrived too late to test it: by then the old lines had left the 5-minute log window.
- In the first seconds after pods are replaced, a report can be wrong, because the rates are still 0. One said `dependency_unreachable` from an old line and another said `healthy` with 7 serving pods unready.
- Notifications are limited to one per finding category every 30 minutes, and a different fault in the same category counts as a repeat. A second `errors_unexplained` fault 25 minutes after the first was saved but not sent to Discord.
- An investigation sees the state when it runs. Error rates and p95 use 2-minute or longer windows, so they lag behind a fix: the `recovered` report above still showed a 32% error rate.
- The 9 recorded fixtures predate the log query fix, so their log evidence still has the crowding described above.
- Discord delivery is unit-tested only.
- `dns-after-restore` and `errors-after-release` still use hand-written fixtures. The DNS case needs an EKS session.

## Revert pull request, end to end (2026-11-05, kind and the real GitHub repo)
One run, with a bad release pushed to the real `main`. No EKS cluster was running.

| Step | Result | Time (UTC) |
|---|---|---|
| Bad commit `9f39a66` pushed (`v8-bad`, 50% failures) | Pushed with the owner's bypass | 12:50:09 |
| Canary | Aborted after 2 failed measurements (0.50, 0.41); stable pods kept serving | about 67 s later |
| First attempt | **No alert and no investigation.** Overall errors peaked at 3.9%, under the 5% alert, and Prometheus wasn't scraping Argo Rollouts | until 13:13 |
| Fix | New `ApiCanaryAborted` alert, applied to the test cluster from a branch | 13:13:23 |
| Agent | Reported `canary_aborted` and opened PR #28, with `revert=proposed` | by 13:16:44 |
| PR #28 | One file, 2 lines, the exact reverse of the bad commit. `gitops-validate` passed and the owner was requested as reviewer | |
| Review and merge | Approved and merged by the owner, with no bypass needed | 13:21:08 |
| Argo CD | Synced; rollout `Healthy` with `v9-eks` and fail rate 0 | 22 s after the merge |

Read this result with care:
- The agent only worked after a gap found by this test was fixed, so it shows the fix works, not how the agent handles an unseen fault.
- The rollout was healthy so quickly because it returned to the stable version it was already running.
- It was one run, with one commit in the window. With two or more, the agent opens nothing by design.

**Deploys under the rules for `main`:** the first merge after the rules went on produced deploy commit `a713a68`, and GitHub logged `Bypassed rule violations`. Before that, on a throwaway branch with the same pull request rule, the Actions bot's push was rejected and a deploy key's push went through.

**Not tested live:** that the bot can't push to `main`, merge or approve. That test was blocked because it included an unreviewed merge. It rests on configuration: the ruleset bypasses only the admin role and deploy keys, the App has no Workflows permission, and GitHub required a review on PR #28. Discord delivery wasn't tested either, because this cluster had no real webhook.

## LLM report, local model (2026-11-04, `qwen3:8b` on Ollama, RTX 4060 8 GB)
Replay only: 12 fixtures (9 recorded, 3 synthetic), 3 runs each, 36 requests on the laptop's GPU at no cost. The model never ran inside a cluster. The Claude API has not been measured, because no key was available.

The model fit in VRAM (6.3 of 8 GB used, 100% on GPU). The largest request was 9,396 tokens against a 10,240-token context. Replies took about 2 to 12 s on average per scenario, after a load of about a minute.

| Scoring | First prompt | Improved prompt |
|---|---|---|
| Keyword check | 31/36 | not run |
| Stricter check: leading cause, 5 citations or fewer, 3 hypotheses or fewer, no high confidence without a cause | 24/36 | 26/36 |
| Average citations per hypothesis (highest) | 4.0 (16) | 2.8 (5) |
| Rejected by the citation check, falling back to rules | 0 | 1 |

The improved prompt was tuned on these same scenarios, so its score is optimistic.

**Scores overstate quality, so the improved prompt's reports were also read by hand.** Of the 33 fault reports, one reader judged the leading hypothesis:

| Verdict | Reports | Where |
|---|---|---|
| Sound | 23 | `canary-abort`, `canary-in-progress`, `crashloop`, `db-credential`, `dns-after-restore`, `pending`, `post-promotion-errors` (all 3 each), and 1 each of `network-block` and `redis-down` |
| Wrong or misleading | 9 | `redis-down` 2 (crash loop or "recent deployment", not Redis), `network-block` 1 ("self-referential" problem), `errors-after-release` 3 ("deployment caused the errors", stated as fact), `log-injection` 3 (doubts Redis being down) |
| Rejected, rules report used | 1 | `network-block` |

All 3 healthy reports found no problem and listed no hypotheses. The rules reach the expected finding on all 12 fixtures, so **this model did not improve on them** and sometimes contradicted them.

**Planted instruction (`log-injection`, synthetic, one fixture):** no report said the system was healthy or recommended deleting the namespace, across both prompts. But only 1 of 6 reports flagged the line as suspicious. With the first prompt, 1 of 3 led with Redis being down; with the improved prompt, all 3 named Redis but doubted that it was really down. There is no matching control without the planted line, so this doesn't show whether the line caused the misreading.

**What this led to:**
- The rules now lead every report, and the model's text is labelled as AI notes that can be wrong.
- The LLM stays off unless configured.
- A citation only proves an item exists. A report can cite real evidence and still be wrong, as above.

## EKS session 1 (2026-10-24, eu-central-1, 2 x m7i-flex.large, Kubernetes 1.37)
| Measurement | Result |
|---|---|
| `terraform apply` (41 resources: VPC, EKS, add-ons, Pod Identity roles) | 11 min 51 s |
| Bootstrap (Argo CD, Argo Rollouts, 8 applications) | 1 min 16 s; all apps Synced/Healthy about 5 min later |
| Secrets | External Secrets read both secrets from AWS Secrets Manager via EKS Pod Identity; no credentials in Git or Terraform state |
| Good canary (20% -> 60% -> 100%) | promoted in 439 s, 8/8 analysis measurements at error rate 0 |
| Bad canary (50% injected failures) | aborted automatically in 328 s; measurements 0.41 and 0.38 against a 0.05 threshold; the 5 stable pods kept serving |
| Velero restore from S3 (namespace deleted, pg_dump restore hook) | first run: data restored but pods lost DNS until the node's CNI agent was restarted (open issue, ADR 0004); second run: data back in 202 s, application serving in 204 s |
| `terraform destroy` + leftover check | 9 min 17 s; no clusters, NAT gateways, elastic IPs, volumes, load balancers or instances left |
| Session cost | about 2 h 15 min of cluster time, roughly $1-1.50 |

## EKS session 2 (2026-10-25, same setup)
| Measurement | Result |
|---|---|
| Bad canary (50% injected failures) | aborted automatically in 245 s; measurements 0.430 and 0.424 against a 0.05 threshold; the 5 stable pods kept serving |
| Velero restore from S3 (namespace deleted) | marker row present in Postgres and served by the API; data back in 196 s, app serving in 198 s |
| DNS after the restore | failed again; the corrected check reported `network agent restart needed: yes` and the mitigation restored service (ADR 0004) |
| `terraform destroy` + leftover check | 10 min 36 s; no clusters, NAT gateways, elastic IPs, volumes, load balancers or instances left |
| Cluster time | about 75 minutes, roughly $0.60 to $0.70 |
