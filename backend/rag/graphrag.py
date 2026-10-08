"""GraphRAG retrieval for NEXUS.

The differentiator is stated plainly: **vector search alone is not enough**.
A question like "which papers connect agent memory to multi-agent coordination?"
has no good answer in the top-k nearest neighbours of the question text, because
the answer is a *path*, not a document. So retrieval here is:

    question
      -> embed                                  (semantic entry point)
      -> vector search over papers/topics/claims/ (candidates)
      -> graph expansion (1-2 hops, whitelisted)  (the relationships)
      -> community + centrality enrichment        (structure)
      -> claim/evidence collection                (contradictions, support)
      -> structured context with provenance        (what the LLM sees)

Everything the LLM sees is returned in `GraphContext.citations` too, so the API
can answer "show me the evidence" without another round trip.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from backend.models.graph import ASSOCIATIVE_RELS
from backend.rag.embeddings import cosine, embed

#: Relationship whitelist used for expansion (keeps the context focused).
EXPANSION_RELS = (
    "STUDIES",
    "USES_METHOD",
    "USES_DATASET",
    "CITES",
    "MAKES_CLAIM",
    "BELONGS_TO",
    "SIMILAR_TO",
    "RELATED_TO",
    "PREDICTED_LINK",
    "AUTHORED",
    "CONTRADICTS",
    "SUPPORTS",
)

MAX_CONTEXT_NODES = 90
MAX_CONTEXT_CLAIMS = 25


@dataclass(slots=True)
class Retrieved:
    id: str
    label: str
    type: str
    score: float
    reason: str
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GraphContext:
    """Structured, provenance-carrying context handed to the reasoning step."""

    question: str
    seeds: list[Retrieved] = field(default_factory=list)
    papers: list[dict[str, Any]] = field(default_factory=list)
    topics: list[dict[str, Any]] = field(default_factory=list)
    methods: list[dict[str, Any]] = field(default_factory=list)
    datasets: list[dict[str, Any]] = field(default_factory=list)
    authors: list[dict[str, Any]] = field(default_factory=list)
    communities: list[dict[str, Any]] = field(default_factory=list)
    claims: list[dict[str, Any]] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    paths: list[dict[str, Any]] = field(default_factory=list)
    predicted_links: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    retrieval_engine: str = "unknown"
    expansion_engine: str = "unknown"

    def to_prompt(self, max_chars: int = 9000) -> str:
        """Render the graph context as text for the LLM (bounded, cited, ordered)."""
        lines: list[str] = []
        lines.append(f"QUESTION: {self.question}")
        lines.append("")
        lines.append("GRAPH CONTEXT (assembled by NEXUS GraphRAG — every item is traceable):")
        lines.append("")

        if self.metrics:
            lines.append("CORPUS METRICS: " + ", ".join(f"{k}={v}" for k, v in self.metrics.items()))
            lines.append("")

        if self.communities:
            lines.append("RESEARCH COMMUNITIES (Louvain, with centrality):")
            for c in self.communities[:6]:
                lines.append(
                    f"- {c['name']} | papers={c.get('paper_count')} | top topics: "
                    f"{', '.join(c.get('top_topics', [])[:4])} | top papers: "
                    f"{', '.join(p['title'][:60] for p in c.get('top_papers', [])[:2])}"
                )
            lines.append("")

        if self.papers:
            lines.append("PAPERS (ranked by relevance + graph importance):")
            for p in self.papers[:18]:
                lines.append(
                    f"- [{p['id']}] {p['title']} ({p.get('year', 'n/a')}) "
                    f"| pagerank={p.get('pagerank', 0):.5f} | community={p.get('community')} "
                    f"| topics: {', '.join(p.get('topics', [])[:5])} "
                    f"| methods: {', '.join(p.get('methods', [])[:4])} "
                    f"| url: {p.get('url')} | evidence: {p.get('reason', '')}"
                )
            lines.append("")

        if self.topics:
            lines.append("TOPICS:")
            for t in self.topics[:14]:
                lines.append(
                    f"- {t['label']} | papers={t.get('paper_count', '?')} | degree={t.get('degree')} "
                    f"| betweenness={t.get('betweenness', 0):.5f}"
                )
            lines.append("")

        if self.methods:
            lines.append("METHODS in scope: " + ", ".join(m["label"] for m in self.methods[:16]))
            lines.append("")
        if self.datasets:
            lines.append("DATASETS in scope: " + ", ".join(d["label"] for d in self.datasets[:12]))
            lines.append("")

        if self.claims:
            lines.append("CLAIMS (with source paper and stance):")
            for c in self.claims[:14]:
                lines.append(f"- [{c['id']}] ({c.get('stance')}) {c['text'][:220]} — source: {c.get('paper')}")
            lines.append("")

        if self.conflicts:
            lines.append("DETECTED CLAIM TENSIONS (potential contradictions, not established facts):")
            for f in self.conflicts[:6]:
                lines.append(
                    f"- score={f['score']} kind={f['kind']}: \"{f['text_a'][:120]}\" VS \"{f['text_b'][:120]}\""
                    f" | reasons: {'; '.join(f.get('reasons', [])[:2])}"
                )
            lines.append("")

        if self.paths:
            lines.append("GRAPH PATHS between retrieved entities:")
            for path in self.paths[:6]:
                chain = " -> ".join(f"{hop['label']}({hop['type']})" for hop in path.get("hops", []))
                lines.append(f"- {chain} | length={path.get('hops_count')}")
            lines.append("")

        if self.predicted_links:
            lines.append("PREDICTED RESEARCH CONNECTIONS (link prediction — clearly hypothetical):")
            for link in self.predicted_links[:6]:
                lines.append(
                    f"- {link['source_label']} ~ {link['target_label']} "
                    f"(adamic_adar={link['adamic_adar']:.3f}, shared neighbours={link['common_neighbors']})"
                )
            lines.append("")

        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n… [context truncated to fit the model window]"
        return text

    def evidence_list(self) -> list[dict[str, Any]]:
        """Papers + claims the answer must cite (used by /api/agent/ask responses)."""
        evidence = []
        for p in self.papers[:12]:
            evidence.append(
                {
                    "id": p["id"],
                    "kind": "paper",
                    "title": p.get("title") or p.get("label") or p["id"],
                    "year": p.get("year"),
                    "url": p.get("url"),
                    "reason": p.get("reason"),
                    "pagerank": p.get("pagerank"),
                    "community": p.get("community"),
                    "summary_source": p.get("summary_source"),
                }
            )
        for c in self.claims[:8]:
            evidence.append(
                {
                    "id": c["id"],
                    "kind": "claim",
                    "title": c["text"][:180],
                    "stance": c.get("stance"),
                    "source_paper": c.get("paper"),
                    "url": c.get("url"),
                }
            )
        return evidence


class VectorIndex:
    """Small in-memory vector index over the graph's nodes.

    Brute-force cosine over a few hundred vectors is faster than the round trip to
    a vector database, and it keeps the demo dependency-free. `search` is O(n·d)
    which is ~10ms for the demo corpus; the interface is the same one a pgvector
    or Neo4j vector-index implementation would expose.
    """

    def __init__(self, store: Any) -> None:
        self.store = store
        self.engine = getattr(store, "embedding_engine", "unknown")

    def search(
        self,
        query: str,
        labels: Sequence[str] | None = None,
        top_k: int = 12,
        min_score: float = 0.05,
    ) -> list[Retrieved]:
        if not query.strip():
            return []
        qvec = embed([query]).vectors[0]
        scored: list[Retrieved] = []
        for node_id, vec in self.store.embeddings.items():
            node = self.store.nodes.get(node_id)
            if node is None:
                continue
            if labels and node.label not in labels:
                continue
            score = cosine(qvec, vec)
            if score >= min_score:
                scored.append(
                    Retrieved(
                        id=node_id,
                        label=node.name,
                        type=node.label,
                        score=score,
                        reason="vector similarity to the question",
                        properties=node.props,
                    )
                )
        scored.sort(key=lambda r: -r.score)
        return scored[:top_k]


class GraphRAG:
    """Vector retrieval + graph expansion + structured evidence assembly."""

    def __init__(self, store: Any) -> None:
        self.store = store
        self.index = VectorIndex(store)

    # ------------------------------------------------------------- helpers
    def _describe(self, node_id: str, reason: str, score: float = 0.0) -> dict[str, Any]:
        node = self.store.nodes[node_id]
        metrics = self.store.analytics.metrics_for(node_id) if self.store.analytics else {}
        return {
            "id": node.id,
            "label": node.name,
            "type": node.label,
            "reason": reason,
            "score": round(score, 4),
            "year": node.props.get("year"),
            "url": node.props.get("url"),
            "field": node.props.get("field"),
            "summary_source": node.props.get("summary_source"),
            "author_status": node.props.get("author_status"),
            "pagerank": round(metrics.get("pagerank", 0.0), 6),
            "betweenness": round(metrics.get("betweenness", 0.0), 6),
            "degree": metrics.get("degree", 0),
            "community": metrics.get("community"),
        }

    def expand(
        self,
        seed_ids: Iterable[str],
        depth: int = 2,
        rel_types: Sequence[str] = EXPANSION_RELS,
        max_nodes: int = MAX_CONTEXT_NODES,
    ) -> set[str]:
        allowed = set(rel_types)
        visited: set[str] = set()
        frontier = [s for s in seed_ids if s in self.store.nodes]
        for _level in range(max(1, min(depth, 3))):
            next_frontier: list[str] = []
            for node_id in frontier:
                if node_id in visited or len(visited) >= max_nodes:
                    continue
                visited.add(node_id)
                for edge in self.store.edges:
                    if edge.type not in allowed:
                        continue
                    if edge.src == node_id and edge.dst not in visited:
                        next_frontier.append(edge.dst)
                    elif edge.dst == node_id and edge.src not in visited:
                        next_frontier.append(edge.src)
            frontier = next_frontier
            if not frontier:
                break
        return visited

    def paths_between(self, a: str, b: str, max_hops: int = 4) -> dict[str, Any] | None:
        """Shortest relationship path (evidence path for the UI highlight)."""
        from backend.algorithms import kernel

        ids, proj = self.store.projection()
        path = kernel.shortest_path(ids, proj, a, b)
        if not path or len(path) > max_hops + 1:
            return None
        hops = []
        for src, dst in zip(path, path[1:]):
            rel = "RELATED"
            for edge in self.store.edges:
                if (edge.src == src and edge.dst == dst) or (edge.dst == src and edge.src == dst):
                    rel = edge.type
                    break
            hops.append(
                {
                    "from": src,
                    "to": dst,
                    "label": f"{self.store.nodes[src].name} —{rel}→ {self.store.nodes[dst].name}",
                    "type": rel,
                }
            )
        return {
            "nodes": path,
            "hops": hops,
            "hops_count": len(hops),
            "node_labels": [{"id": n, "type": self.store.nodes[n].label, "label": self.store.nodes[n].name} for n in path],
        }

    # ------------------------------------------------------------ retrieve
    def retrieve(
        self,
        question: str,
        *,
        labels: Sequence[str] | None = None,
        top_k: int = 12,
        depth: int = 2,
        include_conflicts: bool = True,
        extra_seed_ids: Sequence[str] = (),
        focus_nodes: Sequence[str] = (),
    ) -> GraphContext:
        seeds = self.index.search(question, labels=labels, top_k=top_k)
        seed_ids = [s.id for s in seeds] + [s for s in extra_seed_ids if s in self.store.nodes]

        # Focus nodes (e.g. a research-gap cluster pair) are always expanded.
        for node_id in focus_nodes:
            if node_id in self.store.nodes and node_id not in seed_ids:
                seed_ids.append(node_id)
                seeds.append(
                    Retrieved(
                        id=node_id,
                        label=self.store.nodes[node_id].name,
                        type=self.store.nodes[node_id].label,
                        score=1.0,
                        reason="selected context (focus node)",
                        properties=self.store.nodes[node_id].props,
                    )
                )

        expanded = self.expand(seed_ids, depth=depth)
        ctx = GraphContext(question=question, seeds=seeds)
        ctx.retrieval_engine = self.index.engine
        ctx.expansion_engine = (self.store.analytics.engines.get("pagerank") if self.store.analytics else "n/a") or "n/a"

        counts = self.store.stats()
        ctx.metrics = {
            "papers": counts["labels"].get("Paper", 0),
            "authors": counts["labels"].get("Author", 0),
            "topics": counts["labels"].get("Topic", 0),
            "methods": counts["labels"].get("Method", 0),
            "datasets": counts["labels"].get("Dataset", 0),
            "claims": counts["labels"].get("Claim", 0),
            "communities": counts["communities"],
            "seeds_retrieved": len(seeds),
            "context_nodes": len(expanded),
        }

        for node_id in expanded:
            node = self.store.nodes.get(node_id)
            if node is None:
                continue
            if node.label == "Paper":
                described = self._describe(node_id, "reached by graph expansion")
                described["title"] = node.name  # papers are addressed by title everywhere downstream
                topics = [self.store.nodes[n].name for n in self._neighbours(node_id, "STUDIES")]
                methods = [self.store.nodes[n].name for n in self._neighbours(node_id, "USES_METHOD")]
                described["topics"] = topics
                described["methods"] = methods
                described["abstract"] = node.props.get("abstract", "")
                ctx.papers.append(described)
            elif node.label == "Topic":
                ctx.topics.append(self._describe(node_id, "concept in the retrieved neighbourhood"))
            elif node.label == "Method":
                ctx.methods.append(self._describe(node_id, "method in the retrieved neighbourhood"))
            elif node.label == "Dataset":
                ctx.datasets.append(self._describe(node_id, "dataset in the retrieved neighbourhood"))
            elif node.label == "Author":
                ctx.authors.append(self._describe(node_id, "author of a retrieved paper"))
            elif node.label == "Claim":
                described = self._describe(node_id, "claim made by a retrieved paper")
                described["text"] = node.props.get("text", node.name)
                described["stance"] = node.props.get("stance")
                described["paper"] = node.props.get("provenance", {}).get("paper_id")
                described["url"] = node.props.get("provenance", {}).get("url")
                ctx.claims.append(described)

        # communities covering the retrieved papers
        paper_communities: dict[int, list[str]] = {}
        for paper in ctx.papers:
            community = paper.get("community")
            if community is None or community < 0:
                continue
            paper_communities.setdefault(community, []).append(paper["id"])
        for community_index, member_ids in sorted(paper_communities.items(), key=lambda kv: -len(kv[1]))[:6]:
            profile = next(
                (p for p in self.store.communities() if p["community_index"] == community_index),
                None,
            )
            if profile:
                ctx.communities.append(
                    {
                        **profile,
                        "retrieved_papers": len(member_ids),
                    }
                )

        if include_conflicts:
            involved = {p["id"] for p in ctx.papers}
            for conflict in self.store.conflicts_payload(limit=25):
                if conflict.get("source_a") in involved or conflict.get("source_b") in involved:
                    ctx.conflicts.append(conflict)

        # predicted links touching the retrieved concepts
        retrieved_topics = {t["id"] for t in ctx.topics}
        for link in self.store.predicted_links[:80]:
            if link["source"] in retrieved_topics or link["target"] in retrieved_topics:
                ctx.predicted_links.append(
                    {
                        **link,
                        "source_label": self.store.nodes[link["source"]].name,
                        "target_label": self.store.nodes[link["target"]].name,
                    }
                )

        # evidence paths between the two most central retrieved papers
        top_papers = sorted(ctx.papers, key=lambda p: -p["pagerank"])[:4]
        for i, a in enumerate(top_papers):
            for b in top_papers[i + 1:]:
                path = self.paths_between(a["id"], b["id"], max_hops=3)
                if path:
                    ctx.paths.append({**path, "between": [a["id"], b["id"]]})
                    break

        ctx.papers.sort(key=lambda p: -(p["score"] or 0) - p["pagerank"] * 30)
        ctx.claims = ctx.claims[:MAX_CONTEXT_CLAIMS]
        ctx.citations = ctx.evidence_list()
        return ctx

    def _neighbours(self, node_id: str, rel: str) -> list[str]:
        out = []
        for edge in self.store.edges:
            if edge.type != rel:
                continue
            if edge.src == node_id:
                out.append(edge.dst)
            elif edge.dst == node_id:
                out.append(edge.src)
        return [n for n in out if n in self.store.nodes]

    # ------------------------------------------------------------- answers
    def answer_offline(self, ctx: GraphContext) -> dict[str, Any]:
        """Deterministic, evidence-first answer used when no LLM key is configured.

        This is not a fake LLM: it is a template that reports exactly what the graph
        returned, in the same structure the LLM answer uses, so the demo, the tests
        and the CI all exercise the same response shape. The API labels it
        `answer_engine: "graph-template"` so nobody mistakes it for model output.
        """
        parts: list[str] = []
        top = ctx.papers[:5]
        if top:
            parts.append(
                "Based on the analyzed corpus, the most structurally relevant records are: "
                + "; ".join(f"{p['title']} ({p.get('year') or 'n/a'})" for p in top)
                + "."
            )
        if ctx.communities:
            parts.append(
                "They sit in the research communities "
                + ", ".join(c["name"] for c in ctx.communities[:3])
                + "."
            )
        if ctx.topics:
            parts.append(
                "Recurring concepts: " + ", ".join(t["label"] for t in ctx.topics[:6]) + "."
            )
        if ctx.conflicts:
            worst = ctx.conflicts[0]
            parts.append(
                f"The graph also flags a possible tension (score {worst['score']}) between "
                f"\"{worst['text_a'][:110]}\" and \"{worst['text_b'][:110]}\" — potential, not established."
            )
        if ctx.predicted_links:
            link = ctx.predicted_links[0]
            parts.append(
                f"A predicted (not observed) connection links {link['source_label']} and {link['target_label']}."
            )
        if not parts:
            parts.append(
                "The graph returned no nodes for this question. Try a broader term such as "
                "'AI agents', 'agent memory' or 'multi-agent systems'."
            )
        parts.append(
            "This answer was assembled directly from graph evidence without an LLM "
            "(no LLM key configured); every statement above maps to a node or edge in the response."
        )
        return {
            "answer": " ".join(parts),
            "answer_engine": "graph-template",
            "confidence": "low-medium",
            "confidence_basis": "deterministic template over retrieved graph evidence",
            "used_llm": False,
        }
