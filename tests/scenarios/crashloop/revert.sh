#!/bin/bash
source "$(dirname "$0")/../lib.sh"
# Argo CD does not remove a field that is absent from Git: it reports Synced and leaves it in place.
kubectl -n demo patch deployment worker --type json \
  -p '[{"op":"remove","path":"/spec/template/spec/containers/0/command"}]'
resume_sync
