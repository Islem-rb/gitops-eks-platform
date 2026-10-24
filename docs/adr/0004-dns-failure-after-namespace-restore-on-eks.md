# ADR 0004: DNS failure after a namespace restore on EKS (observed once, cause not established)

Status: open

## Context
In the first EKS restore drill the data came back correctly (the pg_dump hook loaded the rows), but the restored pods could not
resolve DNS for over ten minutes. `nslookup` from the loadgen pod timed out against both the kube-dns service IP and a CoreDNS
pod IP; the api failed with a psycopg `PoolTimeout` and the frontend with `host not found in upstream "api"`. A pod in another
namespace on the other node resolved DNS normally. All demo pods and both CoreDNS pods were on the same node. CoreDNS was healthy,
the NetworkPolicy and PolicyEndpoint objects listed the correct pod IPs, and the namespace had no egress policies.
Restarting the `aws-node` pod (VPC CNI v1.22.4, network policy agent v1.4.0) on that node restored DNS immediately, and the
restarted api and frontend pods then became Ready.

## What we learned afterwards
- The second EKS drill used the exit code of BusyBox `nslookup` to detect the problem. That exit code is 1 even when the lookup
  succeeds (the search-domain lookups return NXDOMAIN), so "agent restart needed: yes" in that run, and on kind, was a false
  positive. We cannot tell whether the restart was needed in the second run.
- On kind (no VPC CNI), a restore from backup gave working DNS immediately (restore complete, 118/118 items, DNS OK at 0 s).
- The drill now checks for the actual answer in the `nslookup` output.

## Decision
Keep the corrected DNS check in `scripts/restore-drill.sh`. If DNS really fails after a restore, it restarts the `aws-node` pod on
the affected nodes, bounces the api and frontend pods, and prints that it did. No claim is made about the root cause.

## Next steps
- The next EKS session is the reproduction test. If the corrected check reports a DNS failure again, suspect the node agent's
  policy state: read the agent logs from a node debug pod and compare agent versions. If it does not, the first failure was a
  one-off.
- Until it reproduces, this is not described as a known bug or a known fix.
