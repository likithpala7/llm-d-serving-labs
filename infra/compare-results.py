#!/usr/bin/env python3
# Prints a side-by-side comparison of two benchmark-ttft.py --json-out files.
import json
import sys

ROWS = [
    ("Requests/sec", "requests_per_sec", "{:.2f}"),
    ("TTFT avg (s)", "ttft_avg_s", "{:.3f}"),
    ("TTFT p50 (s)", "ttft_p50_s", "{:.3f}"),
    ("TTFT p90 (s)", "ttft_p90_s", "{:.3f}"),
    ("E2E latency avg (s)", "e2e_avg_s", "{:.3f}"),
    ("E2E latency p90 (s)", "e2e_p90_s", "{:.3f}"),
    ("Tokens/sec", "tokens_per_sec", "{:.2f}"),
    ("Errors", "errors", "{}"),
]


def load(path):
    with open(path) as f:
        return json.load(f)


def main():
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <result_a.json> <result_b.json>")
        sys.exit(1)

    a = load(sys.argv[1])
    b = load(sys.argv[2])
    label_a = a.get("label") or sys.argv[1]
    label_b = b.get("label") or sys.argv[2]

    w = max(22, len(label_a) + 2, len(label_b) + 2)
    print(f"{'Metric':<22} {label_a:>{w}} {label_b:>{w}}")
    print("-" * (22 + 2 * w + 2))
    for name, key, fmt in ROWS:
        va, vb = a.get(key), b.get(key)
        sa = fmt.format(va) if va is not None else "n/a"
        sb = fmt.format(vb) if vb is not None else "n/a"
        print(f"{name:<22} {sa:>{w}} {sb:>{w}}")


if __name__ == "__main__":
    main()
