#!/usr/bin/env bash
# Removes the app-layer stack deployed by deploy-baseline.sh, leaving the
# EKS cluster itself running (use for iterating on the deployment without
# tearing down/recreating the whole cluster).
#
# Remember: this does NOT stop cluster/NAT-gateway billing. Per CLAUDE.md's
# cost guardrails, end every session with:
#   eksctl delete cluster -f infra/cluster.yaml
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAMESPACE="${NAMESPACE:-llm-d-optimized-baseline}"
GUIDE_NAME=optimized-baseline

helm uninstall "${GUIDE_NAME}" -n "${NAMESPACE}" || true
kubectl delete -n "${NAMESPACE}" -k "${SCRIPT_DIR}/modelserver" --ignore-not-found=true
kubectl delete namespace "${NAMESPACE}" --ignore-not-found=true
