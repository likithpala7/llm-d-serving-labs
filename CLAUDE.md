# Agent Harness on llm-d / EKS — Project Context

## Goal

Build an agent harness: an LLM inference backend (llm-d on EKS running vLLM,
with prefill/decode disaggregation across ≥2 GPUs), a sandboxed environment
for tool execution and context management, and a basic UI to use it.

Build philosophy: prioritize understanding internals and building genuinely
novel pieces over wiring together existing frameworks. This is partly a
learning project and partly a portfolio piece.

## Phased plan

1. **EKS + llm-d/vLLM baseline, single GPU** — working end-to-end as of
   2026-09-30 (see Current Status below); teardown/recreate is routine now
2. PD disaggregation across 2 GPUs (benchmark TTFT/throughput vs. baseline) ← currently here
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
  easier. **This 8-vCPU request was also denied (2026-09-18)** — appealed
  with a detailed use-case writeup via the Support Center console (case
  `178969720900083`). **This appeal was approved (~2026-09-27): the g5/G-VT
  quota (`L-DB2E81BA`) is now 8 vCPUs**, confirmed via
  `aws service-quotas get-service-quota --service-code ec2 --quota-code L-DB2E81BA --region us-east-1`.
- **Bigger finding (2026-09-18, confirmed still unresolved 2026-09-28
  after the quota fix above): the account-level restriction isn't
  G-family/GPU-specific, and getting the G/VT quota approved did NOT fix
  it.** Created the cluster with the GPU node group at `desiredCapacity: 0`
  (as designed, to avoid needing GPU quota) — the plain `m5.large`
  **system** node group still failed to launch any instance, repeatedly,
  with `InvalidParameterCombination - The specified instance type is not
  eligible for Free Tier`. Confirmed via
  `aws autoscaling describe-scaling-activities --auto-scaling-group-name <system nodegroup ASG>`.
  **This account currently cannot launch any non-free-tier EC2 instance
  at all**, not just GPU ones. This is a *separate* restriction from the
  G/VT Service Quotas line item above — that quota is now fixed, but this
  free-tier-only gate is a different account-verification/trust hold that
  the quota appeal never actually addressed (it went through the Service
  Quotas request flow; this needs its own support case, worded around
  "account restricted to Free Tier-eligible EC2 instances only," not a
  quota increase).
  **RESOLVED 2026-09-30**: not a fraud/trust review at all — it's AWS's
  account-plan system. A support case on this specific issue confirmed the
  account was on `accountPlanType: FREE`, which hard-restricts EC2 to
  Free Tier-eligible instance types by design until upgraded. Fixed with
  `aws freetier upgrade-account-plan --account-plan-type PAID` (confirmed
  via `aws freetier get-account-plan-state` before/after). **This is
  permanent/irreversible** — no downgrade back to Free — but the $99.08
  remaining promotional credit carried over unaffected. Post-upgrade,
  normal pay-as-you-go billing applies to anything beyond the credit/Always
  Free limits — i.e. the cost guardrails below are no longer a "the
  cluster won't even launch" backstop, they're the *only* backstop now,
  so the teardown discipline actually matters for real starting now.
  The cluster has been deleted both times this was hit (nothing could run
  on it, no reason to keep paying the ~$0.10/hr control plane cost) —
  recreate with `eksctl create cluster -f infra/cluster.yaml` once this
  restriction lifts. Note: last time, the background `eksctl create
  cluster`/`create nodegroup` process got silently interrupted mid-run
  (likely a WSL/session hiccup) — always verify with
  `eksctl get nodegroup --cluster llm-d-harness --region us-east-1` (not
  just `eksctl get cluster`) before assuming creation actually finished.
