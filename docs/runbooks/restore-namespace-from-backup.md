# Runbook: Restore the `demo` namespace from a Velero backup

**When:** namespace deleted, data lost or corrupted.
**Verified:** drill on kind: pods ready in ~112 s, data back by ~113 s (`scripts/restore-drill.sh`).

## How backups work
Velero backs up all objects in `demo`. Postgres is protected by a **pg_dump pre-backup hook** (dump written to an emptyDir that Velero copies) and a **restore hook** that loads the dump into the fresh database. Raw volume copy is not used for the database.

## Steps
1. Pause ArgoCD auto-sync so self-heal does not recreate an empty namespace:
   `kubectl -n argocd patch app demo-kind --type merge -p '{"spec":{"syncPolicy":{"automated":null}}}'`
2. List backups and pick the latest `Completed` one: `kubectl -n velero get backups`
3. Restore:

kubectl apply -f - <<YAML
apiVersion: velero.io/v1
kind: Restore
metadata: { name: restore-$(date +%s), namespace: velero }
spec: { backupName: <backup-name> }
YAML

4. Wait for `Completed`: `kubectl -n velero get restore`. Check `hookStatus` shows no failures.
5. Re-enable auto-sync:
   `kubectl -n argocd patch app demo-kind --type merge -p '{"spec":{"syncPolicy":{"automated":{"prune":true,"selfHeal":true}}}}'`
6. Verify the app and a known record (jobs API) before declaring recovery.

## Known limits
- kind's local-path volumes are hostPath, which Velero cannot copy: hence the dump hook.
- Backup data lives in an in-cluster S3 on kind (lost with the cluster). On EKS use a real S3 bucket.
