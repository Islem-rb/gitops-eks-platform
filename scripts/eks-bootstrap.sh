#!/bin/bash
# One-command bootstrap after `terraform apply` in infra/envs/dev. Installs Argo CD and Argo Rollouts
# (versions pinned to what is proven on kind), then applies the Applications in dependency order.
set -euo pipefail
cd "$(dirname "$0")/.."
CLUSTER=platform-dev; REGION=eu-central-1

aws eks update-kubeconfig --name "$CLUSTER" --region "$REGION"
kubectl wait --for=condition=Ready nodes --all --timeout=600s
kubectl get nodes

# the legacy gp2 class must stop being the default (gp3 via the EBS CSI driver replaces it)
kubectl patch storageclass gp2 -p '{"metadata":{"annotations":{"storageclass.kubernetes.io/is-default-class":"false"}}}' || true

kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n argocd --server-side --force-conflicts -f https://raw.githubusercontent.com/argoproj/argo-cd/v3.5.6/manifests/install.yaml
kubectl create namespace argo-rollouts --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n argo-rollouts --server-side --force-conflicts -f https://github.com/argoproj/argo-rollouts/releases/download/v1.10.1/install.yaml
kubectl -n argocd rollout status deploy/argocd-server --timeout=300s
kubectl -n argo-rollouts rollout status deploy/argo-rollouts --timeout=300s

for f in gitops/argocd/eks/external-secrets.yaml gitops/argocd/eks/platform-eks.yaml gitops/argocd/eks/velero.yaml \
         gitops/argocd/monitoring.yaml gitops/argocd/monitoring-config.yaml gitops/argocd/loki.yaml gitops/argocd/alloy.yaml \
         gitops/argocd/demo-eks.yaml; do
  kubectl apply -f "$f"
done
echo; echo "Argo CD admin password:"
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d; echo
kubectl -n argocd get applications
