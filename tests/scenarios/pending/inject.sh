#!/bin/bash
source "$(dirname "$0")/../lib.sh"
pause_sync
kubectl -n demo patch deployment worker -p '{"spec":{"template":{"spec":{"containers":[{"name":"worker","resources":{"requests":{"cpu":"64"}}}]}}}}'
