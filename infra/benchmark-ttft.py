#!/usr/bin/env python3
# TTFT + throughput benchmark via streaming completions. Unlike
# load-test.py (blocking requests — can't see TTFT at all, since TTFT is
# only meaningful once you separate "time to first chunk" from "time to
# last chunk"), this streams (stream: true) and times each chunk as it
# arrives.
#
# Requires:
#   kubectl port-forward -n <namespace> svc/<guide>-epp 8080:80
import argparse
import json
import statistics
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed


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


def main():
    parser = argparse.ArgumentParser(description="TTFT + throughput benchmark (streaming)")
    parser.add_argument("--url", default="http://localhost:8080/v1/completions")
    parser.add_argument("--model", default="Qwen/Qwen3-8B")
    parser.add_argument("--requests", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--max-tokens", type=int, default=100)
    parser.add_argument("--prompt", default="Explain what a GPU is in one paragraph.")
    parser.add_argument("--label", default="", help="free-text label printed in the report (e.g. 'PD' or 'baseline')")
    args = parser.parse_args()

    print(f"[{args.label or args.url}] Sending {args.requests} requests at concurrency {args.concurrency}...")
    ttfts, totals, tokens_total, errors = [], [], 0, 0

    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(send_streaming_request, args.url, args.model, args.prompt, args.max_tokens)
            for _ in range(args.requests)
        ]
        for future in as_completed(futures):
            ttft, total, tokens, error = future.result()
            if error:
                errors += 1
                print(f"  error: {error}")
                continue
            if ttft is not None:
                ttfts.append(ttft)
            totals.append(total)
            tokens_total += tokens
    duration = time.monotonic() - start

    ok = args.requests - errors
    ttfts.sort()
    totals.sort()

    print()
    print(f"=== {args.label or 'Results'} ===")
    print(f"Total time:        {duration:.2f}s")
    print(f"Requests:          {args.requests} ({ok} ok, {errors} failed)")
    print(f"Requests/sec:      {args.requests / duration:.2f}")
    if ttfts:
        print(f"TTFT avg:          {statistics.mean(ttfts):.3f}s")
        print(f"TTFT p50:          {percentile(ttfts, 0.50):.3f}s")
        print(f"TTFT p90:          {percentile(ttfts, 0.90):.3f}s")
        print(f"TTFT p99:          {percentile(ttfts, 0.99):.3f}s")
    if totals:
        print(f"E2E latency avg:   {statistics.mean(totals):.3f}s")
        print(f"E2E latency p50:   {percentile(totals, 0.50):.3f}s")
        print(f"E2E latency p90:   {percentile(totals, 0.90):.3f}s")
        print(f"E2E latency p99:   {percentile(totals, 0.99):.3f}s")
    print(f"Tokens generated:  {tokens_total}")
    print(f"Tokens/sec:        {tokens_total / duration:.2f}")


if __name__ == "__main__":
    main()
