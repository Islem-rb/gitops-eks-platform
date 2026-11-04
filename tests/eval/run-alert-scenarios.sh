#!/bin/bash
# Test the path real incidents take: inject a fault and let Prometheus and Alertmanager start the
# investigation, then print every report it produced. Run against scripts/kind-up.sh.
# Faults follow each other closely on purpose, to test that a new fault is not suppressed.
set -u
cd "$(dirname "$0")/../.."
export ARGO_APP="${ARGO_APP:-demo-kind}"

reports() { # reports created since $1 (UTC time), oldest first
  kubectl -n ops-agent exec deploy/ops-agent -- python -c "
import glob, json
rows = []
for f in glob.glob('/data/investigations/*.json'):
    r = json.load(open(f))
    if r['bundle']['trigger'].startswith('alert') and r['created_at'][:19] >= '$1':
        rows.append((r['created_at'][11:19], r['bundle']['alerts'], r['passes'],
                     [x['category'] for x in r['findings']]))
for row in sorted(rows): print(' ', *row)
"
}
webhooks() { kubectl -n ops-agent logs deploy/ops-agent | grep -c 'POST /webhook' || true; }
clean() {
  local bad not_ready
  bad=$(kubectl -n demo get pods --no-headers | grep -vcE '1/1 +Running')
  not_ready=$(kubectl -n demo get deploy --no-headers | awk '{split($2,a,"/"); if(a[1]!=a[2]) print}' | wc -l)
  [ "$(kubectl -n demo get rollout api -o jsonpath='{.status.phase}')" = Healthy ] && [ "$bad" = 0 ] && [ "$not_ready" = 0 ]
}
settle() {
  for _ in $(seq 1 40); do clean && return 0; sleep 15; done
  echo "STOP: cluster did not return to clean"; kubectl -n demo get pods --no-headers; exit 1
}
scenario() { # name, seconds the fault stays active
  settle
  local start; start=$(date -u +%Y-%m-%dT%H:%M:%S)
  echo "=== $1, fault active for $2 s, from $start"
  bash "tests/scenarios/$1/inject.sh" >/dev/null 2>&1
  sleep "$2"
  bash "tests/scenarios/$1/revert.sh" >/dev/null 2>&1
  settle
  sleep 200   # let the last run finish: settle, plus up to two rechecks
  reports "$start"
  kubectl -n ops-agent logs deploy/ops-agent | grep '^investigation' | tail -3 | sed 's/^/  log: /'
}
late_alert() { # the alert arrives just as the fault is fixed
  settle
  local start before; start=$(date -u +%Y-%m-%dT%H:%M:%S); before=$(webhooks)
  echo "=== redis-down with a late alert, from $start"
  bash tests/scenarios/redis-down/inject.sh >/dev/null 2>&1
  for _ in $(seq 1 60); do [ "$(webhooks)" != "$before" ] && break; sleep 10; done
  echo "  webhook arrived at $(date -u +%T), reverting now"
  bash tests/scenarios/redis-down/revert.sh >/dev/null 2>&1
  settle
  sleep 200
  reports "$start"
}

scenario redis-down 300
scenario db-credential 300
scenario network-block 300
scenario post-promotion-errors 300
sleep 300   # the late-alert case needs its own alert to fire, so let the last one resolve
late_alert
sleep 300
scenario fault-after-recovery 300
echo DONE
