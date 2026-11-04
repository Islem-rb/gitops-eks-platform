# Sourced by the inject.sh and revert.sh scripts. Argo CD selfHeal would undo an injected fault,
# so auto-sync is paused first, the same way scripts/restore-drill.sh does it.
set -euo pipefail
APP="${ARGO_APP:-demo-kind}"

pause_sync() {
  kubectl -n argocd patch app "$APP" --type merge -p '{"spec":{"syncPolicy":{"automated":null}}}'
}

resume_sync() {
  kubectl -n argocd patch app "$APP" --type merge \
    -p '{"spec":{"syncPolicy":{"automated":{"prune":true,"selfHeal":true}}}}'
  kubectl -n argocd annotate app "$APP" argocd.argoproj.io/refresh=hard --overwrite
}

# Sets FAIL_RATE on the live Rollout with no commit, so the evidence does not give away the answer
set_fail_rate() {
  kubectl -n demo patch rollout api --type json -p "[
    {\"op\":\"test\",\"path\":\"/spec/template/spec/containers/0/env/3/name\",\"value\":\"FAIL_RATE\"},
    {\"op\":\"replace\",\"path\":\"/spec/template/spec/containers/0/env/3/value\",\"value\":\"$1\"}]"
}
