#!/bin/bash
# Redis fails and recovers, then a different fault begins. The Redis lines are still in the log
# window, but they don't explain the new errors.
source "$(dirname "$0")/../lib.sh"
pause_sync
kubectl -n demo scale deployment redis --replicas=0
sleep 45
kubectl -n demo scale deployment redis --replicas=1
kubectl -n demo rollout status deployment redis --timeout=120s
set_fail_rate 0.5
# skip the canary and the analysis, as if a bad version had already been promoted
kubectl -n demo patch rollout api --subresource=status --type merge -p '{"status":{"promoteFull":true}}'
