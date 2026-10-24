#!/bin/bash
set -u
jobs() { kubectl -n demo exec deploy/loadgen -- curl -s http://api:8000/jobs 2>/dev/null; }
post() { kubectl -n demo exec deploy/loadgen -- curl -s -X POST http://api:8000/jobs -H 'Content-Type: application/json' -d "{\"payload\":\"$1\"}" 2>/dev/null; }
dbcount() { echo "select count(*) from jobs where payload = '$MARK';" | kubectl -n demo exec -i deploy/postgres -c postgres -- sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tA' 2>/dev/null | tr -d '[:space:]'; }
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
kubectl -n argocd patch app "${ARGO_APP:-demo-kind}" --type merge -p '{"spec":{"syncPolicy":{"automated":null}}}'
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
kubectl -n demo wait --for=condition=ready pod -l app=loadgen --timeout=240s
# EKS finding: after a namespace delete+restore the VPC CNI policy agent can keep stale state and block DNS for the
# restored pods. Detect it, restart the agent on the affected nodes, and record that we had to.
AGENT_RESTART=no; DNS_OK=no
for i in 1 2 3 4 5 6; do
  kubectl -n demo exec deploy/loadgen -- nslookup -timeout=3 postgres 2>&1 | grep -q "postgres.demo.svc.cluster.local" && { DNS_OK=yes; break; }; sleep 5
done
if [ "$DNS_OK" = no ]; then
  AGENT_RESTART=yes
  echo "== NETWORK AGENT RESTART NEEDED: DNS from restored pods fails"
  for NODE in $(kubectl -n demo get pods -o jsonpath='{.items[*].spec.nodeName}' | tr ' ' '\n' | sort -u); do
    kubectl -n kube-system delete pod -l k8s-app=aws-node --field-selector spec.nodeName="$NODE" --wait=true
  done
  sleep 25
  kubectl -n demo delete pod -l app=api --wait=false
  kubectl -n demo delete pod -l app=frontend --wait=false
  sleep 10
fi
echo "== network agent restart needed: $AGENT_RESTART"
kubectl -n demo wait --for=condition=ready pod -l app=api --timeout=240s
T1=$(date +%s)
echo "== pods ready after: $((T1-T0)) s"

kubectl -n argocd patch app "${ARGO_APP:-demo-kind}" --type merge -p '{"spec":{"syncPolicy":{"automated":{"prune":true,"selfHeal":true}}}}'
N=0
for i in $(seq 1 30); do N=$(dbcount); [ "${N:-0}" -ge 1 ] && break; sleep 5; done
T2=$(date +%s)
S=0
for i in $(seq 1 12); do S=$(jobs | grep -c "$MARK"); [ "${S:-0}" -ge 1 ] && break; sleep 5; done
T3=$(date +%s)
echo "== restore hook status: $(kubectl -n velero get restore demo3-restore-$TS -o jsonpath='{.status.hookStatus}')"
echo "== marker in postgres AFTER restore: ${N:-0}"
echo "== marker served through the API AFTER restore: ${S:-0}"
if [ "${N:-0}" -ge 1 ] && [ "${S:-0}" -ge 1 ]; then echo "RESULT: DATA RECOVERED and served; db back after $((T2-T0)) s, app serving after $((T3-T0)) s (agent restart: $AGENT_RESTART)"
elif [ "${N:-0}" -ge 1 ]; then echo "RESULT: DATA RECOVERED in postgres but app NOT serving (agent restart: $AGENT_RESTART)"
else echo "RESULT: DATA LOST"; fi
kubectl -n demo get pods
kubectl -n argocd get applications
