#!/bin/bash
# Safe teardown: remove cluster-created AWS resources (EBS volumes) first, then destroy Terraform, then verify nothing bills.
set -uo pipefail
cd "$(dirname "$0")/.."
REGION=eu-central-1; CLUSTER=platform-dev

echo "== 1. stop Argo CD from recreating things"
kubectl -n argocd scale deploy/argocd-applicationset-controller deploy/argocd-server --replicas=0 2>/dev/null
kubectl -n argocd scale statefulset/argocd-application-controller --replicas=0 2>/dev/null
kubectl -n argocd delete applications --all --wait=false 2>/dev/null

echo "== 2. delete workloads, PVCs and any LoadBalancer services"
for ns in demo monitoring loki velero; do
  kubectl delete ns "$ns" --wait=false 2>/dev/null
done
kubectl delete svc -A -l '!kubernetes.io/cluster-service' --field-selector spec.type=LoadBalancer 2>/dev/null
echo "waiting for PVs to disappear (max 5 min)..."
for i in $(seq 1 30); do
  n=$(kubectl get pv --no-headers 2>/dev/null | wc -l); [ "$n" -eq 0 ] && break; sleep 10
done
kubectl get pv 2>/dev/null

echo "== 3. terraform destroy (dev only; shared stack and Velero bucket stay)"
( cd infra/envs/dev && terraform destroy -auto-approve )

echo "== 4. leftover checks (every section should be empty)"
echo "-- clusters:";      aws eks list-clusters --region $REGION --query clusters --output text
echo "-- NAT gateways:";  aws ec2 describe-nat-gateways --region $REGION --filter Name=state,Values=available,pending --query 'NatGateways[].NatGatewayId' --output text
echo "-- elastic IPs:";   aws ec2 describe-addresses --region $REGION --query 'Addresses[].PublicIp' --output text
echo "-- EBS volumes:";   aws ec2 describe-volumes --region $REGION --query 'Volumes[].[VolumeId,State,Size]' --output text
echo "-- load balancers:"; aws elbv2 describe-load-balancers --region $REGION --query 'LoadBalancers[].LoadBalancerName' --output text
echo "-- instances:";     aws ec2 describe-instances --region $REGION --filters Name=instance-state-name,Values=running,pending --query 'Reservations[].Instances[].InstanceId' --output text
