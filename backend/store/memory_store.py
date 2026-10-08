"""In-process research graph store.

This is NEXUS's always-available engine: it loads the curated corpus, builds the
knowledge graph, derives similarity / community / claim-conflict structure, and
answers exactly the same query API as the Neo4j store. Two reasons it exists:

1. **Portability** — a judge (or a reviewer on GitHub) can run the whole product
   with nothing but Python. No Docker, no database service, no credentials.
2. **Truth-preserving fallback** — when Neo4j is unreachable at runtime the API
   keeps working and says so (`engine: "in-process"`), instead of showing an
   error page.

Neo4j remains the reference backend for the graph *database* story (see
`backend/graph/neo4j_store.py` and `cypher/`), and the analytics are computed by
the same native C++ kernel in both.
"""

from __future__ import annotations

import json
import logging
import math
import threading
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable, Sequence

from backend.config import DEMO_DIR
from backend.engine.analytics import AnalyticsEngine, AnalyticsResult
from backend.ingestion import claims as claims_mod
from backend.ingestion.graph_builder import (
    author_id,
    build_graph,
    claim_id,
    community_id,
    dataset_id,
    method_id,
    paper_id,
    topic_id,
)
from backend.models.graph import ASSOCIATIVE_RELS, GEdge, GNode, REL_TYPES, Subgraph
from backend.rag.embeddings import embed

log = logging.getLogger("nexus.store")


