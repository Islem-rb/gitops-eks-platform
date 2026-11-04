#!/bin/bash
source "$(dirname "$0")/../lib.sh"
pause_sync
set_fail_rate 0.5
# skip the canary and the analysis, as if a bad version had already been promoted
kubectl -n demo patch rollout api --subresource=status --type merge -p '{"status":{"promoteFull":true}}'
