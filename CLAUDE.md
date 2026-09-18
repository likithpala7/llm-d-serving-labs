# Agent Harness on llm-d / EKS — Project Context

## Goal

Build an agent harness: an LLM inference backend (llm-d on EKS running vLLM,
with prefill/decode disaggregation across ≥2 GPUs), a sandboxed environment
for tool execution and context management, and a basic UI to use it.

Build philosophy: prioritize understanding internals and building genuinely
novel pieces over wiring together existing frameworks. This is partly a
learning project and partly a portfolio piece.

## Phased plan

1. **EKS + llm-d/vLLM baseline, single GPU** ← currently here
2. PD disaggregation across 2 GPUs (benchmark TTFT/throughput vs. baseline)
3. Sandbox + context-manager service (tool execution, context window mgmt)
4. Basic UI (chat + live tool-call/sandbox trace)

Reference target for where phases 2–3 converge: llm-d's `guides/agentic-serving/`
guide composes prefix-aware routing, KV-cache offloading, and PD disaggregation
specifically for agentic/tool-using workloads — worth revisiting once the
baseline works.

## Current status

- Local dev environment set up (Windows + WSL2/Ubuntu — see below)
- AWS account created: new account, $100 credit (6-month expiry), GPU
  (`g5` family) quota started at 0 vCPUs. First request (16 vCPUs, sized for
  phase 2's two `g5.2xlarge`) was **denied 2026-09-17** — AWS cited their
  standard new-account ramp-up policy. Case ID `178968490300487` (now
  `CASE_CLOSED` after user closed it in the Support Center console).
  Resubmitted the same day scoped to phase 1's actual need instead of
  re-appealing the 16-vCPU ask: **8-vCPU request pending** (one
  `g5.2xlarge`, request ID `84555add1b8c40b7a8ffa5db249ab6c9fmKSvUPq`) —
  small requests from new accounts are much likelier to auto-approve, and
  this builds usage history that makes the later 8→16 ask (for phase 2)
  easier. Check status:
  `aws service-quotas list-requested-service-quota-change-history-by-quota --service-code ec2 --quota-code L-DB2E81BA --region us-east-1`
- `cluster.yaml` (eksctl config) written, cluster **not yet created**.
  GPU node group is present but `desiredCapacity: 0` so cluster creation
  doesn't require GPU quota — bring up everything else now, scale GPU
  nodes on once quota clears:
  `eksctl scale nodegroup --cluster=llm-d-harness --name=gpu-baseline --nodes=1 --nodes-min=0 --nodes-max=1`

## Local environment

- Windows machine, tooling installed inside **WSL2 (Ubuntu)**, not native
  Windows — the llm-d guides are bash scripts, much less friction this way
- Installed: AWS CLI v2, eksctl, kubectl, helm, jq, git
- Docker Desktop for Windows (WSL2 integration enabled) needed once building
  own images for the sandbox/orchestrator — not needed yet for phase 1

## Infra decisions (EKS / eksctl)

- Cluster name `llm-d-harness`, region `us-east-1`, k8s `1.31`
- **System node group**: single `m5.large` (not 2× anything — this is a
  single-person dev cluster with no HA requirement; recreate on failure
  rather than provision for redundancy)
- **GPU node group** `gpu-baseline`: `g5.2xlarge` (1× A10G, 24GB VRAM) —
  chosen over p4d/p5 tier specifically for cost; fits an 8B-class model
  comfortably. Tainted `nvidia.com/gpu=true:NoSchedule`.
- `vpc.nat.gateway: Single` — eksctl's default is one NAT gateway per AZ,
  which is a real cost trap; collapsed to one.
- Full config lives in `cluster.yaml` alongside this file.

## Cost guardrails (important — read before running anything)

AWS Free Tier does **not** cover EKS control plane or GPU instances — every
hour the cluster exists it draws against the $100 credit, GPU on or off.

Known rates (us-east-1, on-demand, as of research in Sep 2026 — re-verify
if it's been a while):
- EKS control plane: $0.10/hr, **runs regardless of node/pod count**
- 1× m5.large system node: ~$0.10/hr
- 1× NAT gateway: ~$0.045/hr + data processing
- g5.2xlarge: $1.21/hr (only while GPU node group is scaled up)

Idle cluster (GPU at 0) ≈ $0.25–0.30/hr (~$6–7/day if forgotten).

**Rule: `eksctl delete cluster -f cluster.yaml` at the end of every session.**
Scaling node groups to 0 does NOT stop the control plane or NAT gateway
billing — only deleting the cluster does. Recreating from `cluster.yaml`
next session costs the same 15–20 min either way, so there's no real
downside to always tearing down.

AWS Budgets set up as a backstop (alerts at 50/80/100% of a $50 budget);
Budget Actions can auto-stop EC2 instances at a threshold but can't touch
the control plane or NAT gateway fees — don't rely on it as the primary
control.

## llm-d specifics

- Repo: `github.com/llm-d/llm-d`, docs under `guides/` (this has moved
  around — `kgateway` is deprecated in favor of `agentgateway`; the old
  `llmd-installer.sh` approach is gone in favor of per-feature "well-lit
  path" guides)
- Phase 1 guide: `guides/optimized-baseline/` — Standalone Mode (built-in
  Envoy proxy, simplest starting point, no separate Gateway implementation
  needed yet)
- GAIE (Gateway API Inference Extension) CRDs pinned via `GAIE_VERSION` —
  was `v1.5.0` at time of research; check `guides/env.sh` in the repo for
  current pin
- **Important gotcha**: the default `guides/optimized-baseline/modelserver/gpu/vllm/`
  manifests deploy 8 replicas of Qwen3-32B (TP=2) — assumes a multi-GPU
  cluster. Trimmed down for this single-GPU setup: `replicas: 1`,
  `Qwen/Qwen3-8B` (fits 24GB VRAM, ungated so no HF license-acceptance
  friction), no `--tensor-parallel-size`, resources sized for `g5.2xlarge`
  (8 vCPU/32GiB), and a toleration added for the `gpu-baseline` node
  group's taint (upstream doesn't assume a tainted GPU pool). **These
  customizations are vendored as plain manifests in `infra/modelserver/`**
  (not edited in place in the `llm-d` clone) — deploy with
  `kubectl apply -n ${NAMESPACE} -k infra/modelserver/` instead of the
  guide's own `-k .../modelserver/gpu/vllm/base/` command. The `llm-d`
  clone itself should stay unmodified/pristine — it's just the upstream
  reference for the router Helm chart and everything else the guide still
  drives directly.
- **Follow-up once benchmarking (phase 2+)**: the router's
  `peakPrefillThroughput: 15928` default (in
  `guides/optimized-baseline/router/optimized-baseline.values.yaml`) is
  calibrated for the guide's reference setup (Qwen3-32B on H100, TP=2) —
  not this Qwen3-8B/A10G setup. Not a blocker with a single replica, but
  needs recalibrating via the guide's `recipes/router/calibration`
  recipe before the saturation-aware routing behavior can be trusted for
  real benchmarking.
- Phase 2 guide: `guides/pd-disaggregation/` — KV transfer via NIXL
  (llm-d's default) or LMCache
- Gated models (e.g. Llama) need an `HF_TOKEN` secret — check that
  guide's own README for the exact secret name/key it expects

## Repo structure

**Monorepo** (not separate infra/app repos) — chosen deliberately since the
architecture is still moving; a repo split is a cheap mechanical operation
(`git filter-repo`) to do later once the interface has stabilized.

- `/infra` — `cluster.yaml`, plus all customized llm-d Helm/kustomize
  config, gateway/CRD setup. Any manifest we deviate from upstream on
  (e.g. `infra/modelserver/`) is vendored here as a standalone copy, not
  edited in place in the separate `llm-d` clone — this repo should be
  everything needed to reproduce the deployed state, independent of
  whatever's on disk in other repos.
- `/app` — sandbox/context-manager service, UI, each owning its own
  Dockerfile and K8s manifests for deploying itself onto the cluster
- Orchestrator service should run in-cluster and reach the llm-d router via
  in-cluster DNS (e.g. `http://<router-svc>.<namespace>.svc.cluster.local`)
  rather than needing router endpoint info passed in from outside

## CI/CD philosophy

- **CI: set up now.** YAML lint, `helm template` / `kustomize build` as a
  dry-run, later the app test suite and `docker build` (no push). No AWS
  credentials needed in CI yet, so no secrets-management surface.
- **CD: deliberately not set up yet.** Two reasons: (1) infra shape is
  still changing session to session, (2) auto-apply on push conflicts with
  the cost discipline above — don't want a workflow reflexively bringing up
  billable GPU infra on a push. When infra automation is eventually added,
  keep it on `workflow_dispatch` (manual trigger), not auto-on-merge.
  App-layer CD (pushing a new image to an already-running Deployment) is a
  safer later candidate since it doesn't spin up new billable infra.
