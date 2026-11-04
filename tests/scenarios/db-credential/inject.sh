#!/bin/bash
source "$(dirname "$0")/../lib.sh"
pause_sync
kubectl -n demo patch secret app-db --type merge \
  -p '{"stringData":{"DATABASE_URL":"postgresql://app:wrong-password@postgres:5432/app"}}'
kubectl -n demo delete pod -l app=api --wait=false
