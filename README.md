# llm-d Serving Labs — Self-Managed LLM Inference on EKS

A from-scratch GPU inference platform on AWS EKS, running [llm-d](https://github.com/llm-d/llm-d) + vLLM, including a real implementation and empirical benchmark of **prefill/decode (P/D) disaggregation** — a serving architecture technique that splits prompt processing and token generation onto separate, specialized GPUs.

This is a personal infrastructure/ML-systems project. The goal was to understand how modern LLM serving actually works under the hood — not just call an API — by standing up the control plane, the routing layer, and the model-serving layer myself, then measuring whether a known optimization technique actually pays off on a small, budget-constrained GPU footprint.

**Short version of the result:** it works correctly, and it doesn't help here — and the repo documents *why*, with real data, rather than stopping at "it didn't help."

## What's actually running

- **AWS EKS** cluster (`eksctl`-managed), two node groups: a small CPU node for routing/control components, and a GPU node group diversified across three instance types/GPU models (A10G, L4, L40S) for capacity resilience under a tight AWS quota
- **[llm-d](https://github.com/llm-d/llm-d) router**: Envoy proxy + a custom Endpoint Picker (EPP) implementing the [Gateway API Inference Extension](https://github.com/kubernetes-sigs/gateway-api-inference-extension) spec — prefix-cache-aware and load-aware routing to backend model servers
- **vLLM** serving `Qwen/Qwen3-8B`, in two topologies:
  - **Aggregated** (phase 1): each GPU runs a complete model server, handling both prompt processing and generation
  - **Disaggregated** (phase 2): dedicated prefill GPU(s) and dedicated decode GPU(s), coordinating via [NIXL](https://github.com/ai-dynamo/nixl) KV-cache transfer over a routing sidecar
- **CI**: YAML lint + `kustomize`/`helm` dry-run on every push
- **CD**: a `workflow_dispatch` GitHub Actions pipeline that redeploys config changes to the live cluster, authenticated via GitHub OIDC federation (no static AWS keys) with narrowly-scoped, namespace-level Kubernetes RBAC
- **Custom benchmarking tooling** (`infra/benchmark-ttft.py`, `infra/benchmark-compare.sh`): streaming-completion load generator that measures time-to-first-token separately from end-to-end latency, plus a fully automated A/B orchestration script

## Architecture

```
                    ┌─────────────────────────────┐
                    │        llm-d Router          │
  client ─────────▶ │  Envoy proxy ──▶ EPP          │
                    │  (prefix-cache + load aware)  │
                    └──────────────┬────────────────┘
                                   │
            ┌──────────────────────┴──────────────────────┐
            ▼                                              ▼
   ┌─────────────────┐                           ┌──────────────────┐
   │  Prefill GPU     │   KV cache via NIXL      │   Decode GPU      │
   │  vLLM (TP=1)     │ ───────────────────────▶ │   routing sidecar  │
   │  prompt → KV     │                          │   + vLLM (TP=1)    │
   └─────────────────┘                           │   KV → tokens      │
                                                  └──────────────────┘
```

Aggregated mode is the same diagram with one GPU doing both jobs per replica.

## Engineering problems actually solved

This is the part that doesn't show up in a benchmark table. Roughly chronological:

| Area | Problem | Fix |
|---|---|---|
| AWS account | New-account GPU quota denied twice; a *separate*, undocumented "Free Tier instance types only" account restriction blocked even non-GPU instances | Diagnosed via support correspondence and `aws freetier` APIs; appealed the quota, upgraded the account plan |
| GPU capacity | `g5.2xlarge` hit `InsufficientInstanceCapacity` across every AZ for 90+ minutes | Diversified the node group across 3 instance types/GPU models (A10G/L4/L40S) — confirmed via the AWS API that `ON_DEMAND` managed node groups support multiple instance types, not just Spot |
| vLLM sizing | Default model context needs more KV-cache memory than fits after model weights load | Capped `--max-model-len`, verified the real ceiling via vLLM's own engine config resolution rather than guessing |
| Kubernetes | Default `RollingUpdate` deadlocks a single-GPU workload (new pod can't schedule while the old one holds the only GPU; old pod won't die until the new one's ready) | `strategy: Recreate` |
| Router sizing | Chart defaults size the router for a production node pool (8 CPU / 16Gi for two sidecar containers) — way oversized for a small dev node | Right-sized resource overrides, applied as a layered Helm values file |
| Router calibration | The prefix-cache-affinity routing plugin ships a default tuned for a completely different reference model/GPU | Measured the real value for this hardware via the project's own calibration job — and re-measured separately for the disaggregated path, since it pays a real KV-transfer overhead the aggregated path doesn't |
| CI/CD auth | GitHub's OIDC token format isn't the commonly-documented one — it embeds immutable numeric IDs, not just repo/owner names | Diagnosed by decoding a real token rather than trusting docs |
| CI/CD RBAC | Two separate, non-obvious Kubernetes RBAC gaps: a namespace-scoped role can never grant access to a cluster-scoped resource (by design), and AWS's managed "edit" policy deliberately excludes RBAC objects | Added narrowly-scoped `ClusterRole`/`Role` grants for exactly the two gaps, nothing broader |
| Upstream drift | The disaggregation router image is a floating tag; it moved past a plugin the deployment guide's own text still referenced, crash-looping the router | Found the exact upstream removal PR and issue, not just a workaround — fixed the config at the actual root cause |
| Quota, round 2 | The second GPU quota increase (needed to run 2 GPUs concurrently) was denied outright — that specific quota is gated to a sales channel, not self-service | Found a technical workaround: the `.xlarge` variant of each GPU type is half the vCPUs with the *same* GPU/VRAM, fitting 2 concurrent GPUs inside the already-approved quota |
| Benchmarking | Naive load testing with blocking requests can't measure time-to-first-token at all | Built a streaming-completions benchmark that times the first chunk separately from the last |

## The benchmark: does P/D disaggregation actually help here?

### Round 1 — quick sanity check (not a fair comparison)

First pass: 1 aggregated GPU vs. 2 disaggregated GPUs (1 prefill + 1 decode), light load.

| Metric (concurrency 10 → 20) | Baseline (1 GPU) | Disaggregated (2 GPUs) |
|---|---|---|
| Requests/sec | 4.73 → 8.99 | 3.64 → 6.16 |
| TTFT avg | 0.258s → 0.288s | 0.806s → 1.120s |
| Tokens/sec | 236 → 450 | 182 → 308 |

Baseline won everywhere — but this compares 1 GPU against 2, and the load was too light to create any real contention for disaggregation to relieve. Not a fair test, so I built a proper one.

### Round 2 — equal compute, sustained load, longer prompts

Fully automated via `infra/benchmark-compare.sh`: scales GPU capacity, deploys each topology, runs a 90-second sustained load (concurrency 30, ~3,000-token prompts, 200-token completions), tears down, and prints a side-by-side report — with **no manual steps** in between.

| Metric | Baseline (2 GPUs, 2 aggregated replicas) | Disaggregated (2 GPUs, 1 prefill + 1 decode) |
|---|---|---|
| Requests/sec | 2.35 | 1.81 |
| TTFT avg | 0.583s | 4.111s |
| TTFT p90 | 1.864s | **21.352s** |
| E2E latency avg | 12.73s | 16.09s |
| Tokens/sec | 470.65 | 361.32 |

### Why — and this is the actual finding

The p50-vs-p90 gap on the disaggregated side (0.79s → 21.4s) is the signature of an **unbounded queue**, not a uniform slowdown. The root cause is measurable, not hand-wavy:

- This topology has exactly **one** prefill GPU, with a directly-measured real throughput ceiling of **1,392 tokens/sec**.
- At concurrency 30 with ~3,000-token prompts, each wave of concurrent requests needs roughly 90,000 tokens of prefill work — over a minute against that ceiling.
- Under *sustained* load, new requests arrive faster than that single GPU can drain the backlog, so the queue grows for the entire test window.
- The aggregated baseline has no equivalent chokepoint: both of its GPUs independently do prefill *and* decode for their own share of traffic, so there's no single resource carrying 100% of one kind of demand.

This directly confirms something `llm-d`'s own reference deployment is built around: their production topology uses **8 prefill GPUs for every 2 decode GPUs** — heavily prefill-weighted, because large prompts need disproportionately more prefill capacity than decode. A fixed-budget, quota-constrained 2-GPU setup can only afford a 1:1 ratio, which is a genuine mismatch for this workload shape. Equalizing GPU *count* didn't equalize *capability*, because the architecture's benefit requires enough prefill capacity to match prefill demand — and this account's AWS quota ceiling doesn't allow provisioning that.

**Conclusion:** P/D disaggregation is a real, correctly-implemented optimization (verified via actual NIXL KV-cache transfer metrics in the serving logs, not just "pods are up") that trades off differently depending on prefill:decode ratio versus workload shape. On this hardware budget, under this workload, the simpler aggregated topology is strictly better — and the benchmark tooling built here would immediately show the crossover point if GPU budget allowed testing a properly-provisioned prefill-heavy ratio.

Full raw results: [`benchmark-results-20261013-225105/`](./benchmark-results-20261013-225105).

## Repo layout

```
infra/
  cluster.yaml                           # eksctl cluster definition
  modelserver/                           # phase 1: aggregated vLLM deployment
  modelserver-pd/                        # phase 2: prefill + decode deployments
  router-*-override.values.yaml          # Helm value overrides (resource sizing, calibration, P/D plugin config)
  deploy-baseline.sh / deploy-pd.sh       # one-command deploy for each topology
  teardown-baseline.sh / teardown-pd.sh   # app-layer teardown (cluster stays up)
  benchmark-ttft.py                      # streaming TTFT/throughput load generator
  benchmark-compare.sh                   # fully automated A/B benchmark orchestration
  compare-results.py                     # side-by-side benchmark report
  github-actions-*.json / *-rbac.yaml    # CD pipeline's IAM trust policy + RBAC grants
.github/workflows/
  ci.yml                                 # lint + dry-run on every push
  deploy.yml                             # manually-triggered config redeploy via OIDC
CLAUDE.md                                # full build log — every bug, every fix, in order
```

`CLAUDE.md` is the detailed engineering journal this README is distilled from — worth reading for the complete, blow-by-blow troubleshooting history.

## Running it yourself

```bash
# Bring up the cluster (GPU node group starts at 0 — no GPU quota/capacity needed yet)
eksctl create cluster -f infra/cluster.yaml

# Aggregated baseline (1 GPU)
eksctl scale nodegroup --cluster=llm-d-harness --name=gpu-baseline --nodes=1 --nodes-min=0 --nodes-max=2 --region us-east-1
HF_TOKEN=<your token> bash infra/deploy-baseline.sh

# — or — P/D disaggregation (2 GPUs)
eksctl scale nodegroup --cluster=llm-d-harness --name=gpu-baseline --nodes=2 --nodes-min=0 --nodes-max=2 --region us-east-1
HF_TOKEN=<your token> bash infra/deploy-pd.sh

# — or — the full automated A/B comparison
HF_TOKEN=<your token> bash infra/benchmark-compare.sh

# Always tear down when done — EKS control plane + NAT gateway bill by the hour regardless of load
eksctl delete cluster -f infra/cluster.yaml
```

## Status

Phase 2 (P/D disaggregation + benchmarking) is complete, with a clear, data-backed answer about when it does and doesn't pay off on this budget. The natural next steps — not pursued further here, since they need either a larger GPU quota or a different workload shape than this project has access to — would be re-testing with a properly-provisioned prefill-heavy ratio, or moving on to phase 3 (a sandboxed tool-execution layer).
