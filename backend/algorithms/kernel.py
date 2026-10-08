"""Bridge between Python and the native C++ graph analytics kernel.

Why a native kernel at all?
    PageRank + Louvain + Brandes betweenness over the *whole* research graph are
    run every time the gap engine or the 3D graph view refreshes. In pure Python
    that is seconds per interaction — fatal during a live demo. The C++ kernel
    (`native/`, zero dependencies) does it in milliseconds.

    If the kernel binary has not been built, this module transparently falls back
    to a pure-Python implementation with identical semantics (slower, and it says
    so in `engine`), so the application never depends on a compiler being present.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from backend.config import NATIVE_BIN

log = logging.getLogger("nexus.kernel")

_LOCK = threading.Lock()
_KERNEL_INFO: dict[str, Any] | None = None


@dataclass(slots=True)
class KernelResult:
    ok: bool
    job: str
    result: dict[str, Any]
    engine: str
    ms: float
    error: str | None = None


def _ensure_binary() -> Path | None:
    if NATIVE_BIN.exists() and NATIVE_BIN.is_file():
        return NATIVE_BIN
    # Best-effort on-demand build so a fresh clone works without a manual step.
    make = shutil.which("make")
    build_dir = NATIVE_BIN.parents[2]
    if make and (build_dir / "Makefile").exists():
        try:
            subprocess.run(
                [make, "-C", str(build_dir), "-j", "2"],
                check=True,
                capture_output=True,
                timeout=300,
            )
            if NATIVE_BIN.exists():
                log.info("built native kernel on demand at %s", NATIVE_BIN)
                return NATIVE_BIN
        except subprocess.SubprocessError as exc:  # pragma: no cover - env dependent
            log.warning("on-demand kernel build failed: %s", exc)
    return None


def kernel_info(force: bool = False) -> dict[str, Any]:
    """Reports whether the native kernel is available (used by /api/health)."""
    global _KERNEL_INFO
    with _LOCK:
        if _KERNEL_INFO is not None and not force:
            return _KERNEL_INFO
        binary = _ensure_binary()
        if binary is None:
            _KERNEL_INFO = {
                "available": False,
                "engine": "python-fallback",
                "path": str(NATIVE_BIN),
                "detail": "native kernel not built — run `make` in native/ for "
                "millisecond-scale Louvain/PageRank/betweenness",
            }
            return _KERNEL_INFO
        try:
            proc = subprocess.run(
                [str(binary)],
                input='{"job":"ping"}',
                capture_output=True,
                text=True,
                timeout=20,
            )
            payload = json.loads(proc.stdout.strip().splitlines()[-1])
            _KERNEL_INFO = {
                "available": bool(payload.get("ok")),
                "engine": payload.get("engine", "nexus-native"),
                "version": payload.get("result", {}).get("version", "unknown"),
                "path": str(binary),
                "language": "C++17",
            }
        except (subprocess.SubprocessError, json.JSONDecodeError, IndexError) as exc:
            _KERNEL_INFO = {
                "available": False,
                "engine": "python-fallback",
                "path": str(binary),
                "detail": f"kernel ping failed: {exc}",
            }
        return _KERNEL_INFO


def run_job(
    job: str,
    nodes: Sequence[str],
    edges: Sequence[tuple[str, str, float]],
    params: dict[str, Any] | None = None,
    *,
    source: str | None = None,
    target: str | None = None,
    pairs: Sequence[tuple[str, str]] | None = None,
    vectors: Sequence[tuple[str, Sequence[float]]] | None = None,
    timeout: float = 120.0,
) -> KernelResult:
    """Execute one analytics job in the native kernel."""
    binary = _ensure_binary()
    if binary is None:
        return KernelResult(False, job, {}, "python-fallback", 0.0, "native kernel unavailable")

    payload: dict[str, Any] = {
        "job": job,
        "nodes": list(nodes),
        "edges": [[u, v, float(w)] for u, v, w in edges],
        "params": params or {},
    }
    if source is not None:
        payload["source"] = source
    if target is not None:
        payload["target"] = target
    if pairs is not None:
        payload["pairs"] = [[a, b] for a, b in pairs]
    if vectors is not None:
        payload["vectors"] = [{"id": i, "vector": [float(x) for x in v]} for i, v in vectors]

    started = time.perf_counter()
    try:
        proc = subprocess.run(
            [str(binary)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return KernelResult(False, job, {}, "nexus-native", 0.0, f"kernel timeout after {timeout}s")
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    if proc.returncode != 0 and not proc.stdout.strip():
        return KernelResult(False, job, {}, "nexus-native", elapsed_ms, proc.stderr.strip()[:400] or "kernel error")

    try:
        data = json.loads(proc.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        return KernelResult(False, job, {}, "nexus-native", elapsed_ms, f"bad kernel output: {exc}")

    if not data.get("ok"):
        return KernelResult(False, job, {}, "nexus-native", elapsed_ms, str(data.get("error"))[:400])

    return KernelResult(
        True,
        job,
        data.get("result", {}),
        data.get("engine", "nexus-native"),
        float(data.get("metrics", {}).get("ms", elapsed_ms)),
    )


# --------------------------------------------------------------------------- #
# Pure-Python fallbacks — same semantics, used only when the kernel is missing. #
# --------------------------------------------------------------------------- #


def py_pagerank(
    nodes: Sequence[str],
    edges: Sequence[tuple[str, str, float]],
    damping: float = 0.85,
    iterations: int = 60,
) -> dict[str, float]:
    n = len(nodes)
    if n == 0:
        return {}
    rank = {v: 1.0 / n for v in nodes}
    out_w: dict[str, float] = {v: 0.0 for v in nodes}
    out_adj: dict[str, list[tuple[str, float]]] = {v: [] for v in nodes}
    for u, v, w in edges:
        if u == v:
            continue
        out_w[u] = out_w.get(u, 0.0) + w
        out_w[v] = out_w.get(v, 0.0) + w
        out_adj.setdefault(u, []).append((v, w))
        out_adj.setdefault(v, []).append((u, w))
    teleport = (1.0 - damping) / n
    for _ in range(iterations):
        dangling = sum(rank[v] for v in nodes if out_w.get(v, 0.0) <= 0.0)
        nxt = {v: teleport + damping * dangling / n for v in nodes}
        for u in nodes:
            w_sum = out_w.get(u, 0.0)
            if w_sum <= 0.0:
                continue
            share = damping * rank[u] / w_sum
            for v, w in out_adj.get(u, ()):
                nxt[v] = nxt.get(v, 0.0) + share * w
        delta = sum(abs(nxt[v] - rank[v]) for v in nodes)
        rank = nxt
        if delta < 1e-9:
            break
    return rank


def py_louvain(
    nodes: Sequence[str],
    edges: Sequence[tuple[str, str, float]],
    resolution: float = 1.0,
    seed: int = 42,
) -> dict[str, int]:
    """Louvain with self-loop-preserving aggregation (same rules as the kernel)."""
    import random

    rng = random.Random(seed)
    ids = {name: i for i, name in enumerate(nodes)}
    level_edges: list[tuple[int, int, float]] = [
        (ids[u], ids[v], float(w)) for u, v, w in edges if u in ids and v in ids
    ]
    cur_n = len(nodes)
    orig_to_cur = list(range(cur_n))
    m = sum(w for _, _, w in level_edges)
    if m <= 0:
        return {name: 0 for name in nodes}
    m2 = 2.0 * m

    for _ in range(20):
        adj: list[dict[int, float]] = [dict() for _ in range(cur_n)]
        for u, v, w in level_edges:
            if u == v:
                adj[u][u] = adj[u].get(u, 0.0) + w
            else:
                adj[u][v] = adj[u].get(v, 0.0) + w
                adj[v][u] = adj[v].get(u, 0.0) + w
        kdeg = []
        for i in range(cur_n):
            deg = sum(adj[i].values())
            if i in adj[i]:
                deg += adj[i][i]
            kdeg.append(deg)

        comm = list(range(cur_n))
        tot = list(kdeg)
        order = list(range(cur_n))
        moved = True
        sweeps = 0
        while moved and sweeps < 20:
            moved = False
            sweeps += 1
            rng.shuffle(order)
            for node in order:
                frm = comm[node]
                cand: dict[int, float] = {}
                for nb, w in adj[node].items():
                    if nb == node:
                        continue
                    cand[comm[nb]] = cand.get(comm[nb], 0.0) + w
                tot[frm] -= kdeg[node]
                best_gain, best = 0.0, frm
                for c, w_in in cand.items():
                    gain = w_in - resolution * tot[c] * kdeg[node] / m2
                    if gain > best_gain + 1e-12:
                        best_gain, best = gain, c
                tot[best] += kdeg[node]
                if best != frm:
                    comm[node] = best
                    moved = True

        remap: dict[int, int] = {}
        compact = [0] * cur_n
        for i in range(cur_n):
            c = comm[i]
            if c not in remap:
                remap[c] = len(remap)
            compact[i] = remap[c]
        new_n = len(remap)
        for i in range(len(nodes)):
            orig_to_cur[i] = compact[orig_to_cur[i]]
        if new_n == cur_n:
            break
        agg: dict[tuple[int, int], float] = {}
        for u, v, w in level_edges:
            a, b = compact[u], compact[v]
            if a > b:
                a, b = b, a
            agg[(a, b)] = agg.get((a, b), 0.0) + w
        level_edges = [(a, b, w) for (a, b), w in agg.items()]
        cur_n = new_n
        if cur_n <= 1:
            break

    remap2: dict[int, int] = {}
    out: dict[str, int] = {}
    for i, name in enumerate(nodes):
        c = orig_to_cur[i]
        if c not in remap2:
            remap2[c] = len(remap2)
        out[name] = remap2[c]
    return out


def py_betweenness(
    nodes: Sequence[str],
    edges: Sequence[tuple[str, str, float]],
    weighted: bool = True,
    samples: int = 0,
    seed: int = 7,
) -> dict[str, float]:
    import heapq
    import random

    rng = random.Random(seed)
    n = len(nodes)
    if n == 0:
        return {}
    idx = {name: i for i, name in enumerate(nodes)}
    adj: list[list[tuple[int, float]]] = [[] for _ in range(n)]
    for u, v, w in edges:
        if u not in idx or v not in idx:
            continue
        iu, iv = idx[u], idx[v]
        adj[iu].append((iv, w))
        adj[iv].append((iu, w))

    sources = list(range(n))
    if 0 < samples < n:
        sources = rng.sample(sources, samples)
    bc = [0.0] * n

    for s in sources:
        if weighted:
            dist = [float("inf")] * n
            sigma = [0.0] * n
            delta = [0.0] * n
            preds: list[list[int]] = [[] for _ in range(n)]
            seen = [False] * n
            dist[s] = 0.0
            sigma[s] = 1.0
            pq = [(0.0, s)]
            stack: list[int] = []
            while pq:
                d, v = heapq.heappop(pq)
                if seen[v]:
                    continue
                seen[v] = True
                stack.append(v)
                for nb, w in adj[v]:
                    nd = d + 1.0 / max(1e-6, w)
                    if nd < dist[nb] - 1e-12:
                        dist[nb] = nd
                        sigma[nb] = sigma[v]
                        preds[nb] = [v]
                        heapq.heappush(pq, (nd, nb))
                    elif abs(nd - dist[nb]) <= 1e-12:
                        sigma[nb] += sigma[v]
                        preds[nb].append(v)
        else:
            from collections import deque

            dist = [-1.0] * n
            sigma = [0.0] * n
            delta = [0.0] * n
            preds = [[] for _ in range(n)]
            dist[s] = 0.0
            sigma[s] = 1.0
            stack = []
            dq = deque([s])
            while dq:
                v = dq.popleft()
                stack.append(v)
                for nb, _w in adj[v]:
                    if dist[nb] < 0:
                        dist[nb] = dist[v] + 1.0
                        dq.append(nb)
                    if dist[nb] == dist[v] + 1.0:
                        sigma[nb] += sigma[v]
                        preds[nb].append(v)
        for w in reversed(stack):
            for v in preds[w]:
                if sigma[w] > 0:
                    delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w])
            if w != s:
                bc[w] += delta[w]
    scale = (n / len(sources)) if 0 < samples < n else 1.0
    norm = 1.0 / ((n - 1) * (n - 2)) if n > 2 else 1.0
    return {nodes[i]: bc[i] * scale * norm for i in range(n)}


def pagerank(nodes: Sequence[str], edges: Sequence[tuple[str, str, float]]) -> tuple[dict[str, float], str]:
    res = run_job("pagerank", nodes, edges)
    if res.ok and "pagerank" in res.result:
        return {k: float(v) for k, v in res.result["pagerank"]}, res.engine
    return py_pagerank(nodes, edges), "python-fallback"


def louvain(
    nodes: Sequence[str],
    edges: Sequence[tuple[str, str, float]],
    resolution: float = 1.0,
    seed: int = 42,
) -> tuple[dict[str, int], str]:
    """Louvain community detection.

    `resolution` controls granularity: >1 yields more, smaller communities.
    The gap engine raises it slightly (1.05) so a tight topic scope still splits
    into meaningful concept clusters rather than one blob.
    """
    res = run_job("louvain", nodes, edges, {"resolution": resolution, "seed": seed})
    if res.ok and "communities" in res.result:
        return {k: int(v) for k, v in res.result["communities"]}, res.engine
    return py_louvain(nodes, edges, resolution=resolution, seed=seed), "python-fallback"


def betweenness(
    nodes: Sequence[str], edges: Sequence[tuple[str, str, float]], samples: int = 0
) -> tuple[dict[str, float], str]:
    res = run_job("betweenness", nodes, edges, {"samples": samples})
    if res.ok and "betweenness" in res.result:
        return {k: float(v) for k, v in res.result["betweenness"]}, res.engine
    return py_betweenness(nodes, edges, samples=samples), "python-fallback"


def shortest_path(
    nodes: Sequence[str], edges: Sequence[tuple[str, str, float]], source: str, target: str
) -> list[str]:
    res = run_job("shortest_path", nodes, edges, source=source, target=target)
    if res.ok:
        return list(res.result.get("path", []))
    # BFS fallback
    from collections import deque

    adj: dict[str, list[str]] = {n: [] for n in nodes}
    for u, v, _w in edges:
        adj.setdefault(u, []).append(v)
        adj.setdefault(v, []).append(u)
    prev: dict[str, str] = {}
    seen = {source}
    dq = deque([source])
    while dq:
        cur = dq.popleft()
        if cur == target:
            break
        for nb in adj.get(cur, ()):
            if nb not in seen:
                seen.add(nb)
                prev[nb] = cur
                dq.append(nb)
    if target not in seen and target != source:
        return []
    path = [target]
    while path[-1] != source:
        path.append(prev[path[-1]])
    return list(reversed(path))


def link_prediction(
    nodes: Sequence[str], edges: Sequence[tuple[str, str, float]], pairs: Sequence[tuple[str, str]]
) -> dict[tuple[str, str], dict[str, float]]:
    res = run_job("link_prediction", nodes, edges, pairs=pairs)
    out: dict[tuple[str, str], dict[str, float]] = {}
    if res.ok:
        for a, b, aa, jc, cn in res.result.get("predictions", []):
            out[(a, b)] = {"adamic_adar": float(aa), "jaccard": float(jc), "common_neighbors": float(cn)}
        return out
    # Python fallback: Adamic-Adar + Jaccard over the undirected neighbourhood.
    import math

    nbr: dict[str, set[str]] = {n: set() for n in nodes}
    for u, v, _w in edges:
        if u in nbr and v in nbr and u != v:
            nbr[u].add(v)
            nbr[v].add(u)
    for a, b in pairs:
        if a not in nbr or b not in nbr:
            continue
        common = nbr[a] & nbr[b]
        union = nbr[a] | nbr[b]
        aa = sum(1.0 / math.log(len(nbr[w]) + 2.0) for w in common)
        out[(a, b)] = {
            "adamic_adar": aa,
            "jaccard": (len(common) / len(union)) if union else 0.0,
            "common_neighbors": float(len(common)),
        }
    return out


def knn(
    vectors: dict[str, Sequence[float]], k: int = 10, min_similarity: float = 0.05
) -> tuple[dict[str, list[tuple[str, float]]], str]:
    ids = list(vectors.keys())
    res = run_job("knn", [], [], {"k": k, "min_similarity": min_similarity}, vectors=[(i, vectors[i]) for i in ids])
    if res.ok and "neighbors" in res.result:
        return {i: [(n, float(s)) for n, s in pairs] for i, pairs in res.result["neighbors"]}, res.engine
    # Python fallback
    import math

    def norm(v: Sequence[float]) -> list[float]:
        s = math.sqrt(sum(float(x) * float(x) for x in v)) or 1.0
        return [float(x) / s for x in v]

    unit = {i: norm(vectors[i]) for i in ids}
    out: dict[str, list[tuple[str, float]]] = {}
    for i in ids:
        sims = []
        for j in ids:
            if i == j:
                continue
            dot = sum(a * b for a, b in zip(unit[i], unit[j]))
            if dot >= min_similarity:
                sims.append((j, dot))
        sims.sort(key=lambda t: -t[1])
        out[i] = sims[:k]
    return out, "python-fallback"


def distances(
    nodes: Sequence[str], edges: Sequence[tuple[str, str, float]], source: str, max_depth: int = 3
) -> dict[str, int]:
    """Hop distances from `source`, used for the evidence-path view."""
    from collections import deque

    adj: dict[str, list[str]] = {n: [] for n in nodes}
    for u, v, _w in edges:
        adj.setdefault(u, []).append(v)
        adj.setdefault(v, []).append(u)
    dist = {source: 0}
    dq = deque([source])
    while dq:
        cur = dq.popleft()
        if dist[cur] >= max_depth:
            continue
        for nb in adj.get(cur, ()):
            if nb not in dist:
                dist[nb] = dist[cur] + 1
                dq.append(nb)
    return dist
