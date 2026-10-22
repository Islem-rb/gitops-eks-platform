#!/bin/bash
set -u
jobs() { kubectl -n demo exec deploy/loadgen -- curl -s http://api:8000/jobs 2>/dev/null; }
post() { kubectl -n demo exec deploy/loadgen -- curl -s -X POST http://api:8000/jobs -H 'Content-Type: application/json' -d "{\"payload\":\"$1\"}" 2>/dev/null; }
waitphase() {
  for i in $(seq 1 120); do
    p=$(kubectl -n velero get $1 $2 -o jsonpath='{.status.phase}' 2>/dev/null)
    echo "  $1/$2: ${p:-?}"; case "$p" in Completed|PartiallyFailed|Failed) return;; esac; sleep 5
  done
}

A=$(kubectl -n demo get pod -l app=postgres -o jsonpath='{.items[0].metadata.annotations}')
NS=$(echo "$A" | grep -o 'dump.sql' | wc -l)
if ! echo "$A" | grep -q pg_dump || ! echo "$A" | grep -q psql || [ "$NS" -lt 2 ]; then
  echo "ABORT: backup/restore hook annotations missing or damaged in the live pod"; exit 1; fi
echo "== hook annotations look intact"

TS=$(date +%s); MARK="drill-marker-$TS"
echo "== write marker: $(post $MARK)"; sleep 4
echo "== marker present before: $(jobs | grep -c "$MARK")"

echo "== backup"
cat <<YAML | kubectl apply -f -
apiVersion: velero.io/v1
kind: Backup
metadata: { name: demo3-$TS, namespace: velero }
spec:
  includedNamespaces: [demo]
  defaultVolumesToFsBackup: true
  ttl: 168h
YAML
waitphase backup demo3-$TS
PH=$(kubectl -n velero get backup demo3-$TS -o jsonpath='{.status.phase}')
ERR=$(kubectl -n velero get backup demo3-$TS -o jsonpath='{.status.errors}')
HOOK=$(kubectl -n velero get backup demo3-$TS -o jsonpath='{.status.hookStatus}')
PVB=$(kubectl -n velero get podvolumebackups -o jsonpath='{.items[*].status.phase}')
echo "backup phase=$PH errors=${ERR:-0} hooks=$HOOK podvolumebackups=[$PVB]"
if [ "$PH" != "Completed" ] || ! echo "$PVB" | grep -q Completed; then
  echo "ABORT: backup not clean, namespace NOT deleted"; exit 1; fi

echo "== DISASTER: pause auto-sync, delete namespace demo"
kubectl -n argocd patch app demo-kind --type merge -p '{"spec":{"syncPolicy":{"automated":null}}}'
T0=$(date +%s)
kubectl delete namespace demo --wait=true --timeout=240s

echo "== restore"
cat <<YAML | kubectl apply -f -
apiVersion: velero.io/v1
kind: Restore
metadata: { name: demo3-restore-$TS, namespace: velero }
spec:
  backupName: demo3-$TS
YAML
waitphase restore demo3-restore-$TS
kubectl -n demo wait --for=condition=ready pod -l app=postgres --timeout=240s
kubectl -n demo wait --for=condition=ready pod -l app=api --timeout=240s
kubectl -n demo wait --for=condition=ready pod -l app=loadgen --timeout=240s
T1=$(date +%s)
echo "== pods ready after: $((T1-T0)) s"

kubectl -n argocd patch app demo-kind --type merge -p '{"spec":{"syncPolicy":{"automated":{"prune":true,"selfHeal":true}}}}'
N=0
for i in $(seq 1 30); do N=$(jobs | grep -c "$MARK"); [ "$N" -ge 1 ] && break; sleep 5; done
T2=$(date +%s)
echo "== restore hook status: $(kubectl -n velero get restore demo3-restore-$TS -o jsonpath='{.status.hookStatus}')"
echo "== marker present AFTER restore: $N"
if [ "$N" -ge 1 ]; then echo "RESULT: DATA RECOVERED, data back after $((T2-T0)) s"; else echo "RESULT: DATA LOST"; fi
kubectl -n demo get pods
kubectl -n argocd get applications
