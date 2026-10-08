"""Graph analytics orchestration.

Runs the Neo4j GDS-equivalent algorithm set over the current projection:

    PageRank          -> influence ranking (which papers/concepts matter)
    Louvain           -> research communities
    Brandes           -> bridge nodes (betweenness)
    Adamic-Adar/Jaccard -> link prediction ("potential relationship")
    cosine kNN        -> semantic similarity (vector space + graph)

Every result carries the engine that produced it (`nexus-native`, `neo4j-gds`,
`python-fallback`) so the UI can be explicit about how a number was computed.
Results are cached and invalidated by graph revision.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from backend.algorithms import kernel
from backend.models.graph import ASSOCIATIVE_RELS, GEdge, GNode

log = logging.getLogger("nexus.analytics")


@dataclass(slots=True)
class AnalyticsResult:
    revision: int
    pagerank: dict[str, float] = field(default_factory=dict)
    betweenness: dict[str, float] = field(default_factory=dict)
    communities: dict[str, int] = field(default_factory=dict)
    degree: dict[str, int] = field(default_factory=dict)
    engines: dict[str, str] = field(default_factory=dict)

    def metrics_for(self, node_id: str) -> dict[str, float]:
        return {
            "pagerank": round(self.pagerank.get(node_id, 0.0), 6),
            "betweenness": round(self.betweenness.get(node_id, 0.0), 6),
            "community": self.communities.get(node_id, -1),
            "degree": self.degree.get(node_id, 0),
        }


class AnalyticsEngine:
    """Computes and caches graph metrics for a projection."""

    def __init__(self, betweenness_samples: int = 220) -> None:
        self._lock = threading.Lock()
        self._cache: dict[int, AnalyticsResult] = {}
        # Brandes is O(nm); sampling keeps a 5k-node projection interactive.
        self.betweenness_samples = betweenness_samples

    # ------------------------------------------------------------------ core
    @staticmethod
    def _project(
        nodes: Sequence[GNode],
        edges: Sequence[GEdge],
        node_types: set[str] | None = None,
        rel_types: set[str] | None = None,
    ) -> tuple[list[str], list[tuple[str, str, float]]]:
        allowed_nodes = {n.id for n in nodes if node_types is None or n.label in node_types}
        proj: list[tuple[str, str, float]] = []
        for e in edges:
            if e.src not in allowed_nodes or e.dst not in allowed_nodes:
                continue
            if rel_types is not None and e.type not in rel_types:
                continue
            # Predicted links must never feed the algorithms that generated them.
            if e.type == "PREDICTED_LINK":
                continue
            weight = float(e.props.get("weight", 1.0) or 1.0)
            proj.append((e.src, e.dst, weight))
        return sorted(allowed_nodes), proj

    def compute(
        self,
        revision: int,
        nodes: Sequence[GNode],
        edges: Sequence[GEdge],
        *,
        force: bool = False,
    ) -> AnalyticsResult:
        with self._lock:
            if not force and revision in self._cache:
                return self._cache[revision]

            ids, proj = self._project(nodes, edges)
            degree: dict[str, int] = {i: 0 for i in ids}
            for u, v, _w in proj:
                degree[u] = degree.get(u, 0) + 1
                degree[v] = degree.get(v, 0) + 1

            pr, pr_engine = kernel.pagerank(ids, proj)
            communities, lv_engine = kernel.louvain(ids, proj)
            samples = self.betweenness_samples if len(ids) > 400 else 0
            bc, bc_engine = kernel.betweenness(ids, proj, samples=samples)

            result = AnalyticsResult(
                revision=revision,
                pagerank=pr,
                betweenness=bc,
                communities=communities,
                degree=degree,
                engines={
                    "pagerank": pr_engine,
                    "louvain": lv_engine,
                    "betweenness": bc_engine,
                    "betweenness_sampling": "exact" if samples == 0 else f"{samples} sources (approximate)",
                    "link_prediction": "native kernel: Adamic-Adar + Jaccard + common neighbours "
                    "(Python fallback identical)",
                },
            )
            self._cache[revision] = result
            if len(self._cache) > 8:  # keep the cache small and predictable
                oldest = sorted(self._cache)[0]
                self._cache.pop(oldest, None)
            return result

    # --------------------------------------------------------- similarity ---
    @staticmethod
    def structural_similarity(
        nodes: Sequence[GNode],
        edges: Sequence[GEdge],
        node_types: set[str] | None = None,
        limit_pairs: int = 400,
    ) -> list[dict[str, Any]]:
        """Adamic-Adar + Jaccard over the associative projection.

        These are *potential* relationships: the caller must present them as
        "predicted research connection", never as existing links.
        """
        ids, proj = AnalyticsEngine._project(nodes, edges, node_types=node_types, rel_types=set(ASSOCIATIVE_RELS))
        if not ids:
            return []
        id_set = set(ids)
        neighbours: dict[str, set[str]] = {i: set() for i in ids}
        for u, v, _w in proj:
            if u in id_set and v in id_set and u != v:
                neighbours[u].add(v)
                neighbours[v].add(u)

        # Candidate pairs: two-hop neighbours only (keeps the job small).
        candidates: set[tuple[str, str]] = set()
        for i in ids:
            for mid in neighbours[i]:
                for second in neighbours[mid]:
                    if second != i and second not in neighbours[i]:
                        candidates.add((i, second) if i < second else (second, i))
                        if len(candidates) >= limit_pairs * 20:
                            break

        pairs = list(candidates)[:limit_pairs]
        if not pairs:
            return []
        scored = kernel.link_prediction(ids, proj, pairs)
        existing = {(u, v) for u, v, _w in proj} | {(v, u) for u, v, _w in proj}
        out = []
        for (a, b), scores in scored.items():
            if (a, b) in existing:
                continue
            out.append(
                {
                    "source": a,
                    "target": b,
                    "adamic_adar": round(scores["adamic_adar"], 5),
                    "jaccard": round(scores["jaccard"], 5),
                    "common_neighbors": int(scores["common_neighbors"]),
                }
            )
        out.sort(key=lambda r: (-r["adamic_adar"], -r["jaccard"]))
        return out

    @staticmethod
    def semantic_neighbours(
        vectors: dict[str, Sequence[float]], k: int = 8, min_similarity: float = 0.15
    ) -> tuple[dict[str, list[tuple[str, float]]], str]:
        if not vectors:
            return {}, "none"
        return kernel.knn(vectors, k=k, min_similarity=min_similarity)

    @staticmethod
    def normalise(values: dict[str, float]) -> dict[str, float]:
        """Min-max normalisation used by the opportunity score (documented, not magic)."""
        if not values:
            return {}
        lo = min(values.values())
        hi = max(values.values())
        if math.isclose(hi, lo):
            return {k: 1.0 for k in values}
        return {k: (v - lo) / (hi - lo) for k, v in values.items()}


def gini(values: Iterable[float]) -> float:
    """Gini coefficient of a score distribution (used to sanity-check gap scores)."""
    xs = sorted(float(v) for v in values)
    n = len(xs)
    if n == 0 or sum(xs) == 0:
        return 0.0
    cumulative = 0.0
    for i, x in enumerate(xs, start=1):
        cumulative += i * x
    return (2 * cumulative) / (n * sum(xs)) - (n + 1) / n
