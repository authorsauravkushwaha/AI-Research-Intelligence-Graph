"""The NEXUS research-agent tool suite (§24).

Every tool is a thin, validated wrapper over the graph store, so the same
capability is reachable from the UI, the REST API, the agent and (through the
MCP server in `mcp/`) an external MCP client. Tools return *structured* evidence:
ids, metrics and provenance, never prose, so the reasoning step is always able to
show where a statement came from.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Sequence

log = logging.getLogger("nexus.tools")


@dataclass(slots=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., dict[str, Any]]

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


class ToolRegistry:
    """Holds the tools and executes them with validated arguments."""

    def __init__(self) -> None:
        self.tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def catalogue(self) -> list[dict[str, Any]]:
        return [t.to_json() for t in self.tools.values()]

    def execute(self, name: str, arguments: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        tool = self.tools.get(name)
        if tool is None:
            return {"ok": False, "error": f"unknown tool '{name}'", "available": list(self.tools)}
        args = dict(arguments or {})
        args.update(kwargs)
        try:
            return {"ok": True, "tool": name, "result": tool.handler(**args)}
        except TypeError as exc:
            return {"ok": False, "tool": name, "error": f"invalid arguments: {exc}"}
        except Exception as exc:  # noqa: BLE001 - a tool failure must not break the agent
            log.exception("tool %s failed", name)
            return {"ok": False, "tool": name, "error": str(exc)}


def _int(value: Any, default: int, low: int = 1, high: int = 500) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, parsed))


def build_registry(store: Any, gap_engine: Any, graphrag: Any) -> ToolRegistry:
    registry = ToolRegistry()

    # ------------------------------------------------------------ papers ---
    def search_papers(query: str = "", limit: Any = 12, year_min: Any = None, year_max: Any = None) -> dict[str, Any]:
        hits = store.search(query, labels=["Paper"], limit=_int(limit, 12, 1, 100))
        if year_min is not None:
            hits = [h for h in hits if (h.get("year") or 0) >= int(year_min)]
        if year_max is not None:
            hits = [h for h in hits if (h.get("year") or 9999) <= int(year_max)]
        return {
            "query": query,
            "count": len(hits),
            "papers": [
                {
                    **h,
                    "note": "title/abstract text match over the analyzed corpus; "
                    "abstracts are editorial summaries for the demo records",
                }
                for h in hits
            ],
        }

    registry.register(
        Tool(
            "search_papers",
            "Lexical + graph-ranked search over papers in the corpus.",
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "keywords, e.g. 'agent memory'"},
                    "limit": {"type": "integer", "default": 12},
                    "year_min": {"type": "integer"},
                    "year_max": {"type": "integer"},
                },
                "required": ["query"],
            },
            search_papers,
        )
    )

    def get_paper(paper_id: str) -> dict[str, Any]:
        node_id = paper_id if ":" in paper_id else f"paper:{paper_id}"
        detail = store.node_detail(node_id)
        if detail is None:
            return {"found": False, "paper_id": paper_id, "hint": "use search_papers first"}
        return {"found": True, **detail}

    registry.register(
        Tool(
            "get_paper",
            "Full record for one paper: metadata, topics, methods, citations, claims, metrics.",
            {"type": "object", "properties": {"paper_id": {"type": "string"}}, "required": ["paper_id"]},
            get_paper,
        )
    )

    def find_related_papers(paper_id: str, limit: Any = 10) -> dict[str, Any]:
        node_id = paper_id if ":" in paper_id else f"paper:{paper_id}"
        if node_id not in store.nodes:
            return {"found": False, "paper_id": paper_id}
        related: dict[str, dict[str, Any]] = {}
        for edge in store.edges:
            if edge.type not in {"SIMILAR_TO", "CITES", "RELATED_TO"}:
                continue
            other = None
            if edge.src == node_id:
                other = edge.dst
            elif edge.dst == node_id:
                other = edge.src
            if other is None or other not in store.nodes or not other.startswith("paper:"):
                continue
            node = store.nodes[other]
            entry = related.setdefault(
                other,
                {
                    "id": other,
                    "title": node.name,
                    "year": node.props.get("year"),
                    "url": node.props.get("url"),
                    "similarity": None,
                    "citation_link": False,
                    "predicted": bool(edge.props.get("predicted")),
                    "why": [],
                },
            )
            if edge.type == "SIMILAR_TO":
                entry["similarity"] = edge.props.get("similarity")
                entry["why"].append("semantic similarity")
            elif edge.type == "CITES":
                entry["citation_link"] = True
                entry["why"].append("citation lineage (curated subset)")
            else:
                entry["why"].append("shared concept")
        items = sorted(related.values(), key=lambda r: (-(r["similarity"] or 0), r["title"]))[:_int(limit, 10, 1, 50)]
        return {"paper_id": node_id, "count": len(items), "related": items}

    registry.register(
        Tool(
            "find_related_papers",
            "Papers similar to, citing, or sharing concepts with a given paper.",
            {"type": "object", "properties": {"paper_id": {"type": "string"}, "limit": {"type": "integer"}}},
            find_related_papers,
        )
    )

    # -------------------------------------------------------- communities ---
    def find_research_communities(topic: Any = None, limit: Any = 10) -> dict[str, Any]:
        profiles = store.communities()
        if topic:
            needle = str(topic).lower()
            profiles = [
                p
                for p in profiles
                if needle in p["name"].lower()
                or any(needle in t.lower() for t in p.get("top_topics", []))
            ]
        profiles = profiles[:_int(limit, 10, 1, 40)]
        return {
            "count": len(profiles),
            "engine": store.analytics.engines.get("louvain") if store.analytics else "unknown",
            "method": "Louvain community detection over the paper/topic/method graph",
            "communities": profiles,
        }

    registry.register(
        Tool(
            "find_research_communities",
            "Research communities discovered by Louvain community detection.",
            {"type": "object", "properties": {"topic": {"type": "string"}, "limit": {"type": "integer"}}},
            find_research_communities,
        )
    )

    # ------------------------------------------------------- bridge nodes ---
    def find_bridge_nodes(label: Any = "Paper", limit: Any = 10) -> dict[str, Any]:
        label = str(label or "Paper")
        items = store.top("betweenness", label, _int(limit, 10, 1, 50))
        for item in items:
            item["explanation"] = (
                "High betweenness: many shortest paths between other nodes pass through this one, "
                "so it connects otherwise separated research areas."
                if item["value"] > 0
                else "No measurable bridging role in the analyzed corpus."
            )
        return {
            "label": label,
            "metric": "betweenness centrality (Brandes)",
            "engine": store.analytics.engines.get("betweenness") if store.analytics else "unknown",
            "nodes": items,
        }

    registry.register(
        Tool(
            "find_bridge_nodes",
            "Nodes with the highest betweenness centrality (structural bridges between communities).",
            {"type": "object", "properties": {"label": {"type": "string"}, "limit": {"type": "integer"}}},
            find_bridge_nodes,
        )
    )

    # --------------------------------------------------- predicted links ---
    def find_potential_connections(topic: Any = None, limit: Any = 10) -> dict[str, Any]:
        limit = _int(limit, 10, 1, 50)
        links = store.predicted_links
        if topic:
            needle = str(topic).lower()
            links = [
                link
                for link in links
                if needle in store.nodes[link["source"]].name.lower()
                or needle in store.nodes[link["target"]].name.lower()
            ]
        out = [
            {
                **link,
                "source_label": store.nodes[link["source"]].name,
                "target_label": store.nodes[link["target"]].name,
                "label": "Predicted research connection — hypothesis, not an observed link",
            }
            for link in links[:limit]
        ]
        return {
            "count": len(out),
            "method": "Adamic-Adar + Jaccard link prediction over the concept graph",
            "disclaimer": "Predicted links are unobserved relationships in this corpus. They are research "
            "hypotheses, not findings.",
            "connections": out,
        }

    registry.register(
        Tool(
            "find_potential_connections",
            "Predicted (unobserved) relationships between topics/concepts, with scores.",
            {"type": "object", "properties": {"topic": {"type": "string"}, "limit": {"type": "integer"}}},
            find_potential_connections,
        )
    )

    # ------------------------------------------------------------- gaps ---
    def find_research_gaps(
        topic: str = "",
        year_min: Any = None,
        year_max: Any = None,
        top_k: Any = 5,
    ) -> dict[str, Any]:
        result = gap_engine.find_gaps(
            topic or None,
            year_min=int(year_min) if year_min else None,
            year_max=int(year_max) if year_max else None,
            top_k=_int(top_k, 5, 1, 12),
        )
        return result

    registry.register(
        Tool(
            "find_research_gaps",
            "Runs the NEXUS Research Gap Engine: candidate underexplored cluster pairs with scores.",
            {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "year_min": {"type": "integer"},
                    "year_max": {"type": "integer"},
                    "top_k": {"type": "integer"},
                },
                "required": ["topic"],
            },
            find_research_gaps,
        )
    )

    # ---------------------------------------------------------- evidence ---
    def get_evidence(opportunity_id: Any = None, topic: Any = None, index: Any = 0) -> dict[str, Any]:
        gaps = gap_engine.find_gaps(str(topic) if topic else "AI Agents", top_k=12)
        opportunities = gaps.get("opportunities", [])
        if opportunity_id:
            opportunities = [o for o in opportunities if o["id"] == opportunity_id]
        if not opportunities:
            return {"found": False, "hint": "run find_research_gaps first and reuse one of its ids"}
        chosen = opportunities[min(int(index or 0), len(opportunities) - 1)]
        return {
            "found": True,
            "opportunity": chosen,
            "safety_notice": gaps.get("safety_notice"),
            "note": "Evidence below is the exact graph payload behind the opportunity — papers, bridge "
            "papers, score components and any detected claim tensions.",
        }

    registry.register(
        Tool(
            "get_evidence",
            "Returns the full evidence bundle (papers, bridges, scores, conflicts) for an opportunity.",
            {
                "type": "object",
                "properties": {
                    "opportunity_id": {"type": "string"},
                    "topic": {"type": "string"},
                    "index": {"type": "integer"},
                },
            },
            get_evidence,
        )
    )

    # ------------------------------------------------- conflicting claims ---
    def find_conflicting_claims(topic: Any = None, limit: Any = 10) -> dict[str, Any]:
        limit = _int(limit, 10, 1, 40)
        conflicts = store.conflicts_payload(limit=200)
        if topic:
            needle = str(topic).lower()
            conflicts = [
                c
                for c in conflicts
                if needle in (c.get("text_a", "") + c.get("text_b", "")).lower()
                or needle in str(c.get("source_a", "")).lower()
                or needle in str(c.get("source_b", "")).lower()
            ]
        note = ""
        if topic and not conflicts:
            # Never return "nothing" when the corpus does contain tensions: report
            # the strongest overall and say the filter matched none.
            all_conflicts = store.conflicts_payload(limit=200)
            note = f"No tension matched the filter '{topic}'. Showing the strongest tensions in the corpus instead."
            conflicts = all_conflicts
        return {
            "count": len(conflicts[:limit]),
            "note": note,
            "total_detected": len(conflicts),
            "engine": getattr(store, "claim_engine", "unknown"),
            "method": "lexical claim resolver: polarity + effect-direction opposition with shared concept tokens",
            "disclaimer": "Potential contradictions detected by NEXUS. They are hypotheses to check by reading the "
            "papers, not established disagreements.",
            "conflicts": conflicts[:limit],
        }

    registry.register(
        Tool(
            "find_conflicting_claims",
            "Claim-level tensions (potential contradictions) detected across the corpus.",
            {"type": "object", "properties": {"topic": {"type": "string"}, "limit": {"type": "integer"}}},
            find_conflicting_claims,
        )
    )

    # ------------------------------------------------ connecting papers ---
    def find_connecting_papers(area_a: str = "", area_b: str = "", limit: Any = 12) -> dict[str, Any]:
        """Papers that touch both research areas — the direct answer to
        "which papers connect X and Y?" (the demo's most valuable question)."""
        limit = _int(limit, 12, 1, 40)
        if not area_a or not area_b:
            return {"found": False, "error": "area_a and area_b are required"}

        def resolve(text: str) -> list[str]:
            _, topics, _ = gap_engine.scope(text, min_papers=1)
            return topics

        topics_a = resolve(area_a)
        topics_b = resolve(area_b)
        set_a, set_b = set(topics_a), set(topics_b)
        if not set_a or not set_b:
            return {"found": False, "error": f"could not resolve areas: {area_a!r} / {area_b!r}"}

        bridges = []
        for node in store.nodes.values():
            if node.label != "Paper":
                continue
            topics = {e.dst for e in store.edges if e.src == node.id and e.type == "STUDIES"}
            touches_a = topics & set_a
            touches_b = topics & set_b
            if touches_a and touches_b:
                bridges.append(
                    {
                        "id": node.id,
                        "title": node.name,
                        "year": node.props.get("year"),
                        "url": node.props.get("url"),
                        "connects": sorted(
                            store.nodes[t].name for t in touches_a if t in store.nodes
                        )
                        + ["↔"]
                        + sorted(store.nodes[t].name for t in touches_b if t in store.nodes),
                        "pagerank": round(store.analytics.pagerank.get(node.id, 0.0), 6)
                        if store.analytics
                        else None,
                    }
                )
        bridges.sort(key=lambda b: -(b["pagerank"] or 0))

        # also give the shortest structural path between the two areas
        anchor_a = sorted(set_a & set(store.nodes))[:1]
        anchor_b = sorted(set_b & set(store.nodes))[:1]
        path = None
        if anchor_a and anchor_b:
            path = graphrag.paths_between(anchor_a[0], anchor_b[0], max_hops=4)

        return {
            "area_a": {"query": area_a, "topics": [store.nodes[t].name for t in topics_a[:8]]},
            "area_b": {"query": area_b, "topics": [store.nodes[t].name for t in topics_b[:8]]},
            "bridge_paper_count": len(bridges),
            "bridge_papers": bridges[:limit],
            "shortest_path": path,
            "note": "Bridge papers are papers that the corpus records as studying topics from both areas. "
            "Their count is a lower bound on the real connection, which may exist outside this corpus.",
        }

    registry.register(
        Tool(
            "find_connecting_papers",
            "Papers that connect two research areas, plus the shortest graph path between them.",
            {
                "type": "object",
                "properties": {
                    "area_a": {"type": "string"},
                    "area_b": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["area_a", "area_b"],
            },
            find_connecting_papers,
        )
    )

    # ------------------------------------------------------- graph query ---
    def query_graph(node_type: Any = "Paper", sort: Any = "pagerank", limit: Any = 20, year_min: Any = None) -> dict[str, Any]:
        label = str(node_type or "Paper")
        result = store.list_nodes(
            label=label,
            limit=_int(limit, 20, 1, 200),
            sort=str(sort or "pagerank"),
            year_min=int(year_min) if year_min else None,
        )
        return {
            "query": {
                "node_type": label,
                "sort": sort,
                "limit": limit,
                "year_min": year_min,
            },
            "engine": store.engine_name,
            "cypher_equivalent": _cypher_hint(label, sort, limit),
            **result,
        }

    registry.register(
        Tool(
            "query_graph",
            "Structured graph query: list nodes of a type, sorted by a graph metric.",
            {
                "type": "object",
                "properties": {
                    "node_type": {"type": "string", "enum": ["Paper", "Topic", "Method", "Dataset", "Author", "Claim", "Community"]},
                    "sort": {"type": "string", "enum": ["pagerank", "betweenness", "degree", "year"]},
                    "limit": {"type": "integer"},
                    "year_min": {"type": "integer"},
                },
            },
            query_graph,
        )
    )

    # --------------------------------------------------- path / metrics ---
    def shortest_evidence_path(source: str = "", target: str = "") -> dict[str, Any]:
        if not source or not target:
            return {"found": False, "error": "source and target node ids are required"}
        path = graphrag.paths_between(source, target)
        if not path:
            return {"found": False, "error": "no path found within 4 hops"}
        return {"found": True, **path, "label": "Graph evidence path (shortest relationship chain)"}

    registry.register(
        Tool(
            "shortest_evidence_path",
            "Shortest relationship path between two nodes — used for the evidence-graph highlight.",
            {"type": "object", "properties": {"source": {"type": "string"}, "target": {"type": "string"}}},
            shortest_evidence_path,
        )
    )

    def graph_metrics(node_id: str = "") -> dict[str, Any]:
        if node_id and node_id in store.nodes:
            return {"found": True, **(store.node_detail(node_id) or {})}
        return {
            "found": False,
            "available_metrics": ["pagerank", "betweenness", "degree", "community", "similarity"],
            "engine": store.analytics.engines if store.analytics else {},
            "top_pagerank_papers": store.top("pagerank", "Paper", 5),
            "top_bridge_papers": store.top("betweenness", "Paper", 5),
        }

    registry.register(
        Tool(
            "graph_metrics",
            "Graph metrics for a node (or the global rankings): PageRank, betweenness, degree, community.",
            {"type": "object", "properties": {"node_id": {"type": "string"}}},
            graph_metrics,
        )
    )

    return registry


def _cypher_hint(label: str, sort: str, limit: Any) -> str:
    metric = {"pagerank": "n.pagerank", "betweenness": "n.betweenness", "degree": "n.degree", "year": "n.year"}.get(
        str(sort), "n.pagerank"
    )
    safe_label = label if label.isalpha() else "Paper"
    return (
        f"MATCH (n:{safe_label}) RETURN n.id, n.name ORDER BY {metric} DESC LIMIT {_int(limit, 20, 1, 200)}"
    )
