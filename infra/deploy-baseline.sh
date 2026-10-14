#!/usr/bin/env bash
# Deploys the phase 1 optimized-baseline stack (llm-d router + our vendored
# single-GPU vLLM manifests in ./modelserver) onto an already-created
# llm-d-harness cluster with gpu-baseline scaled to >=1.
#
# Requires:
#   - kubectl context pointed at llm-d-harness
#   - HF_TOKEN env var set to a valid HuggingFace token
#   - a sibling clone of https://github.com/llm-d/llm-d (path below),
#     kept pristine — see CLAUDE.md's llm-d specifics section for why
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LLM_D_REPO_ROOT="${LLM_D_REPO_ROOT:-$(cd "${SCRIPT_DIR}/../../llm-d" && pwd)}"
NAMESPACE="${NAMESPACE:-llm-d-optimized-baseline}"
GUIDE_NAME=optimized-baseline

: "${HF_TOKEN:?Set HF_TOKEN to a valid HuggingFace token before running}"

# tr strips CRLF in case the llm-d checkout has Windows line endings
# (e.g. via a git core.autocrlf setting) — bash chokes sourcing those directly
source <(tr -d '\r' < "${LLM_D_REPO_ROOT}/guides/env.sh")

echo "==> Installing GAIE CRDs (${GAIE_VERSION})"
kubectl apply -f "https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/${GAIE_VERSION}/v1-manifests.yaml"

echo "==> Creating namespace ${NAMESPACE}"
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

echo "==> Creating llm-d-hf-token secret"
kubectl create secret generic llm-d-hf-token \
  --from-literal="HF_TOKEN=${HF_TOKEN}" \
  --namespace "${NAMESPACE}" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "==> Installing llm-d router (standalone mode)"
# upgrade --install, not plain install — idempotent whether or not a
# release by this name already exists (e.g. left over from a prior
# session that didn't run teardown-baseline.sh)
helm upgrade --install "${GUIDE_NAME}" \
  "${ROUTER_STANDALONE_CHART}" \
  -f "${LLM_D_REPO_ROOT}/guides/recipes/router/base.values.yaml" \
  -f "${LLM_D_REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml" \
  -f "${SCRIPT_DIR}/router-resources-override.values.yaml" \
  -f "${SCRIPT_DIR}/router-calibration-override.values.yaml" \
  -n "${NAMESPACE}" --version "${ROUTER_CHART_VERSION}"

echo "==> Applying vLLM model server (infra/modelserver)"
kubectl apply -n "${NAMESPACE}" -k "${SCRIPT_DIR}/modelserver"

echo "==> Waiting for the decode pod to become ready (can take a while: image pull + model download)"
kubectl -n "${NAMESPACE}" rollout status deployment/optimized-baseline-nvidia-gpu-vllm-decode --timeout=15m

IP=$(kubectl get service "${GUIDE_NAME}-epp" -n "${NAMESPACE}" -o jsonpath='{.spec.clusterIP}')
echo "==> Done. Router ClusterIP: ${IP}"
echo "Verify with:"
echo "  kubectl run curl-test --rm -i --restart=Never --image=cfmanteiga/alpine-bash-curl-jq:latest -n ${NAMESPACE} \\"
echo "    -- /bin/sh -c 'curl -sS -X POST http://${IP}/v1/completions -H \"Content-Type: application/json\" -d \"{\\\"model\\\": \\\"Qwen/Qwen3-8B\\\", \\\"prompt\\\": \\\"How are you today?\\\"}\"'"