- **Stuck deletion (2026-09-28), resolved.** After hitting the free-tier
  restriction again, deleted the cluster — `gpu-baseline`'s stack deleted
  fine, but the `system` nodegroup got stuck in `DELETING` (both via
  `aws eks describe-nodegroup` and its CloudFormation stack) for 40+
  minutes with zero progress (`modifiedAt` frozen, no `health.issues`, no
  `DELETE_FAILED` event); `eksctl delete cluster`'s own waiter gave up
  after ~25 min (`exceeded max wait time for StackDeleteComplete waiter`),
  same pattern as the create-side timeout. Scaling the stuck ASG's
  capacity to 0 directly didn't help, and reissuing `aws eks
  delete-nodegroup` returned the same stuck state with no new error.
  **What actually fixed it: force-deleting the underlying ASG directly**
  (`aws autoscaling delete-auto-scaling-group --force-delete
  --auto-scaling-group-name <eks-system-* ASG>`) — this apparently was
  what EKS's own deletion workflow was silently waiting on; the nodegroup
  and its stack both cleared within ~2-3 minutes afterward. No real EC2
  instances ever ran in that nodegroup (all launches failed), so none of
  this accumulated compute cost — it was purely a stuck API workflow.
  **Takeaway for next time this happens**: don't wait indefinitely on a
  nodegroup stuck in `DELETING` with no error surfaced — find its backing
  ASG (`aws autoscaling describe-auto-scaling-groups`, name pattern
  `eks-<nodegroup-name>-<uuid>`) and force-delete it directly; that's a
  faster, effective unstick even without a clear root-cause error message.
  Cluster is now fully deleted and confirmed clean (no NAT gateway, VPC,
  ASGs, or instances remain) — recreate with `eksctl create cluster -f
  infra/cluster.yaml` whenever the free-tier account restriction above
  actually lifts (not yet confirmed — check before spending time on a
  fresh create attempt).
- **g5.2xlarge capacity saga (2026-09-30), resolved — cluster is up with a
  real GPU node.** After both account-level blockers above were actually
  cleared, `eksctl create cluster` still failed to launch the `gpu-baseline`
  GPU node with `InsufficientInstanceCapacity` — AWS genuinely out of
  physical `g5.2xlarge` inventory, unrelated to our account. This rotated
  unpredictably across AZs (`us-east-1f`/`b` failed, then after pinning to
  `a`/`c`/`d` those failed too, then after widening to all 6 AZs `b`/`c`/
  `d`/`f` all failed repeatedly while `a` was suspiciously never attempted).
  Also hit along the way: **`us-east-1e` isn't valid for the EKS control
  plane at all** (`"EKS does not support creating control plane instances
  in us-east-1e"` — hard error, not a capacity issue; dropped it from
  `availabilityZones`, kept the other 5: a/b/c/d/f). To test whether `a`
  really had capacity, temporarily restricted the ASG to just that one
  subnet with `aws autoscaling update-auto-scaling-group
  --vpc-zone-identifier <subnet-in-us-east-1a-only>` — **this worked**, the
  GPU node came up within ~30s. Restored the ASG back to all 5 subnets
  afterward for future resilience. **Takeaway**: don't pin `availabilityZones`
  to a fixed subset for a scarce instance type like g5 — use every AZ the
  EKS control plane actually supports (all of us-east-1 except `us-east-1e`)
  so the ASG has the most chances of landing wherever capacity currently is;
  if it still won't land anywhere, temporarily narrowing the ASG's
  `--vpc-zone-identifier` to one specific AZ is a fast way to test/force
  that AZ specifically without touching the rest of the cluster.
  `cluster.yaml`'s `availabilityZones` now reflects the final 5-AZ list.
