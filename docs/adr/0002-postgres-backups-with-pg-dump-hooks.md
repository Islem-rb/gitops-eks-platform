# 0002. Back up Postgres with pg_dump hooks, not volume copy

Status: accepted

## Context
Velero's file-system backup did not capture the Postgres volume on kind: the PVs from the local-path provisioner are `hostPath`, and the backup produced no volume backup even though it reported `Completed`. A raw copy of a running database's files is also not guaranteed to be consistent.

## Decision
A pre-backup hook runs `pg_dump --clean --if-exists` inside the Postgres pod into an `emptyDir` that Velero can copy. A restore hook loads the dump with `psql`. The drill script refuses to continue unless a volume backup exists and the backup phase is `Completed`.

## Consequences
- The backup is logical and consistent. Recovery point = backup frequency; restore time grows with database size.
- Measured restore of the demo namespace: about 97 s.
- On EKS with the EBS CSI driver this should be re-evaluated (snapshots may be better), by testing, not assuming.
