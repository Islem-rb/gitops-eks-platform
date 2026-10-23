#!/bin/bash
# usage: scripts/release.sh <version> <fail_rate>   e.g. v7 0   or   v8-bad 0.5
set -u
VER=$1; FR=$2; F=gitops/apps/demo/base/api.yaml
python3 - "$VER" "$FR" "$F" <<'PY'
import re,sys
ver,fr,f=sys.argv[1:4]
s=open(f).read()
s,n1=re.subn(r'(name:\s*APP_VERSION\s*\n\s*value:\s*)"?[^"\n]*"?', r'\1"%s"'%ver, s)
s,n2=re.subn(r'(name:\s*FAIL_RATE\s*\n\s*value:\s*)"?[^"\n]*"?', r'\1"%s"'%fr, s)
assert n1==1 and n2==1, (n1,n2)
open(f,'w').write(s)
PY
git add $F && git commit --no-verify -q -m "release: api $VER (fail rate $FR)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01R2XWrA5AL8ZtTDsnFt9MgB" && git push -q && echo "pushed $VER"
STABLE0=$(kubectl -n demo get rollout api -o jsonpath='{.status.stableRS}')
kubectl -n argocd annotate app demo-kind argocd.argoproj.io/refresh=hard --overwrite >/dev/null
T0=$(date +%s)
for i in $(seq 1 120); do
  PH=$(kubectl -n demo get rollout api -o jsonpath='{.status.phase}')
  ST=$(kubectl -n demo get rollout api -o jsonpath='{.status.stableRS}')
  AR=$(kubectl -n demo get analysisrun --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1:].status.phase}' 2>/dev/null)
  echo "t=$(( $(date +%s)-T0 ))s rollout=$PH analysisrun=${AR:--}"
  if [ "$PH" = "Degraded" ]; then echo "RESULT: ABORTED (Degraded)"; break; fi
  if [ "$PH" = "Healthy" ] && [ "$ST" != "$STABLE0" ]; then echo "RESULT: PROMOTED"; break; fi
  sleep 5
done
AN=$(kubectl -n demo get analysisrun --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1:].metadata.name}')
echo "== analysisrun $AN"
kubectl -n demo get analysisrun $AN -o json | python3 -c "
import sys,json
d=json.load(sys.stdin); s=d['status']
print('phase:', s['phase'])
for m in s.get('metricResults',[]):
    print('successful',m.get('successful',0),'failed',m.get('failed',0),'measurements',m.get('count',0))
    for x in m.get('measurements',[]): print('  ',x['startedAt'],x['phase'],x.get('value'))"
kubectl -n demo get pods -l app=api -L rollouts-pod-template-hash --no-headers | awk '{print $1,$2,$3,$NF}'
