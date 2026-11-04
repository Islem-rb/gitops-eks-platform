#!/bin/bash
source "$(dirname "$0")/../lib.sh"
resume_sync
sleep 30
kubectl -n demo delete pod -l app=api --wait=false
