# llm-d Serving Labs — Self-Managed LLM Inference on EKS

**The question this project set out to answer:** does splitting prompt processing (*prefill*) and token generation (*decode*) onto separate, dedicated GPUs — a serving technique called prefill/decode disaggregation — actually make LLM serving faster, or is it a win that only shows up on paper?

To find out, I built a complete GPU inference stack from scratch on AWS EKS, running [llm-d](https://github.com/llm-d/llm-d) and vLLM, implemented both the simple ("aggregated") and disaggregated serving topologies on it, and built the tooling needed to measure the difference honestly. The answer turned out to be more interesting than yes or no — and the reason why is the actual payoff of this project.

## The setup

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
   │  prompt → KV     │ ───────────────────────▶ │   KV → tokens      │
   └─────────────────┘                           └──────────────────┘
```

An EKS cluster, llm-d's router (Envoy plus a custom endpoint picker doing prefix-cache- and load-aware routing), and vLLM serving `Qwen/Qwen3-8B`. Aggregated mode is the same diagram with one GPU doing both jobs.

One constraint shaped everything that follows, and it's worth stating up front rather than burying: **the AWS account behind this has a hard quota ceiling of 8 vCPUs for GPU instances — room for exactly two concurrent GPUs, no more.** Every design decision past this point had to live inside that budget. That turns out to matter a great deal.

## The experiment

### A first look, and why it wasn't trustworthy

The obvious first test: one aggregated GPU vs. two disaggregated GPUs (one prefill, one decode), light load. Aggregated won on every metric. But this comparison was lopsided twice over — one GPU against two is not a fair fight, and the load was light enough that the two GPUs in the disaggregated setup were never actually contending with each other for anything. A result from an unfair test isn't a result, so I built a proper one.

### A fair test

I automated the whole comparison into one script (`infra/benchmark-compare.sh`): scale the cluster, deploy the aggregated baseline at **two replicas** (the same two-GPU budget as the disaggregated setup — now genuinely equal compute), run a 90-second *sustained* load with longer prompts (~3,000 tokens) at concurrency 30, tear down, repeat for the disaggregated topology, and print the two side by side. No manual steps, same conditions both times.

| Metric | Aggregated (2 GPUs) | Disaggregated (1 prefill + 1 decode) |
|---|---|---|
| Requests/sec | 2.35 | 1.81 |
| TTFT p50 | 0.385s | 0.787s |
| **TTFT p90** | **1.864s** | **21.352s** |
| Tokens/sec | 470.65 | 361.32 |

Disaggregation still lost — badly, and in a very specific way. The p50 numbers are only about 2x apart, but p90 is **over 11x worse**. That shape — fine at the median, catastrophic at the tail — isn't what a uniformly slower system looks like. It's the signature of a queue that never drains.

### Finding the actual mechanism

A disaggregated topology with one prefill GPU and one decode GPU has exactly one place prompt-processing can happen: that single prefill GPU. I'd already measured its real throughput ceiling directly, using llm-d's own calibration tooling: **1,392 tokens/sec**.

The math makes the bottleneck concrete. At concurrency 30 with ~3,000-token prompts, each wave of concurrent requests needs roughly 90,000 tokens of prefill work — against a 1,392 tokens/sec ceiling, that's over a minute just to clear *one* wave. Under sustained load, new requests keep arriving before the old ones are done, so the backlog grows for the entire test window. Requests that land early see a short queue; requests that land late are stuck behind an ever-longer one. That's exactly the p50-vs-p90 gap in the data.

The aggregated baseline never hits this, because it has no equivalent chokepoint: both of its GPUs independently do prefill *and* decode for their own share of traffic. Nothing routes 100% of one kind of demand through a single resource.

## The finding

This isn't a story about disaggregation being overhyped. It's a story about a known, *documented* tradeoff showing up exactly where the documentation says it will. llm-d's own reference deployment uses **8 prefill GPUs for every 2 decode GPUs** — heavily prefill-weighted, because large prompts genuinely need disproportionate prefill capacity. The project's two-GPU budget can only afford a 1:1 ratio, which is a real mismatch for this workload.

**Equalizing GPU *count* didn't equalize *capability*.** Disaggregation's benefit depends on provisioning enough prefill capacity to match prefill demand — and an 8-vCPU quota doesn't allow provisioning that here. Within that budget, the simpler topology — every GPU doing everything — is simply the better engineering choice. The architecture itself works correctly (confirmed via real NIXL KV-cache transfer metrics in the serving logs, not just "the pods are up"); it just isn't the right tool for this resource envelope.

## Try it yourself

```bash
eksctl create cluster -f infra/cluster.yaml

# Aggregated baseline
eksctl scale nodegroup --cluster=llm-d-harness --name=gpu-baseline --nodes=1 --region us-east-1
HF_TOKEN=<your token> bash infra/deploy-baseline.sh

# — or — the full automated comparison from above
HF_TOKEN=<your token> bash infra/benchmark-compare.sh

# Always tear down — EKS control plane + NAT gateway bill hourly regardless of load
eksctl delete cluster -f infra/cluster.yaml
```

## Repo layout

```
infra/
  cluster.yaml              # eksctl cluster definition
  modelserver/               # aggregated vLLM deployment
  modelserver-pd/            # prefill + decode deployments
  deploy-*.sh / teardown-*.sh
  benchmark-ttft.py          # streaming TTFT/throughput load generator
  benchmark-compare.sh       # the automated A/B comparison above
.github/workflows/           # CI (lint + dry-run) and CD (OIDC-authenticated config redeploy)
CLAUDE.md                    # the full build log — every bug, every fix, in order
```

Getting to the point where this experiment could even run involved a long tail of real infrastructure problems — AWS quota and account restrictions, GPU capacity scarcity, Kubernetes RBAC edge cases, upstream API drift in a fast-moving project, building a benchmarking tool because the obvious one (blocking HTTP requests) can't actually measure time-to-first-token at all. None of that is the point of this README, but all of it is documented, in order, in `CLAUDE.md` for anyone who wants the full story.
