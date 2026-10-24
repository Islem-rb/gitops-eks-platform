# ADR 0004: Restart the VPC CNI policy agent after a namespace restore on EKS

Status: accepted (workaround), root cause not yet proven

## Context
The Velero restore drill passed on kind. On EKS it restored the data correctly (the pg_dump hook loaded the rows), but the
restored pods could not use the network: DNS lookups from the `demo` namespace timed out, so the api could not resolve
`postgres` (psycopg `PoolTimeout`) and the frontend could not resolve `api`. Pods in other namespaces and on the other node
resolved DNS normally. CoreDNS was healthy, the NetworkPolicy objects and the PolicyEndpoint objects listed the correct pod
IPs, and no egress policy existed in the namespace.

## What was observed
- All restored pods and both CoreDNS pods were on the same node; only same-node traffic in `demo` failed.
- Restarting the `aws-node` pod (CNI plus network policy agent, v1.22.4 / agent v1.4.0) on that node fixed DNS at once;
  restarted api/frontend pods then became Ready.
- The failure reproduced on a second, identical drill run. The agent log could not be read (distroless image, no shell).

## Decision
The drill script now tests DNS from the restored namespace. If it fails, it restarts the `aws-node` pod on every node that
hosts demo pods, bounces the api and frontend pods, and prints `agent restart: yes`, so the workaround is always recorded.
The data check reads postgres directly and separately verifies that the API serves it; an earlier version checked only
through the API and reported a false `DATA LOST`.

## Consequences
- Recovery on EKS took 202 s (data) / 204 s (serving) including the agent restart.
- This is a workaround, not an explanation. Next step: read the node agent logs from the host (or a node debug pod), check
  for a known issue in the agent version, and test whether recreating the NetworkPolicies after restore avoids the restart.
- Velero should not restore controller-owned objects (for example PolicyEndpoints); excluding them from the backup is a
  candidate follow-up.
