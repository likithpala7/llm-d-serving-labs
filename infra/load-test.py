#!/usr/bin/env python3
# Concurrent load test against the router. Requires:
#   kubectl port-forward -n llm-d-optimized-baseline svc/optimized-baseline-epp 8080:80
import argparse
import json
import statistics
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

URL = "http://localhost:8080/v1/completions"


def send_request(prompt, max_tokens):
    payload = {"model": "Qwen/Qwen3-8B", "prompt": prompt, "max_tokens": max_tokens}
    req = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.load(resp)
        return time.monotonic() - start, result["usage"]["completion_tokens"], None
    except Exception as e:
        return time.monotonic() - start, 0, str(e)


def main():
    parser = argparse.ArgumentParser(description="Load test the vLLM router endpoint")
    parser.add_argument("--requests", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--max-tokens", type=int, default=50)
    parser.add_argument("--prompt", default="Explain what a GPU is in one paragraph.")
    args = parser.parse_args()

    print(f"Sending {args.requests} requests at concurrency {args.concurrency}...")
    latencies, tokens_total, errors = [], 0, 0

    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(send_request, args.prompt, args.max_tokens) for _ in range(args.requests)]
        for future in as_completed(futures):
            latency, tokens, error = future.result()
            latencies.append(latency)
            if error:
                errors += 1
                print(f"  error: {error}")
            else:
                tokens_total += tokens
    duration = time.monotonic() - start

    ok = args.requests - errors
    sorted_lat = sorted(latencies)
    print()
    print(f"Total time:       {duration:.2f}s")
    print(f"Requests:         {args.requests} ({ok} ok, {errors} failed)")
    print(f"Requests/sec:     {args.requests / duration:.2f}")
    if ok:
        print(f"Latency avg:      {statistics.mean(latencies):.2f}s")
        print(f"Latency p50:      {sorted_lat[int(len(sorted_lat) * 0.5)]:.2f}s")
        print(f"Latency p90:      {sorted_lat[int(len(sorted_lat) * 0.9)]:.2f}s")
        print(f"Latency p99:      {sorted_lat[min(int(len(sorted_lat) * 0.99), len(sorted_lat) - 1)]:.2f}s")
        print(f"Latency max:      {max(latencies):.2f}s")
        print(f"Tokens generated: {tokens_total}")
        print(f"Tokens/sec:       {tokens_total / duration:.2f}")


if __name__ == "__main__":
    main()
