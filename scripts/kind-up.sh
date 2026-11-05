#!/bin/bash
# Local stack for evaluating the investigator: Argo CD, Rollouts, monitoring, Loki, Alloy, the demo
# app and ops-agent. Argo CD follows the Git branch given in BRANCH, so push that branch first.
# Discord gets a dummy URL unless DISCORD_WEBHOOK_URL is set, and the LLM stays off unless
# ANTHROPIC_API_KEY is set. It never proposes a revert unless GITHUB_APP_KEY_FILE is set. Remove it with: kind delete cluster --name ops-eval
set -euo pipefail
cd "$(dirname "$0")/.."
CLUSTER="${CLUSTER:-ops-eval}"; BRANCH="${BRANCH:-main}"

for service in api worker frontend; do
  docker build -q -t "platform/$service:local" "app/$service" >/dev/null
done
docker build -q -f app/ops-agent/Dockerfile -t platform/ops-agent:local . >/dev/null
kind create cluster --name "$CLUSTER" --wait 180s
for service in api worker frontend ops-agent; do
  kind load docker-image "platform/$service:local" --name "$CLUSTER" >/dev/null 2>&1
done

for namespace in argocd argo-rollouts monitoring ops-agent demo; do
  kubectl create namespace "$namespace"
done
kubectl apply -n argocd --server-side --force-conflicts -f https://raw.githubusercontent.com/argoproj/argo-cd/v3.5.6/manifests/install.yaml >/dev/null
kubectl apply -n argo-rollouts --server-side --force-conflicts -f https://github.com/argoproj/argo-rollouts/releases/download/v1.10.1/install.yaml >/dev/null
kubectl -n argocd rollout status deploy/argocd-server --timeout=300s
kubectl -n argo-rollouts rollout status deploy/argo-rollouts --timeout=300s

# Secrets live only in the cluster. Alertmanager will not start without its copy of the token.
TOKEN=$(openssl rand -hex 32)
kubectl -n ops-agent  create secret generic ops-agent-webhook --from-literal=token="$TOKEN"
kubectl -n monitoring create secret generic ops-agent-webhook --from-literal=token="$TOKEN"
kubectl -n monitoring create secret generic discord-webhook \
  --from-literal=url="${DISCORD_WEBHOOK_URL:-http://127.0.0.1:9/no-discord}"
if [ -n "${DISCORD_WEBHOOK_URL:-}" ]; then
  kubectl -n ops-agent create secret generic ops-agent-discord --from-literal=url="$DISCORD_WEBHOOK_URL"
fi

if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
  kubectl -n ops-agent create secret generic ops-agent-llm --from-literal=api-key="$ANTHROPIC_API_KEY"
fi

if [ -n "${GITHUB_APP_KEY_FILE:-}" ]; then
  kubectl -n ops-agent create secret generic ops-agent-github-app \
    --from-file=private-key.pem="$GITHUB_APP_KEY_FILE"
fi

for app in monitoring monitoring-config loki alloy demo-kind ops-agent-kind; do
  sed "s#targetRevision: main#targetRevision: $BRANCH#" "gitops/argocd/$app.yaml" | kubectl apply -f -
done
echo "Applied. Loki can take about 7 minutes to become ready; wait for: kubectl -n argocd get applications"
