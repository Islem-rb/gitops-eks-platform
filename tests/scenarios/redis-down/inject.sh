#!/bin/bash
source "$(dirname "$0")/../lib.sh"
pause_sync
kubectl -n demo scale deployment redis --replicas=0