class MemoryGraphStore:
    """Graph store backed entirely by the in-process corpus + native kernel."""

    engine_name = "in-process"

    def __init__(self, corpus_path: Path | None = None, *, enable_similarity: bool = True) -> None:
        self.corpus_path = corpus_path or (DEMO_DIR / "corpus.json")
        self.nodes: dict[str, GNode] = {}
        self.edges: list[GEdge] = []
        self.analytics_engine = AnalyticsEngine()
        self.analytics: AnalyticsResult | None = None
        self.revision = 0
        self.embeddings: dict[str, list[float]] = {}
        self.embedding_engine = "none"
        self.conflicts: list[dict[str, Any]] = []
        self.predicted_links: list[dict[str, Any]] = []
        self.similarity_pairs: list[dict[str, Any]] = []
        self.community_profiles: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._corpus: dict[str, Any] = {}
        self.enable_similarity = enable_similarity

    # ------------------------------------------------------------ lifecycle
    def load(self) -> None:
        with self._lock:
            if not self.corpus_path.exists():
                raise FileNotFoundError(
                    f"corpus not found at {self.corpus_path} — run `python3 scripts/build_corpus.py`"
                )
            self._corpus = json.loads(self.corpus_path.read_text(encoding="utf-8"))
            built = build_graph(self._corpus)
            self.nodes = {n.id: n for n in built.nodes}
            self.edges = list(built.edges)
            self._add_corpus_taxonomy_nodes()
            self._resolve_claims()
            self.analytics = self.analytics_engine.compute(self.revision, list(self.nodes.values()), self.edges, force=True)
            if self.enable_similarity:
                self._build_similarity()
            self._assign_communities()
            self._rebuild_community_profiles()
            self.analytics = self.analytics_engine.compute(self.revision, list(self.nodes.values()), self.edges, force=True)
            log.info(
                "in-process graph ready: %d nodes / %d edges (revision %d)",
                len(self.nodes),
                len(self.edges),
                self.revision,
            )

    # --------------------------------------------------------------- build
    def _add_corpus_taxonomy_nodes(self) -> None:
        for kind, label, idfn in (
            ("topics", "Topic", topic_id),
            ("methods", "Method", method_id),
            ("datasets", "Dataset", dataset_id),
        ):
            for record in self._corpus.get(kind, []):
                node_id = idfn(record["name"])
                if node_id in self.nodes:
                    continue
                # Taxonomy entries with no surviving paper connection are still
                # useful as vocabulary, but only if at least one paper uses them.
                if record.get("papers", 0) > 0:
                    self.nodes[node_id] = GNode(
                        id=node_id,
                        label=label,
                        name=record["name"],
                        props={"paper_count": record.get("papers", 0)},
                    )

    def _resolve_claims(self) -> None:
        records = [
            {
                "id": c["id"],
                "text": c["text"],
                "paper": c.get("paper"),
                "source": c.get("source", "corpus"),
                "stance": c.get("stance", "reports"),
            }
            for c in self._corpus.get("claims", [])
        ]
        payload = claims_mod.resolve_claims(records)
        self.claim_engine = payload.get("engine", "unknown")
        self.conflicts = payload.get("conflicts", [])

        parsed = {c["id"]: c for c in payload.get("claims", [])}
        for cid, data in parsed.items():
            node = self.nodes.get(claim_id(cid))
            if node:
                node.props.setdefault("negated", data.get("negated"))
                node.props.setdefault("strength", data.get("strength"))
                node.props.setdefault("direction", data.get("direction"))

        # claim-level contradictions (explicit, scored, explained)
        for conflict in self.conflicts:
            a, b = claim_id(conflict["claim_a"]), claim_id(conflict["claim_b"])
            if a in self.nodes and b in self.nodes:
                self._add_edge(
                    a,
                    b,
                    "CONTRADICTS",
                    weight=conflict["score"],
                    score=conflict["score"],
                    kind=conflict["kind"],
                    reasons=conflict["reasons"],
                    provenance="claim-conflict-resolver",
                    predicted=True,
                )
                # paper-level projection of the same evidence (see model notes)
                paper_a = self.nodes[a].props.get("provenance", {}).get("paper_id")
                if paper_a and paper_a in self.nodes:
                    self._add_edge(
                        paper_a,
                        b,
                        "CONTRADICTS",
                        weight=conflict["score"],
                        score=conflict["score"],
                        reasons=conflict["reasons"],
                        provenance="claim-conflict-resolver",
                        predicted=True,
                    )

        # support edges: same paper cluster, aligned stance/direction
        by_paper: dict[str, list[str]] = defaultdict(list)
        for cid, data in parsed.items():
            if data.get("paper_id"):
                by_paper[data["paper_id"]].append(claim_id(cid))
        for record in self._corpus.get("claims", []):
            if record.get("stance") != "supports":
                continue
            source = claim_id(record["id"])
            if source not in self.nodes:
                continue
            for other in self.nodes.values():
                if other.label != "Claim" or other.id == source:
                    continue
                if other.props.get("stance") != "supports":
                    continue
                if other.props.get("provenance", {}).get("paper_id") == record.get("paper"):
                    continue
                src_data = parsed.get(record["id"], {})
                dst_data = parsed.get(other.id, {})
                if src_data.get("direction") and src_data.get("direction") == dst_data.get("direction"):
                    self._add_edge(
                        source,
                        other.id,
                        "SUPPORTS",
                        weight=0.6,
                        provenance="claim-alignment",
                        predicted=True,
                    )

    def _add_edge(self, src: str, dst: str, rel: str, **props: Any) -> None:
        if src == dst or src not in self.nodes or dst not in self.nodes:
            return
        for e in self.edges:
            if e.src == src and e.dst == dst and e.type == rel:
                return
        self.edges.append(GEdge(src=src, dst=dst, type=rel, props={k: v for k, v in props.items() if v is not None}))

    def _build_similarity(self) -> None:
        """Semantic similarity via embeddings + structural link prediction."""
        targets: list[tuple[str, str]] = []
        for node in self.nodes.values():
            if node.label == "Paper":
                targets.append((node.id, f"{node.props.get('title', node.name)}. {node.props.get('abstract', '')}"))
            elif node.label == "Topic":
                targets.append((node.id, f"{node.name} research topic"))
            elif node.label == "Method":
                targets.append((node.id, f"{node.name} method"))
            elif node.label == "Claim":
                targets.append((node.id, node.props.get("text", node.name)))

        batch = embed([text for _id, text in targets])
        self.embedding_engine = batch.engine
        self.embeddings = {tid: vec for (tid, _t), vec in zip(targets, batch.vectors)}

        # Paper <-> Paper semantic neighbours (explicitly a similarity, never a citation)
        paper_ids = [i for i in self.embeddings if i.startswith("paper:")]
        paper_vectors = {i: self.embeddings[i] for i in paper_ids}
        neighbours, _engine = AnalyticsEngine.semantic_neighbours(paper_vectors, k=5, min_similarity=0.25)
        claimed = {(e.src, e.dst) for e in self.edges if e.type in {"CITES", "SIMILAR_TO"}}
        for src, pairs in neighbours.items():
            for dst, sim in pairs:
                if (src, dst) in claimed or (dst, src) in claimed:
                    continue
                self.edges.append(
                    GEdge(
                        src=src,
                        dst=dst,
                        type="SIMILAR_TO",
                        props={
                            "weight": round(sim, 4),
                            "similarity": round(sim, 4),
                            "method": "embedding-cosine",
                            "engine": self.embedding_engine,
                            "predicted": True,
                            "provenance": "semantic-similarity",
                        },
                    )
                )
                self.similarity_pairs.append({"source": src, "target": dst, "similarity": round(sim, 4)})

        # Topic <-> Topic: co-occurrence (structural) + optional embedding hint
        topic_pairs = self._topic_cooccurrence()
        for (a, b), stats in topic_pairs.items():
            if stats["papers"] < 2:
                continue
            self.edges.append(
                GEdge(
                    src=a,
                    dst=b,
                    type="RELATED_TO",
                    props={
                        "weight": round(min(1.0, stats["papers"] / 6.0), 4),
                        "shared_papers": stats["papers"],
                        "jaccard": round(stats["jaccard"], 4),
                        "method": "co-occurrence",
                        "provenance": "shared-paper-analysis",
                    },
                )
            )

        # Link prediction over topics: potential relationships, clearly labelled.
        topic_ids = [i for i in self.nodes if i.startswith("topic:")]
        predicted = AnalyticsEngine.structural_similarity(
            list(self.nodes.values()),
            self.edges,
            node_types={"Topic"},
            limit_pairs=300,
        )
        existing_topics = {(e.src, e.dst) for e in self.edges if e.type == "RELATED_TO"}
        for row in predicted[:60]:
            a, b = row["source"], row["target"]
            if (a, b) in existing_topics or (b, a) in existing_topics:
                continue
            if row["common_neighbors"] < 1 or row["adamic_adar"] <= 0:
                continue
            self.predicted_links.append(row)
            self.edges.append(
                GEdge(
                    src=a,
                    dst=b,
                    type="PREDICTED_LINK",
                    props={
                        "weight": round(row["adamic_adar"], 4),
                        "adamic_adar": row["adamic_adar"],
                        "jaccard": row["jaccard"],
                        "common_neighbors": row["common_neighbors"],
                        "method": "adamic-adar+jaccard",
                        "predicted": True,
                        "provenance": "link-prediction",
                    },
                )
            )
        self.revision += 1

    def _topic_cooccurrence(self) -> dict[tuple[str, str], dict[str, float]]:
        topic_sets: dict[str, set[str]] = defaultdict(set)
        for node in self.nodes.values():
            if node.label == "Paper":
                for e in self.edges:
                    if e.src == node.id and e.type == "STUDIES":
                        topic_sets[e.dst].add(node.id)
        pairs: dict[tuple[str, str], dict[str, float]] = {}
        items = list(topic_sets.items())
        for i, (a, set_a) in enumerate(items):
            for b, set_b in items[i + 1:]:
                shared = set_a & set_b
                if not shared:
                    continue
                union = set_a | set_b
                pairs[(a, b)] = {
                    "papers": float(len(shared)),
                    "jaccard": len(shared) / len(union) if union else 0.0,
                }
        return pairs

    def _assign_communities(self) -> None:
        assert self.analytics is not None
        comm_map = self.analytics.communities
        members: dict[int, list[str]] = defaultdict(list)
        for node_id, comm in comm_map.items():
            members[comm].append(node_id)

        existing = {n.id for n in self.nodes.values() if n.label == "Community"}
        for comm_id, member_ids in sorted(members.items()):
            papers = [m for m in member_ids if m.startswith("paper:")]
            topics = [m for m in member_ids if m.startswith("topic:")]
            if len(papers) < 2 and len(topics) < 3:
                continue  # ignore singletons: they are not "communities"
            top_topics = Counter(self.nodes[t].name for t in topics if t in self.nodes).most_common(3)
            name = " · ".join(t for t, _c in top_topics) or f"Community {comm_id}"
            cid = community_id(comm_id)
            self.nodes[cid] = GNode(
                id=cid,
                label="Community",
                name=name if len(name) <= 70 else name[:67] + "…",
                props={
                    "community_index": comm_id,
                    "paper_count": len(papers),
                    "topic_count": len(topics),
                    "top_topics": [t for t, _c in top_topics],
                },
            )
            for member in member_ids:
                node = self.nodes.get(member)
                if node is None or node.label == "Community":
                    continue
                if node.label in {"Paper", "Topic", "Author", "Method"}:
                    self._add_edge(member, cid, "BELONGS_TO", weight=1.0, provenance="louvain")

        self.communities_by_index = members
        existing |= {n.id for n in self.nodes.values() if n.label == "Community"}

    def _rebuild_community_profiles(self) -> None:
        assert self.analytics is not None
        profiles: list[dict[str, Any]] = []
        for node in self.nodes.values():
            if node.label != "Community":
                continue
            idx = node.props.get("community_index")
            members = self.analytics.communities
            paper_ids = [
                m
                for m, c in members.items()
                if c == idx and m.startswith("paper:")
            ]
            topic_ids = [m for m, c in members.items() if c == idx and m.startswith("topic:")]
            method_ids = [m for m, c in members.items() if c == idx and m.startswith("method:")]
            top_papers = sorted(
                paper_ids,
                key=lambda p: -self.analytics.pagerank.get(p, 0.0),
            )[:6]
            top_topics = sorted(topic_ids, key=lambda t: -self.analytics.pagerank.get(t, 0.0))[:8]
            years = [self.nodes[p].props.get("year") for p in paper_ids if self.nodes[p].props.get("year")]
            profiles.append(
                {
                    "id": node.id,
                    "name": node.name,
                    "community_index": idx,
                    "paper_count": len(paper_ids),
                    "topic_count": len(topic_ids),
                    "method_count": len(method_ids),
                    "top_topics": [self.nodes[t].name for t in top_topics],
                    "top_papers": [
                        {
                            "id": p,
                            "title": self.nodes[p].name,
                            "year": self.nodes[p].props.get("year"),
                            "pagerank": round(self.analytics.pagerank.get(p, 0.0), 5),
                        }
                        for p in top_papers
                    ],
                    "year_range": [min(years), max(years)] if years else None,
                    "avg_pagerank": round(
                        sum(self.analytics.pagerank.get(p, 0.0) for p in paper_ids) / max(1, len(paper_ids)), 6
                    ),
                }
            )
        profiles.sort(key=lambda p: -p["paper_count"])
        for rank, profile in enumerate(profiles):
            self.nodes[profile["id"]].props["rank"] = rank
        self.community_profiles = profiles

    # ----------------------------------------------------------------- API
    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "engine": self.engine_name,
            "revision": self.revision,
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "embedding_engine": self.embedding_engine,
            "claim_engine": getattr(self, "claim_engine", "unknown"),
            "corpus_path": str(self.corpus_path),
            "corpus_version": self._corpus.get("corpus_version"),
            "provenance": self._corpus.get("provenance", {}),
        }

    def stats(self) -> dict[str, Any]:
        counts = Counter(n.label for n in self.nodes.values())
        rel_counts = Counter(e.type for e in self.edges)
        return {
            "nodes": sum(counts.values()),
            "edges": len(self.edges),
            "labels": dict(counts),
            "relationships": dict(rel_counts),
            "communities": len(self.community_profiles),
            "predicted_links": len(self.predicted_links),
            "conflicts": len(self.conflicts),
            "engine": self.engine_name,
            "revision": self.revision,
        }

    def node(self, node_id: str) -> GNode | None:
        return self.nodes.get(node_id)

    def node_detail(self, node_id: str) -> dict[str, Any] | None:
        node = self.nodes.get(node_id)
        if node is None:
            return None
        assert self.analytics is not None
        metrics = self.analytics.metrics_for(node_id)

        neighbours: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for e in self.edges:
            other_id = None
            if e.src == node_id:
                other_id, direction = e.dst, "out"
            elif e.dst == node_id:
                other_id, direction = e.src, "in"
            if other_id is None:
                continue
            other = self.nodes.get(other_id)
            if other is None:
                continue
            neighbours[e.type].append(
                {
                    "id": other.id,
                    "name": other.name,
                    "type": other.label,
                    "direction": direction,
                    "weight": e.props.get("weight"),
                    "predicted": bool(e.props.get("predicted")),
                    "properties": e.props,
                }
            )

        detail: dict[str, Any] = {
            "id": node.id,
            "label": node.name,
            "type": node.label,
            "properties": node.props,
            "metrics": metrics,
            "metrics_explained": explain_metrics(node, metrics, self.community_profiles),
            "neighbours": {k: v[:40] for k, v in neighbours.items()},
            "neighbour_counts": {k: len(v) for k, v in neighbours.items()},
        }
        if node.label == "Paper":
            detail["authors"] = [
                self.nodes[n["id"]].name for n in neighbours.get("AUTHORED", []) if n["id"] in self.nodes
            ]
            detail["topics"] = [self.nodes[n["id"]].name for n in neighbours.get("STUDIES", [])]
            detail["methods"] = [self.nodes[n["id"]].name for n in neighbours.get("USES_METHOD", [])]
            detail["datasets"] = [self.nodes[n["id"]].name for n in neighbours.get("USES_DATASET", [])]
            detail["citations_out"] = [n["id"] for n in neighbours.get("CITES", []) if n["direction"] == "out"]
            detail["cited_by"] = [n["id"] for n in neighbours.get("CITES", []) if n["direction"] == "in"]
            detail["in_graph_citation_degree"] = len(detail["citations_out"]) + len(detail["cited_by"])
            detail["similar_papers"] = [
                {"id": n["id"], "name": n["name"], "similarity": n["properties"].get("similarity")}
                for n in neighbours.get("SIMILAR_TO", [])
            ]
            detail["claims"] = [
                {"id": n["id"], "text": self.nodes[n["id"]].props.get("text"), "stance": self.nodes[n["id"]].props.get("stance")}
                for n in neighbours.get("MAKES_CLAIM", [])
                if n["id"] in self.nodes
            ]
        return detail

    def search(self, text: str, labels: Sequence[str] | None = None, limit: int = 25) -> list[dict[str, Any]]:
        needle = (text or "").strip().lower()
        results: list[dict[str, Any]] = []
        for node in self.nodes.values():
            if labels and node.label not in labels:
                continue
            haystack = f"{node.name} {node.props.get('text', '')} {node.props.get('abstract', '')}".lower()
            if needle and needle not in haystack:
                continue
            score = 0.0
            if needle:
                if node.name.lower() == needle:
                    score += 2.0
                if node.name.lower().startswith(needle):
                    score += 1.0
                score += haystack.count(needle) * 0.2
            if self.analytics:
                score += self.analytics.pagerank.get(node.id, 0.0) * 5.0
            results.append(
                {
                    "id": node.id,
                    "label": node.name,
                    "type": node.label,
                    "score": round(score, 5),
                    "year": node.props.get("year"),
                    "field": node.props.get("field"),
                    "url": node.props.get("url"),
                    "summary_source": node.props.get("summary_source"),
                }
            )
        results.sort(key=lambda r: -r["score"])
        return results[:limit]

    def list_nodes(
        self,
        label: str | None = None,
        limit: int = 50,
        offset: int = 0,
        sort: str = "pagerank",
        order: str = "desc",
        year_min: int | None = None,
        year_max: int | None = None,
        field: str | None = None,
    ) -> dict[str, Any]:
        assert self.analytics is not None
        items = [n for n in self.nodes.values() if (label is None or n.label == label)]
        if year_min is not None:
            items = [n for n in items if (n.props.get("year") or 0) >= year_min]
        if year_max is not None:
            items = [n for n in items if (n.props.get("year") or 9999) <= year_max]
        if field:
            items = [n for n in items if n.props.get("field") == field]

        def key(n: GNode) -> Any:
            if sort == "name":
                return n.name.lower()
            if sort == "pagerank":
                return self.analytics.pagerank.get(n.id, 0.0)
            if sort == "betweenness":
                return self.analytics.betweenness.get(n.id, 0.0)
            if sort == "degree":
                return float(self.analytics.degree.get(n.id, 0))
            if sort == "year":
                return float(n.props.get("year") or 0)
            return float(len(n.name))

        reverse = order != "asc"
        items.sort(key=key, reverse=reverse)
        total = len(items)
        page = items[offset : offset + limit]
        return {
            "total": total,
            "offset": offset,
            "limit": limit,
            "items": [
                {
                    "id": n.id,
                    "label": n.name,
                    "type": n.label,
                    "year": n.props.get("year"),
                    "field": n.props.get("field"),
                    "url": n.props.get("url"),
                    "pagerank": round(self.analytics.pagerank.get(n.id, 0.0), 5),
                    "betweenness": round(self.analytics.betweenness.get(n.id, 0.0), 5),
                    "degree": self.analytics.degree.get(n.id, 0),
                    "community": self.analytics.communities.get(n.id),
                }
                for n in page
            ],
        }

    # ----------------------------------------------------------- subgraphs
    def subgraph(
        self,
        *,
        seeds: Sequence[str] | None = None,
        query: str | None = None,
        depth: int = 1,
        node_types: Sequence[str] | None = None,
        rel_types: Sequence[str] | None = None,
        year_min: int | None = None,
        year_max: int | None = None,
        min_degree: int = 0,
        max_nodes: int | None = None,
        max_edges: int | None = None,
        include_predicted: bool = True,
        limit_seeds: int = 60,
    ) -> Subgraph:
        from backend.config import get_settings

        settings = get_settings()
        max_nodes = max_nodes or settings.server.max_graph_nodes
        max_edges = max_edges or settings.server.max_graph_edges
        assert self.analytics is not None

        if not seeds and query:
            hits = self.search(query, labels=node_types, limit=limit_seeds)
            seeds = [h["id"] for h in hits]
        seeds = [s for s in (seeds or []) if s in self.nodes]
        if not seeds:
            # Default view: the most influential nodes, never the whole graph.
            ranked = sorted(
                self.nodes.values(),
                key=lambda n: -self.analytics.pagerank.get(n.id, 0.0),
            )
            seeds = [n.id for n in ranked[:limit_seeds]]

        allowed_types = set(node_types) if node_types else None
        allowed_rels = set(rel_types) if rel_types else None

        adjacency: dict[str, list[GEdge]] = defaultdict(list)
        for e in self.edges:
            if e.type == "PREDICTED_LINK" and not include_predicted:
                continue
            if allowed_rels and e.type not in allowed_rels:
                continue
            adjacency[e.src].append(e)
            adjacency[e.dst].append(e)

        visited: set[str] = set()
        truncated = False
        frontier = list(dict.fromkeys(seeds))
        for level in range(max(0, min(depth, 3)) + 1):
            next_frontier: list[str] = []
            for node_id in frontier:
                if node_id in visited or len(visited) >= max_nodes:
                    truncated = truncated or len(visited) >= max_nodes
                    continue
                node = self.nodes.get(node_id)
                if node is None:
                    continue
                if allowed_types and node.label not in allowed_types:
                    continue
                if level > 0 and not self._passes_filters(node, self.analytics, year_min, year_max, min_degree):
                    continue
                visited.add(node_id)
                next_frontier.extend(
                    e.dst if e.src == node_id else e.src for e in adjacency.get(node_id, [])
                )
            frontier = next_frontier
            if not frontier:
                break

        sub = Subgraph()
        for node_id in visited:
            sub.add_node(self.nodes[node_id].with_metrics(self.analytics.metrics_for(node_id)))
        edge_count = 0
        for e in self.edges:
            if e.src in visited and e.dst in visited:
                if e.type == "PREDICTED_LINK" and not include_predicted:
                    continue
                if allowed_rels and e.type not in allowed_rels:
                    continue
                if edge_count >= max_edges:
                    truncated = True
                    break
                if sub.add_edge(e):
                    edge_count += 1

        # Report the truncation on the payload, not just in a local: the front end keys its
        # "truncated (expand on demand)" hint off `truncated`, and a bounded view that stays
        # silent about the bound is exactly the kind of quiet half-truth this prototype avoids.
        sub.truncated = truncated
        if truncated:
            sub.notes.append(
                f"View truncated to {len(sub.nodes)} nodes / {len(sub.edges)} edges "
                "(progressive expansion keeps the first paint fast — expand a node to see more)."
            )
        return sub

    @staticmethod
    def _passes_filters(
        node: GNode, analytics: AnalyticsResult, year_min: int | None, year_max: int | None, min_degree: int
    ) -> bool:
        year = node.props.get("year")
        if year_min is not None and (year or 0) < year_min:
            return False
        if year_max is not None and (year or 9999) > year_max:
            return False
        if min_degree and analytics.degree.get(node.id, 0) < min_degree:
            return False
        return True

    def expand(self, node_ids: Sequence[str], rel_types: Sequence[str] | None = None, limit: int = 60) -> Subgraph:
        sub = Subgraph()
        visited: set[str] = set()
        for node_id in node_ids:
            node = self.nodes.get(node_id)
            if node:
                sub.add_node(node)
                visited.add(node_id)
        added = 0
        for e in self.edges:
            if added >= limit:
                sub.truncated = True
                break
            if rel_types and e.type not in rel_types:
                continue
            if e.src in visited and e.dst not in visited:
                target = self.nodes.get(e.dst)
                if target and sub.add_node(target):
                    visited.add(e.dst)
                    added += 1
                sub.add_edge(e)
            elif e.dst in visited and e.src not in visited:
                source = self.nodes.get(e.src)
                if source and sub.add_node(source):
                    visited.add(e.src)
                    added += 1
                sub.add_edge(e)
        return sub

    def neighbours(self, node_id: str, rel_types: Sequence[str] | None = None) -> list[dict[str, Any]]:
        out = []
        for e in self.edges:
            if rel_types and e.type not in rel_types:
                continue
            if e.src == node_id:
                other = e.dst
            elif e.dst == node_id:
                other = e.src
            else:
                continue
            node = self.nodes.get(other)
            if node:
                out.append({"id": node.id, "label": node.name, "type": node.label, "rel": e.type, "properties": e.props})
        return out

    def top(self, metric: str = "pagerank", label: str = "Paper", limit: int = 10) -> list[dict[str, Any]]:
        assert self.analytics is not None
        values = {
            "pagerank": self.analytics.pagerank,
            "betweenness": self.analytics.betweenness,
            "degree": {k: float(v) for k, v in self.analytics.degree.items()},
        }.get(metric, self.analytics.pagerank)
        ranked = sorted(
            (n for n in self.nodes.values() if n.label == label),
            key=lambda n: -values.get(n.id, 0.0),
        )[:limit]
        return [
            {
                "id": n.id,
                "label": n.name,
                "type": n.label,
                "value": round(values.get(n.id, 0.0), 6),
                "degree": self.analytics.degree.get(n.id, 0),
                "year": n.props.get("year"),
                "community": self.analytics.communities.get(n.id),
                "url": n.props.get("url"),
            }
            for n in ranked
        ]

    def communities(self) -> list[dict[str, Any]]:
        return self.community_profiles

    def community_of(self, node_id: str) -> int | None:
        return self.analytics.communities.get(node_id) if self.analytics else None

    def projection(
        self, node_types: Sequence[str] | None = None, rel_types: Sequence[str] | None = None
    ) -> tuple[list[str], list[tuple[str, str, float]]]:
        return AnalyticsEngine._project(
            list(self.nodes.values()), self.edges, set(node_types) if node_types else None, set(rel_types) if rel_types else None
        )

    def conflicts_payload(self, limit: int = 40) -> list[dict[str, Any]]:
        out = []
        for conflict in self.conflicts[:limit]:
            claim_a = self.nodes.get(claim_id(conflict["claim_a"]))
            claim_b = self.nodes.get(claim_id(conflict["claim_b"]))
            if not claim_a or not claim_b:
                continue
            out.append(
                {
                    **conflict,
                    "text_a": claim_a.props.get("text", claim_a.name),
                    "text_b": claim_b.props.get("text", claim_b.name),
                    "source_a": conflict.get("paper_a"),
                    "source_b": conflict.get("paper_b"),
                    "url_a": self.nodes.get(conflict.get("paper_a") or "", GNode("", "", "")).props.get("url")
                    if conflict.get("paper_a")
                    else None,
                    "url_b": self.nodes.get(conflict.get("paper_b") or "", GNode("", "", "")).props.get("url")
                    if conflict.get("paper_b")
                    else None,
                    "label": "Potential contradiction — AI-detected hypothesis, not an established fact",
                    "engine": getattr(self, "claim_engine", "unknown"),
                }
            )
        return out


