#!/usr/bin/env python3
# TTFT + throughput benchmark via streaming completions. Unlike
# load-test.py (blocking requests — can't see TTFT at all, since TTFT is
# only meaningful once you separate "time to first chunk" from "time to
# last chunk"), this streams (stream: true) and times each chunk as it
# arrives.
#
# Two modes:
#   --requests N        fixed count, split across --concurrency workers
#   --duration SECONDS  sustained load: each of --concurrency workers
#                        fires requests back-to-back until the deadline
#                        (closer to the llm-d guide's own rate-sustained-
#                        for-120s benchmarking methodology than a fixed
#                        small count is)
#
# --prompt-tokens N builds an approximate N-token prompt by repeating
# filler text (~0.75 tokens/word in English — a heuristic for load
# shape, not an exact tokenizer match) instead of the short default
# prompt, so --prompt-tokens + --max-tokens can approximate a given
# ISL/OSL ratio.
#
# Requires:
#   kubectl port-forward -n <namespace> svc/<guide>-epp 8080:80
import argparse
import json
import statistics
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed


def build_prompt(token_target):
    if not token_target:
        return "Explain what a GPU is in one paragraph."
    filler = "The quick brown fox jumps over the lazy dog near the riverbank. "
    words_needed = int(token_target / 0.75)
    repeated = (filler * (words_needed // len(filler.split()) + 1)).split()
    return " ".join(repeated[:words_needed]) + " Now, in one paragraph, explain what a GPU is."


def send_streaming_request(url, model, prompt, max_tokens):
    payload = {"model": model, "prompt": prompt, "max_tokens": max_tokens, "stream": True}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    start = time.monotonic()
    ttft = None
    completion_tokens = 0
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            for line in resp:
                line = line.decode().strip()
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if data == "[DONE]":
                    break
                if ttft is None:
                    ttft = time.monotonic() - start
                chunk = json.loads(data)
                choices = chunk.get("choices", [])
                if choices and choices[0].get("text"):
                    completion_tokens += 1
        total = time.monotonic() - start
        return ttft, total, completion_tokens, None
    except Exception as e:
        total = time.monotonic() - start
        return ttft, total, completion_tokens, str(e)


def percentile(sorted_vals, p):
    if not sorted_vals:
        return float("nan")
    idx = min(int(len(sorted_vals) * p), len(sorted_vals) - 1)
    return sorted_vals[idx]


def worker_fixed_count(url, model, prompt, max_tokens, count, results):
    for _ in range(count):
        results.append(send_streaming_request(url, model, prompt, max_tokens))


def worker_sustained(url, model, prompt, max_tokens, deadline, results):
    while time.monotonic() < deadline:
        results.append(send_streaming_request(url, model, prompt, max_tokens))


def main():
    parser = argparse.ArgumentParser(description="TTFT + throughput benchmark (streaming)")
    parser.add_argument("--url", default="http://localhost:8080/v1/completions")
    parser.add_argument("--model", default="Qwen/Qwen3-8B")
    parser.add_argument("--requests", type=int, default=50, help="fixed-count mode: total requests")
    parser.add_argument("--duration", type=float, default=0, help="sustained mode: seconds; overrides --requests")
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--max-tokens", type=int, default=100)
    parser.add_argument("--prompt", default=None, help="literal prompt text; overrides --prompt-tokens")
    parser.add_argument("--prompt-tokens", type=int, default=0, help="approx. prompt length via repeated filler text")
    parser.add_argument("--label", default="", help="free-text label printed in the report (e.g. 'PD' or 'baseline')")
    parser.add_argument("--json-out", default=None, help="write a machine-readable summary to this file")
    args = parser.parse_args()

    prompt = args.prompt if args.prompt is not None else build_prompt(args.prompt_tokens)
    results = []

    if args.duration > 0:
        print(f"[{args.label or args.url}] Sustained load for {args.duration:.0f}s at concurrency {args.concurrency}...")
        deadline = time.monotonic() + args.duration
        start = time.monotonic()
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = [
                pool.submit(worker_sustained, args.url, args.model, prompt, args.max_tokens, deadline, results)
                for _ in range(args.concurrency)
            ]
            for f in as_completed(futures):
                f.result()
        duration = time.monotonic() - start
        total_requests = len(results)
    else:
        print(f"[{args.label or args.url}] Sending {args.requests} requests at concurrency {args.concurrency}...")
        start = time.monotonic()
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            futures = [
                pool.submit(send_streaming_request, args.url, args.model, prompt, args.max_tokens)
                for _ in range(args.requests)
            ]
            for f in as_completed(futures):
                results.append(f.result())
        duration = time.monotonic() - start
        total_requests = args.requests

    ttfts, totals, tokens_total, errors = [], [], 0, 0
    for ttft, total, tokens, error in results:
        if error:
            errors += 1
            print(f"  error: {error}")
            continue
        if ttft is not None:
            ttfts.append(ttft)
        totals.append(total)
        tokens_total += tokens

    ok = total_requests - errors
    ttfts.sort()
    totals.sort()

    summary = {
        "label": args.label,
        "duration_s": duration,
        "total_requests": total_requests,
        "ok": ok,
        "errors": errors,
        "requests_per_sec": total_requests / duration if duration else 0,
        "tokens_generated": tokens_total,
        "tokens_per_sec": tokens_total / duration if duration else 0,
        "ttft_avg_s": statistics.mean(ttfts) if ttfts else None,
        "ttft_p50_s": percentile(ttfts, 0.50) if ttfts else None,
        "ttft_p90_s": percentile(ttfts, 0.90) if ttfts else None,
        "ttft_p99_s": percentile(ttfts, 0.99) if ttfts else None,
        "e2e_avg_s": statistics.mean(totals) if totals else None,
        "e2e_p50_s": percentile(totals, 0.50) if totals else None,
        "e2e_p90_s": percentile(totals, 0.90) if totals else None,
        "e2e_p99_s": percentile(totals, 0.99) if totals else None,
    }

    print()
    print(f"=== {args.label or 'Results'} ===")
    print(f"Total time:        {duration:.2f}s")
    print(f"Requests:          {total_requests} ({ok} ok, {errors} failed)")
    print(f"Requests/sec:      {summary['requests_per_sec']:.2f}")
    if ttfts:
        print(f"TTFT avg:          {summary['ttft_avg_s']:.3f}s")
        print(f"TTFT p50:          {summary['ttft_p50_s']:.3f}s")
        print(f"TTFT p90:          {summary['ttft_p90_s']:.3f}s")
        print(f"TTFT p99:          {summary['ttft_p99_s']:.3f}s")
    if totals:
        print(f"E2E latency avg:   {summary['e2e_avg_s']:.3f}s")
        print(f"E2E latency p50:   {summary['e2e_p50_s']:.3f}s")
        print(f"E2E latency p90:   {summary['e2e_p90_s']:.3f}s")
        print(f"E2E latency p99:   {summary['e2e_p99_s']:.3f}s")
    print(f"Tokens generated:  {tokens_total}")
    print(f"Tokens/sec:        {summary['tokens_per_sec']:.2f}")

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump(summary, f, indent=2)


if __name__ == "__main__":
    main()
