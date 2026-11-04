#!/bin/bash
source "$(dirname "$0")/../lib.sh"
pause_sync
kubectl -n demo delete networkpolicy allow-postgres
# existing connections survive the block, so force new ones
kubectl -n demo delete pod -l app=api --wait=false
