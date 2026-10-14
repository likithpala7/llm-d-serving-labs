#!/usr/bin/env bash
# End-to-end equal-compute, sustained-load comparison: 2-replica aggregated
# baseline vs. 2-GPU PD disaggregation. Handles everything itself — scaling
# gpu-baseline to 2, deploying each stack, port-forwarding, running the
# sustained benchmark, tearing down, and printing the final comparison —
# so there's nothing to run manually in between.
#
# Why this exists: the earlier light test (10-20 concurrency, 50 output
# tokens, 1 GPU vs 2 GPUs) wasn't a fair comparison on two counts —
# unequal compute budget, and a workload too light to create any real
# prefill/decode contention for PD disaggregation to relieve. This fixes
# both: baseline scaled to 2 replicas (same 2-GPU budget as PD), and a
# sustained run with longer prompts/outputs and higher concurrency.
#
# Requires:
#   - HF_TOKEN set (same secret both deploy-baseline.sh and deploy-pd.sh need)
#   - kubectl context pointed at llm-d-harness
#   - nothing else already using the GPU nodes (this script scales
#     gpu-baseline to 2 and expects to have both nodes to itself)
#
# Tunable via env vars (defaults below chosen for an 8B model on
# A10G/L4/L40S-class GPUs — not the llm-d guide's own production-scale
# reference numbers, which target a 120B model on H200s):
#   DURATION_SECONDS (90), CONCURRENCY (30), MAX_TOKENS (200),
#   PROMPT_TOKENS (3000, approximate — see benchmark-ttft.py)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLUSTER_NAME=llm-d-harness
REGION=us-east-1

DURATION_SECONDS="${DURATION_SECONDS:-90}"
CONCURRENCY="${CONCURRENCY:-30}"
MAX_TOKENS="${MAX_TOKENS:-200}"
PROMPT_TOKENS="${PROMPT_TOKENS:-3000}"

: "${HF_TOKEN:?Set HF_TOKEN to a valid HuggingFace token before running}"

RESULTS_DIR="${SCRIPT_DIR}/../benchmark-results-$(date +%Y%m%d-%H%M%S)"
mkdir -p "${RESULTS_DIR}"
echo "==> Results will be saved to ${RESULTS_DIR}"

PF_PID=""
cleanup_port_forward() {
  if [ -n "${PF_PID}" ] && kill -0 "${PF_PID}" 2>/dev/null; then
    kill "${PF_PID}" 2>/dev/null || true
    wait "${PF_PID}" 2>/dev/null || true
  fi
  PF_PID=""
}
trap cleanup_port_forward EXIT

wait_for_gpu_nodes() {
  local want="$1"
  echo "==> Waiting for ${want} Ready GPU node(s)..."
  for i in $(seq 1 90); do
    ready=$(kubectl get nodes -l role=gpu --no-headers 2>/dev/null | grep -c ' Ready ' || true)
    if [ "${ready}" -ge "${want}" ]; then
      echo "    ${ready} GPU node(s) Ready"
      return 0
    fi
    sleep 10
  done
  echo "ERROR: timed out waiting for ${want} GPU node(s)" >&2
  exit 1
}

port_forward_and_benchmark() {
  local namespace="$1" service="$2" label="$3" json_out="$4"
  echo "==> Port-forwarding ${service}.${namespace}..."
  kubectl port-forward -n "${namespace}" "svc/${service}" 8080:80 >/dev/null 2>&1 &
  PF_PID=$!
  for i in $(seq 1 30); do
    if curl -sS -f -o /dev/null -X POST http://localhost:8080/v1/completions \
        -H "Content-Type: application/json" \
        -d '{"model":"Qwen/Qwen3-8B","prompt":"hi","max_tokens":1}' 2>/dev/null; then
      break
    fi
    sleep 2
  done
  echo "==> Running sustained benchmark against ${label} (${DURATION_SECONDS}s, concurrency ${CONCURRENCY})..."
  python3 "${SCRIPT_DIR}/benchmark-ttft.py" \
    --duration "${DURATION_SECONDS}" \
    --concurrency "${CONCURRENCY}" \
    --max-tokens "${MAX_TOKENS}" \
    --prompt-tokens "${PROMPT_TOKENS}" \
    --label "${label}" \
    --json-out "${json_out}" \
    | tee "${RESULTS_DIR}/${label}.log"
  cleanup_port_forward
}

echo "==> Scaling gpu-baseline to 2 nodes"
eksctl scale nodegroup --cluster="${CLUSTER_NAME}" --name=gpu-baseline --nodes=2 --nodes-min=0 --nodes-max=2 --region "${REGION}"
wait_for_gpu_nodes 2

echo ""
echo "=== Phase 1: baseline (2 replicas, equal compute budget to PD) ==="
bash "${SCRIPT_DIR}/deploy-baseline.sh"
echo "==> Scaling baseline decode deployment to 2 replicas"
kubectl scale deployment/optimized-baseline-nvidia-gpu-vllm-decode -n llm-d-optimized-baseline --replicas=2
kubectl rollout status deployment/optimized-baseline-nvidia-gpu-vllm-decode -n llm-d-optimized-baseline --timeout=15m

port_forward_and_benchmark llm-d-optimized-baseline optimized-baseline-epp "baseline-2gpu" "${RESULTS_DIR}/baseline-2gpu.json"

echo "==> Tearing down baseline"
bash "${SCRIPT_DIR}/teardown-baseline.sh"

echo ""
echo "=== Phase 2: PD disaggregation (1 prefill + 1 decode, 2 GPUs) ==="
bash "${SCRIPT_DIR}/deploy-pd.sh"

port_forward_and_benchmark llm-d-pd-disaggregation pd-disaggregation-epp "pd-disaggregation" "${RESULTS_DIR}/pd-disaggregation.json"

echo "==> Tearing down PD disaggregation"
bash "${SCRIPT_DIR}/teardown-pd.sh"

echo ""
echo "=== Comparison ==="
python3 "${SCRIPT_DIR}/compare-results.py" "${RESULTS_DIR}/baseline-2gpu.json" "${RESULTS_DIR}/pd-disaggregation.json" | tee "${RESULTS_DIR}/comparison.txt"

echo ""
echo "==> Done. Full results in ${RESULTS_DIR}"
echo "    Remember: this script did NOT delete the cluster. Run"
echo "    'eksctl delete cluster -f infra/cluster.yaml' when you're done for the session."
