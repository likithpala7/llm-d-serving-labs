#!/usr/bin/env bash
# Deploys the phase 2 PD (prefill/decode) disaggregation stack — llm-d
# router + our vendored 2-GPU vLLM manifests in ./modelserver-pd — onto
# an already-created llm-d-harness cluster with gpu-baseline scaled to 2.
#
# Runs alongside (not instead of) the phase 1 optimized-baseline stack —
# different namespace, different router release — but the two together
# need 2 concurrent GPUs just for this guide, so don't also scale up
# optimized-baseline's model server at the same time unless gpu-baseline
# is scaled to 3+ (and the quota allows it — see cluster.yaml).
#
# Requires:
#   - kubectl context pointed at llm-d-harness, gpu-baseline scaled to 2
#   - HF_TOKEN env var set to a valid HuggingFace token
#   - a sibling clone of https://github.com/llm-d/llm-d (path below),
#     kept pristine — see CLAUDE.md's llm-d specifics section for why
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LLM_D_REPO_ROOT="${LLM_D_REPO_ROOT:-$(cd "${SCRIPT_DIR}/../../llm-d" && pwd)}"
NAMESPACE="${NAMESPACE:-llm-d-pd-disaggregation}"
GUIDE_NAME=pd-disaggregation

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
# release by this name already exists
helm upgrade --install "${GUIDE_NAME}" \
  "${ROUTER_STANDALONE_CHART}" \
  -f "${LLM_D_REPO_ROOT}/guides/recipes/router/base.values.yaml" \
  -f "${LLM_D_REPO_ROOT}/guides/${GUIDE_NAME}/router/${GUIDE_NAME}.values.yaml" \
  -f "${SCRIPT_DIR}/router-resources-override.values.yaml" \
  -f "${SCRIPT_DIR}/router-pd-plugins-override.values.yaml" \
  -n "${NAMESPACE}" --version "${ROUTER_CHART_VERSION}"

echo "==> Applying vLLM prefill/decode model servers (infra/modelserver-pd)"
kubectl apply -n "${NAMESPACE}" -k "${SCRIPT_DIR}/modelserver-pd"

echo "==> Waiting for the prefill pod to become ready (can take a while: image pull + model download)"
kubectl -n "${NAMESPACE}" rollout status deployment/pd-disaggregation-nvidia-gpu-vllm-prefill --timeout=15m

echo "==> Waiting for the decode pod to become ready"
kubectl -n "${NAMESPACE}" rollout status deployment/pd-disaggregation-nvidia-gpu-vllm-decode --timeout=15m

IP=$(kubectl get service "${GUIDE_NAME}-epp" -n "${NAMESPACE}" -o jsonpath='{.spec.clusterIP}')
echo "==> Done. Router ClusterIP: ${IP}"
echo "Verify with:"
echo "  kubectl run curl-test --rm -i --restart=Never --image=cfmanteiga/alpine-bash-curl-jq:latest -n ${NAMESPACE} \\"
echo "    -- /bin/sh -c 'curl -sS -X POST http://${IP}/v1/completions -H \"Content-Type: application/json\" -d \"{\\\"model\\\": \\\"Qwen/Qwen3-8B\\\", \\\"prompt\\\": \\\"How are you today?\\\"}\"'"
echo ""
echo "Once calibrated for real (guides/recipes/router/calibration/calibrate.sh"
echo "against this namespace), update infra/router-pd-plugins-override.values.yaml's"
echo "peakPrefillThroughput and re-run: helm upgrade ${GUIDE_NAME} ... && kubectl rollout restart -n ${NAMESPACE} deployment/${GUIDE_NAME}-epp"