- **PHASE 1 WORKING END-TO-END (2026-09-30).** `llm-d-harness` cluster
  `ACTIVE`, both node groups `ACTIVE` (`system` 1x `m5.large`,
  `gpu-baseline` 1x `g5.2xlarge` with `nvidia.com/gpu: 1` allocatable).
  Ran `infra/deploy-baseline.sh` successfully — router (`epp` pod `2/2
  Running`) and the vLLM decode pod (`1/1 Running`, `Qwen/Qwen3-8B`)
  both healthy. Verified with a real inference request through the
  router (`kubectl run curl-test ... -- curl http://<epp-svc-clusterip>/v1/completions`)
  — got back a genuine completion. Two more bugs found and fixed getting
  here (both already vendored, not upstream issues):
  1. Model's default context (40960 tokens) needs more KV cache than fits
     on the A10G after model weights — capped with `--max-model-len=8192`
     in `infra/modelserver/decode-deployment.yaml`.
  2. The default `RollingUpdate` deployment strategy deadlocks for any
     single-replica GPU workload on a single-GPU node (new pod can't
     schedule while old pod holds the only GPU, old pod won't die until
     new one's ready) — switched to `strategy: {type: Recreate}` in the
     same file. Worth remembering for phase 2's prefill/decode pods too.
  Remember the cost guardrails above are real now (paid plan) — don't
  forget `eksctl delete cluster -f infra/cluster.yaml` at end of session.
  To recreate from scratch later: `eksctl create cluster -f
  infra/cluster.yaml` (creates `gpu-baseline` at `desiredCapacity: 0`,
  no GPU quota/capacity needed for that step), then
  `eksctl scale nodegroup --cluster=llm-d-harness --name=gpu-baseline --nodes=1 --nodes-min=0 --nodes-max=1`
  to bring the GPU node up (may need a few retries/minutes if capacity is
  tight — see saga above), then `bash infra/deploy-baseline.sh` (needs
  `HF_TOKEN` set — run this one yourself, not through an AI assistant,
  since it's a secret).
- **Cluster torn down (2026-10-01), confirmed fully clean.** Ended the
  session by fully deleting `llm-d-harness` (both node groups + control
  plane + VPC + NAT gateway) rather than just scaling the GPU to 0 —
  verified nothing billable remains (`aws eks describe-cluster` → not
  found, no CloudFormation stacks, no VPC, no NAT gateway, no ASGs, no
  running instances). Hit the by-now-familiar `eksctl` silent-stall
  pattern once again right after the node groups cleared (background
  process stopped polling without erroring or completing) — fixed the
  same way as before: reissue `eksctl delete cluster -f infra/cluster.yaml`
  directly rather than waiting on the stalled process. **To resume next
  session**: `eksctl create cluster -f infra/cluster.yaml`, then scale
  `gpu-baseline` to 1, then `bash infra/deploy-baseline.sh` (or, once the
  GPU is up, trigger the `.github/workflows/deploy.yml` CD workflow instead
  for config-only changes — it still needs its first real end-to-end run
  with the GPU actually up, see the CI/CD section below).
- **`gpu-baseline` diversified across 3 instance types (2026-10-01).**
  After `g5.2xlarge` alone hit `InsufficientInstanceCapacity` across
  every AZ for 90+ minutes straight on a fresh recreate, switched
  `cluster.yaml`'s `gpu-baseline` from a single `instanceType` to
  `instanceTypes: ["g5.2xlarge", "g6.2xlarge", "g6e.2xlarge"]` — EKS
  managed node groups support multiple `instanceTypes` for `ON_DEMAND`
  capacity, not just `SPOT` (confirmed via the AWS `CreateNodegroup` API
  reference; most docs/examples only show it for Spot, but nothing
  restricts it). All three are 8 vCPU/single-GPU, so **no new quota
  needed** — same `L-DB2E81BA` limit covers the whole G-family. Three
  independent hardware pools (A10G/L4/L40S) instead of one, for more
  chances of finding capacity. Deliberately excluded `g4dn.2xlarge`
  (T4, 16GB VRAM — too tight for Qwen3-8B's ~15.27GB bf16 weights alone).
  Note: `amiFamily: AmazonLinux2023` + mixed NVIDIA types resolves to one
  shared `AL2023_x86_64_NVIDIA` AMI type automatically; eksctl also
  auto-excludes any AZ that doesn't support one of the listed types
  (e.g. dropped `us-east-1f` here, since it doesn't support `g6e.2xlarge`)
  rather than failing outright. **Changing `instanceTypes` on an existing
  node group requires delete+recreate of just that node group** (not the
  whole cluster, and not an in-place update) —
  `eksctl delete nodegroup --cluster=llm-d-harness --name=gpu-baseline`
  then `eksctl create nodegroup -f infra/cluster.yaml`. One wrinkle hit
  along the way: immediately re-running `create nodegroup` right after a
  delete can spuriously report the node group as "already existing" (an
  eksctl-side eventual-consistency check, not a real conflict) — it
  resolved on a simple retry a few seconds later. First successful launch
  after this change landed on `g5.2xlarge` again, so this particular
  instance may have just been capacity clearing up over time rather than
  proof the diversification was the deciding factor — but the config is
  more resilient either way for next time.
- **End of session (2026-10-01): full teardown confirmed, nothing
  billable running.** `eksctl delete cluster -f infra/cluster.yaml` —
  hit the by-now-routine silent-stall pattern once more right after the
  node groups cleared, fixed the same way as every other time
  (reissue the delete command). Verified clean: no cluster, no
  CloudFormation stacks, no VPC, no NAT gateway, no ASGs, no running
  instances. Session summary since the last status note above:
  - CD workflow (`deploy.yml`) fully validated end-to-end for real —
    not just the pre-flight check anymore. Found and fixed 3 more
    issues getting there: (1) the EKS Access Entry doesn't survive a
    cluster delete+recreate even though the IAM role/OIDC provider do;
    (2) `AmazonEKSEditPolicy` excludes `rbac.authorization.k8s.io`
    objects on purpose (privilege-escalation guard) but the router
    chart owns a `Role` that `helm upgrade` needs to read; (3) it also
    has no way to know about the GAIE CRD's `inference.networking.k8s.io`
    API group. All three fixed with narrowly-scoped, namespace-level
    grants — see the CI/CD section below for specifics.
  - `peakPrefillThroughput` recalibrated for real hardware (3682, L4) —
    see the llm-d specifics section below.
  - AWS Budget actually created (it was claimed but never real before).
  - `gpu-baseline` diversified across 3 GPU instance types after a
    sustained 90+ min capacity shortage on `g5.2xlarge` alone.
  - Confirmed empirically (deliberately tried scaling to 2 GPUs): the
    8-vCPU quota has zero headroom for a second concurrent GPU —
    fails immediately with `VcpuLimitExceeded`, not a capacity error.
    Submitted the follow-up 16-vCPU request for phase 2 (see llm-d
    specifics section) — check its status before spending time on
    phase 2's actual 2-GPU setup.
  **To resume**: `eksctl create cluster -f infra/cluster.yaml`, scale
  `gpu-baseline` to 1, then either `bash infra/deploy-baseline.sh` or
  (once that's done once) trigger `.github/workflows/deploy.yml` for
  any config-only changes. Remember to redo the EKS Access Entry +
  both RBAC grants if the cluster was fully recreated (see CI/CD
  section) — those don't survive a cluster delete, only the IAM-side
  pieces do.

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

**AWS Budget actually set up now (2026-10-01)** — this was previously just
claimed here without ever being done (discovered via `aws budgets
describe-budgets` coming back empty). `agent-harness-monthly`, $50/month,
email alerts to likithpala7@gmail.com at 50/80/100% actual spend —
config vendored in `infra/budget.json` /
`infra/budget-notifications.json`, created via `aws budgets
create-budget`. Note: `CalculatedSpend` showed `$0.0` immediately after
creation despite real usage that session — AWS Budgets has the same
billing-data lag Cost Explorer does, so don't read a low number right
after creation as proof of low spend. **This is alerts only** — Budget
Actions (the separate feature that can auto-stop EC2 instances at a
threshold) is NOT configured, and still couldn't touch the control
plane or NAT gateway fees even if it were — don't rely on this budget
as the primary cost control, `eksctl delete cluster` still is.

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
- **Same oversizing problem on the router side (2026-09-30)**: the guide's
  `recipes/router/base.values.yaml` sets `router.proxy.resources` (envoy
  sidecar) AND `router.epp.resources` (epp container) to 4 CPU/8Gi request,
  16Gi limit **each** — two separate keys, both sized for a production
  node pool, not our single `m5.large` `system` node (2 vCPU/8GiB total).
  The `epp` pod sat `Pending` forever (`Insufficient cpu, Insufficient
  memory`) until both were overridden down. Fix vendored in
  `infra/router-resources-override.values.yaml` (250m CPU/512Mi request,
  1Gi limit each) and layered on as the last `-f` in `deploy-baseline.sh`'s
  `helm install`/`upgrade` — same "vendor the override, don't edit the
  llm-d clone" pattern as the vLLM manifests above.
- **`peakPrefillThroughput` recalibrated (2026-10-02).** The guide's
  default (`15928`, for Qwen3-32B on H100 TP=2) didn't match this setup.
  Measured via `guides/recipes/router/calibration/calibrate.sh`:
  `NAMESPACE=llm-d-optimized-baseline MODEL_NAME=Qwen/Qwen3-8B
  CHUNK_SIZE=2048` (not the recipe's `CHUNK_SIZE=8192` default — that has
  to match vLLM's actual resolved `max_num_batched_tokens`, which for our
  `--max-model-len=10000` config is `2048`, confirmed via `EngineArgs(...)
  .create_engine_config().scheduler_config.max_num_batched_tokens` run
  inside the pod rather than assumed — vLLM computes this dynamically,
  it's not a fixed default, and the full startup log line it's normally
  printed in gets log-rotated out within minutes). **Result: 3682
  tokens/sec, measured on a `g6.2xlarge` (L4).** Applied via a third
  override file, `infra/router-calibration-override.values.yaml`
  (the plugins config is one YAML-as-string value in Helm's eyes, not a
  mergeable map, so the override has to carry the whole
  `optimized-baseline-plugins.yaml` content, not just the one line) —
  now layered into both `deploy-baseline.sh` and `deploy.yml`.
  **This value is hardware-specific and `gpu-baseline` now diversifies
  across 3 GPU types (A10G/L4/L40S)** — if a future recreate lands on a
  different one, re-run `calibrate.sh` and update this file; 3682 is
  only valid for the L4. Also discovered while fixing this: the EPP
  **does not hot-reload** its plugins ConfigMap — a plain `helm upgrade`
  updates the ConfigMap's content but the running pod keeps its
  in-memory config until restarted. `deploy.yml` now does
  `kubectl rollout restart deployment/<guide>-epp` right after every
  `helm upgrade`, so this is no longer something to remember to do by
  hand.
- **Phase 2 prep: second quota increase requested (2026-10-02).** The
  G/VT quota (`L-DB2E81BA`) is a single account-wide ceiling on total
  vCPUs across all *simultaneously*-running G-family instances, not a
  per-node-group limit. Every GPU type we use is 8 vCPUs, and
  `gpu-baseline` already runs right at the current 8-vCPU ceiling with
  one GPU up — zero headroom. PD disaggregation needs prefill and decode
  on two GPUs *concurrently* (16 vCPUs), so this was always going to be
  needed — it's literally the other half of the original 16-vCPU ask
  from the very first (denied) request, which got split into 8+8 after
  that denial. Submitted a new request for 16 total: request ID
  `930342003d63424f8801cf1f991c7eed3Z0GJJjt`, status `PENDING`. Check:
  `aws service-quotas list-requested-service-quota-change-history-by-quota --service-code ec2 --quota-code L-DB2E81BA --region us-east-1`.
  No other phase-2 work is blocked on this landing — can still read the
  `pd-disaggregation` guide and update `cluster.yaml`/manifests while it's
  in flight; only actually scaling to 2 concurrent GPUs needs it approved.
  **DENIED 2026-10-13 — and this time it's a hard, structural denial, not
  a "retry with more justification" one.** AWS Billing support (case
  `179089988700169`) said the internal service team can't approve this
  one at all: *"quota increases for [G-family] instances are facilitated
  by AWS sales and need[] to be submitted through them"* — i.e. this
  specific quota, past some threshold, is gated to the Sales/account-
  manager channel rather than self-service Service Quotas, full stop.
  Pushed back once (noting the 8-vCPU request alone took ~10 days
  including a denial+appeal, and this one had already been open a
  week) — got a polite but firm "out of my hands, this is Sales-gated
  policy" response pointing to AWS Sales contact channels (live chat /
  phone / contact-us form) to get an account manager assigned.
  **Workaround found: switch to the `.xlarge` (not `.2xlarge`) variants
  of the same GPU types.** `g5.xlarge` / `g6.xlarge` / `g6e.xlarge` are
  **4 vCPUs each** (confirmed via `aws ec2 describe-instance-types`) —
  same GPU model and VRAM as their `.2xlarge` counterparts (A10G/L4
  22.9GB, L40S 45.8GB), just half the vCPU/system-RAM allocation. Two of
  these running concurrently = 8 vCPUs total, fully covered by the
  **already-approved** quota — no Sales conversation needed at all.
  **Implemented 2026-10-13.** `cluster.yaml`'s `gpu-baseline` switched
  to `instanceTypes: ["g5.xlarge", "g6.xlarge", "g6e.xlarge"]` with
  `maxSize: 2` (was `.2xlarge`/`maxSize: 1`). `infra/modelserver/decode-
  deployment.yaml`'s resource requests retuned down to fit 4 vCPU/16GiB
  per node (`limits: cpu: 3.5, memory: 14Gi`; `requests: cpu: 3, memory:
  12Gi` — was 7/28 and 6/24 for the old 8 vCPU/32GiB `.2xlarge` nodes).
  GPU VRAM itself is unaffected (same model/amount as before), so
  `--max-model-len` and the model choice didn't need to change — this
  was purely a cpu/memory retune. **Not yet done**: this only brings up
  capacity for a second concurrent GPU node — it does not yet implement
  PD disaggregation itself (separate prefill/decode deployments, KV
  transfer). That's the next real step, via the `guides/pd-
  disaggregation/` guide below.
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
- **CD: app-layer config redeploy set up (2026-09-30), scoped deliberately
  narrow.** `.github/workflows/deploy.yml`, `workflow_dispatch` only — no
  auto-trigger on push, both because infra shape is still changing and
  because auto-apply would conflict with the cost discipline above (the
  cluster usually won't even exist between sessions). Scope: config/manifest
  changes to an **already-running** cluster only — model name,
  `--max-model-len`, resource overrides, router Helm values. It does
  `kubectl apply -k infra/modelserver` + `helm upgrade` for the router, then
  waits for rollout. It deliberately does **not** touch GAIE CRDs, namespace
  creation, or the `HF_TOKEN` secret (those are one-time `deploy-baseline.sh`
  concerns) — and *can't*, even if asked to: the IAM role's EKS access policy
  is namespace-scoped (see below), not cluster-scoped. A pre-flight step
  fails fast with a clear message if the GPU node isn't up, rather than
  timing out confusingly. Still out of scope: building/pushing custom
  container images — this only re-applies existing config, nothing builds
  new image artifacts yet.
  - **Auth**: GitHub OIDC federation (not static AWS keys) — a GitHub
    Actions-specific OIDC provider was created in IAM
    (`token.actions.githubusercontent.com`, thumbprint verified live against
    Let's Encrypt's current ISRG Root X1, not a hardcoded value — GitHub
    switched from DigiCert to Let's Encrypt at some point, so old thumbprint
    guides floating around are stale). Role: `github-actions-agent-harness-deploy`,
    trust policy restricted to one specific repo/branch — but **GitHub's
    actual OIDC `sub` claim format isn't the classic documented
    `repo:OWNER/REPO:ref:refs/heads/BRANCH`** — it now embeds immutable
    numeric IDs: `repo:likithpala7@58575772/agent-harness@1375277757:ref:refs/heads/main`
    (user ID after `@` on the owner, repo ID after `@` on the repo name —
    presumably to stop impersonation via repo delete+recreate or rename).
    First attempt used the old format and failed every retry with
    `Not authorized to perform sts:AssumeRoleWithWebIdentity`; confirmed the
    real value by temporarily adding a workflow step that fetches and
    base64-decodes the actual OIDC JWT payload rather than guessing further
    — see `infra/github-actions-trust-policy.json` for the corrected policy.
    If this needs recreating later (new repo, transferred ownership, etc.),
    decode a real token the same way rather than assuming the classic format
    is still accurate.
    IAM permissions: just `eks:DescribeCluster` on this one cluster (see
    `infra/github-actions-permissions-policy.json`) — actual Kubernetes
    access comes from an EKS Access Entry mapping that role to
    `AmazonEKSEditPolicy`, scoped via `accessScope` to just the
    `llm-d-optimized-baseline` namespace (not cluster-admin, can't touch
    other namespaces or cluster-scoped resources like CRDs/RBAC). No
    `HF_TOKEN` or other secrets live in GitHub Actions at all — config-only
    redeploys never touch it.
  - Recreating this setup from scratch (e.g. after a full account/cluster
    rebuild) needs, in order: `aws iam create-open-id-connect-provider` (OIDC
    provider), `aws iam create-role` + `put-role-policy` (the role + its two
    policy files above), `aws eks create-access-entry` +
    `associate-access-policy` (the actual RBAC grant — this last command
    needs explicit user confirmation/manual run, it's the step that grants
    real cluster access and gets flagged by permission classifiers).
  - **Confirmed working (2026-10-01) up through the pre-flight check**:
    OIDC auth → IAM role assumption → `aws eks update-kubeconfig` → kubectl
    connectivity all succeeded; the pre-flight step correctly failed with
    its intended clear message since the GPU node was down at the time
    (post-break scale-down). The `kubectl apply`/`helm upgrade`/rollout
    steps are unverified until run once with the GPU actually up.
  - **Found and fixed (2026-10-01): the EKS Access Entry does NOT survive
    a cluster delete+recreate**, even though the IAM OIDC provider and
    role (account-level, cluster-independent) do. After recreating
    `llm-d-harness` from scratch, the CD workflow's pre-flight check
    failed claiming no GPU node, even with a real `Ready` GPU node up —
    `aws eks describe-access-entry` for the role came back
    `ResourceNotFoundException`: the access entry (and its policy
    association) had to be recreated from scratch on the new cluster
    (same two commands as the original setup above). **Whenever the
    cluster is deleted and recreated, redo the access entry +
    policy association too** — it's not just an IAM-side concern.
  - **Also found and fixed: a real, separate RBAC gap in the pre-flight
    check itself.** `kubectl get nodes` targets a **cluster-scoped**
    resource; a namespace-scoped `RoleBinding` (what `AmazonEKSEditPolicy`
    + `accessScope: namespace` produces) can never grant access to a
    cluster-scoped resource like `Node`, no matter what the underlying
    policy's rules list — this is a fundamental Kubernetes RBAC rule, not
    a misconfiguration to retry past. Fixed by adding a Kubernetes group
    (`github-actions-node-readers`) to the role's access entry via
    `aws eks update-access-entry --kubernetes-groups`, then a small
    read-only `ClusterRole`/`ClusterRoleBinding` (`get`/`list`/`watch` on
    `nodes` only) bound to that group — vendored in
    `infra/github-actions-node-reader-rbac.yaml`, applied directly via
    `kubectl apply -f` (also gets flagged by permission classifiers,
    same as the access-policy association — expect to run it yourself).
    **This ClusterRoleBinding is a cluster-internal Kubernetes object,
    not an AWS/IAM resource** — it does NOT get wiped by a cluster
    delete+recreate... except it does, trivially, because the entire
    cluster (and everything inside it, including this RBAC object) is
    what got deleted. Re-apply `infra/github-actions-node-reader-rbac.yaml`
    any time the cluster itself is recreated, alongside the access entry.