def explain_metrics(node: GNode, metrics: dict[str, float], profiles: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Plain-language explanation of a node's graph metrics."""
    out: list[dict[str, str]] = []
    pr = metrics.get("pagerank", 0.0)
    bc = metrics.get("betweenness", 0.0)
    degree = int(metrics.get("degree", 0))
    comm = int(metrics.get("community", -1))

    if pr > 0:
        strength = "top-tier" if pr > 0.01 else "notable" if pr > 0.004 else "modest"
        out.append(
            {
                "metric": "PageRank",
                "value": f"{pr:.5f}",
                "meaning": (
                    f"PageRank measures influence by how much highly-connected research points at this node. "
                    f"At {pr:.5f} this is {strength} influence within the analyzed corpus."
                ),
            }
        )
    if bc > 0:
        bridge = bc > 0.01
        out.append(
            {
                "metric": "Betweenness centrality",
                "value": f"{bc:.5f}",
                "meaning": (
                    "Betweenness counts how often this node sits on the shortest path between others. "
                    + (
                        "This node acts as a bridge between otherwise separated research areas."
                        if bridge
                        else "Its bridging role is limited: most paths do not need to pass through it."
                    )
                ),
            }
        )
    out.append(
        {
            "metric": "Degree",
            "value": str(degree),
            "meaning": f"{degree} direct relationships were collected in the graph for this node.",
        }
    )
    if comm >= 0:
        profile = next((p for p in profiles if p["community_index"] == comm), None)
        out.append(
            {
                "metric": "Research community",
                "value": str(comm),
                "meaning": (
                    f"Louvain community detection places this node in community {comm}"
                    + (f" — '{profile['name']}' ({profile['paper_count']} papers)." if profile else ".")
                ),
            }
        )
    return out
