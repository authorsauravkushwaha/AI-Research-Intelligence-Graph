#!/usr/bin/env bash
# Synthetic benchmark: what does the kernel cost on a 20k-node / 120k-edge graph?
set -euo pipefail
BIN="build/bin/nexus-kernel"
python3 - "$BIN" <<'PY'
import json, random, subprocess, sys, time
BIN = sys.argv[1]
random.seed(7)
n, e = 20000, 120000
nodes = [f"n{i}" for i in range(n)]
edges = [[nodes[random.randrange(n)], nodes[random.randrange(n)], random.random()] for _ in range(e)]
for job in ("pagerank", "louvain", "betweenness", "degree"):
    payload = json.dumps({"job": job, "nodes": nodes, "edges": edges, "params": {"samples": 2000}})
    t0 = time.time()
    out = subprocess.run([BIN], input=payload, capture_output=True, text=True)
    dt = time.time() - t0
    d = json.loads(out.stdout)
    print(f"  {job:14s} ok={d['ok']} kernel_ms={d['metrics']['ms']:.1f} wall_s={dt:.2f}")
PY
