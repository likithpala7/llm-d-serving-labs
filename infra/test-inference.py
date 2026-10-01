#!/usr/bin/env python3
# Sends a test completion request to the router. Requires:
#   kubectl port-forward -n llm-d-optimized-baseline svc/optimized-baseline-epp 8080:80
import json
import urllib.request

URL = "http://localhost:8080/v1/completions"
payload = {
    "model": "Qwen/Qwen3-8B",
    "prompt": "The capital of France is",
    "max_tokens": 20,
}

req = urllib.request.Request(
    URL,
    data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)

with urllib.request.urlopen(req) as resp:
    result = json.load(resp)

print(result["choices"][0]["text"])
