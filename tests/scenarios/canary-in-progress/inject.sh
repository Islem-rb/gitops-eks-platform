#!/bin/bash
source "$(dirname "$0")/../lib.sh"
pause_sync
set_fail_rate 0.5
echo "Record within about 60 s, before the analysis aborts the canary."
