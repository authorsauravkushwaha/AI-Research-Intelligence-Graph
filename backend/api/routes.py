"""NEXUS REST API (§33).

One place builds the engine, one place exposes it: every route below reads from
the same `MemoryGraphStore` interface (backed by Neo4j or the in-process engine),
the same `GapEngine`, the same `GraphRAG` and the same tool registry the agent and
the future MCP server use.

Design rules enforced here:
* graph payloads are always paged or seeded (never the whole graph),
* every intelligent answer carries its evidence and a confidence basis,
* the opportunity score always travels with its formula and safety notice,
* predicted links are always labelled as predictions,
* a missing LLM key never breaks a route.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import PlainTextResponse

from backend.agents.agent import ResearchAgent
from backend.agents.llm import LLMClient
from backend.agents.tools import build_registry
from backend.api.schemas import (
    AgentRequest,
    PlanRequest,
    ExpandRequest,
    ExportRequest,
    GapRequest,
    GraphRequest,
    ReportRequest,
)
import datetime as _dt

from backend.config import capability_flags, get_settings
from backend.engine.explorer import Explorer
from backend.models.graph import ASSOCIATIVE_RELS, NODE_COLORS, REL_TYPES
from backend.engine.gaps import SAFETY_NOTICE, GapEngine, score_model
from backend.graph.factory import get_state
from backend.rag.graphrag import GraphRAG
from backend.services.export import export_graph
from backend.services.planner import plan as plan_query
from backend.services.sidecars import status as sidecar_status
from backend.services.report import ReportService

router = APIRouter(prefix="/api")

_settings = get_settings()
_state = get_state()
_store = _state.store
_gap_engine = GapEngine(_store)
_graphrag = GraphRAG(_store)
_registry = build_registry(_store, _gap_engine, _graphrag)
_agent = ResearchAgent(_store, _gap_engine, _registry, _graphrag)
_explorer = Explorer(_store, _gap_engine)
_reports = ReportService(_store, _explorer, _agent)
_started = time.time()


# ------------------------------------------------------------------ helpers
def _status() -> dict[str, Any]:
    return {
        **_state.status(),
        "engine": _store.engine_name,
        "revision": _store.revision,
        "uptime_seconds": round(time.time() - _started, 1),
    }


def _store_health() -> dict[str, Any]:
    health = _store.health()
    health["backend"] = _status()
    return health


# --------------------------------------------------------------------- meta
@router.get("/health", tags=["meta"], summary="Engine status, capability flags and graph counts")
def health() -> dict[str, Any]:
    flags = capability_flags()
    store = _store
    health_payload = _store_health()
    degraded = _state.degraded or not flags.get("neo4j_configured", False)
    notices = list(_state.warnings)
    if not flags.get("llm_configured"):
        notices.append(
            "No LLM key configured: the agent answers from graph evidence with deterministic templates "
            "instead of generated prose (every number and citation is still real)."
        )
    return {
        "status": "ok" if not _state.degraded else "degraded",
        "version": _settings.version,
        "demo_mode": _settings.server.demo_mode,
        "capabilities": flags,
        "store": health_payload,
        "algorithms": store.analytics.engines if store.analytics else {},
        "counts": store.stats(),
        "notices": notices,
        "safety_notice": SAFETY_NOTICE,
        "degraded": degraded,
    }


@router.get("/tools", tags=["meta"], summary="The shared research tool suite (API + agent + MCP)")
def tools() -> dict[str, Any]:
    return {"count": len(_registry.tools), "tools": _registry.catalogue()}


@router.get("/algorithms", tags=["meta"], summary="Graph algorithms in use, with what each is for")
def algorithms() -> dict[str, Any]:
    engines = _store.analytics.engines if _store.analytics else {}
    return {
        "engines": engines,
        "analytics_engine": _store.engine_name,
        "algorithms": [
            {
                "id": "similarity",
                "label": "Semantic similarity",
                "engine": _store.embedding_engine,
                "purpose": "SIMILAR_TO edges between papers, retrieved by GraphRAG before graph expansion.",
                "implementation": "embedding cosine similarity (3-tier: external API → local hashing → lexical overlap).",
            },
            {
                "id": "community_detection",
                "label": "Community detection",
                "engine": engines.get("louvain", "unknown"),
                "purpose": "Research communities; the separation component of the opportunity score.",
                "implementation": "Louvain modularity optimisation (native C++ kernel, resolution 1.2).",
            },
            {
                "id": "centrality",
                "label": "Centrality",
                "engine": engines.get("pagerank", "unknown"),
                "purpose": "Influence (PageRank) and structural bridges (betweenness) — never 'importance' as a claim about science.",
                "implementation": "PageRank + Brandes betweenness (native C++ kernel; betweenness sampled above 400 nodes).",
            },
            {
                "id": "link_prediction",
                "label": "Link prediction",
                "engine": engines.get("link_prediction", "unknown"),
                "purpose": "Candidate research connections, always labelled as hypotheses.",
                "implementation": "Adamic–Adar + Jaccard + common neighbours on the co-occurrence projection.",
            },
        ],
        "safety_notice": SAFETY_NOTICE,
    }


@router.get("/opportunity-score", tags=["meta"], summary="Transparent NEXUS Opportunity Score model")
def opportunity_score() -> dict[str, Any]:
    return {
        "name": "NEXUS Opportunity Score",
        "label": "prototype heuristic — not a validated scientific metric",
        "formula": score_model(),
        "safety_notice": SAFETY_NOTICE,
    }


@router.get("/safety", tags=["meta"], summary="Safety, provenance and data-honesty notices")
def safety() -> dict[str, Any]:
    return {
        "safety_notice": SAFETY_NOTICE,
        "provenance": _store.health().get("provenance", {}),
        "rules": [
            "NEXUS never claims that nobody has researched a topic.",
            "Every AI conclusion is a hypothesis and carries its graph evidence.",
            "Predicted connections are labelled predictions, not observations.",
            "Scores are prototype heuristics; the formula is shown next to every score.",
            "Real corpus records (arXiv ids/titles/urls) and editorial text are labelled separately.",
        ],
    }


@router.get("/mcp", tags=["meta"], summary="MCP tool manifest exposed for external agents")
def mcp_manifest() -> dict[str, Any]:
    return {
        "name": "nexus-research-intelligence",
        "version": _settings.version,
        "description": "Research-graph intelligence tools (GraphRAG + gap engine) exposed over the shared registry.",
        "tools": [
            {"name": tool.name, "description": tool.description, "input_schema": tool.parameters}
            for tool in _registry.tools.values()
        ],
    }


# --------------------------------------------------------------------- home
@router.get("/dashboard", tags=["home"], summary="Everything the Home dashboard renders (§27)")
def dashboard() -> dict[str, Any]:
    payload = _explorer.dashboard()
    payload["status"] = _status()
    payload["capabilities"] = capability_flags()
    payload["analytics"] = _store.analytics.engines if _store.analytics else {}
    return payload


@router.get("/timeline", tags=["home"], summary="Papers per year and per-topic activity")
def timeline(topic: str | None = Query(default=None, max_length=160)) -> dict[str, Any]:
    store = _store
    years: dict[int, int] = {}
    topic_years: dict[str, dict[int, int]] = {}
    paper_ids, topic_ids, _ = _gap_engine.scope(topic, min_papers=1)
    scoped = set(paper_ids)
    for edge in store.edges:
        if edge.type != "STUDIES":
            continue
        paper = store.nodes.get(edge.src)
        if paper is None or paper.label != "Paper":
            continue
        if scoped and paper.id not in scoped:
            continue
        year = paper.props.get("year")
        if not year:
            continue
        years[year] = years.get(year, 0) + 1
        topic_name = store.nodes[edge.dst].name if edge.dst in store.nodes else None
        if topic_name:
            topic_years.setdefault(topic_name, {})
            topic_years[topic_name][year] = topic_years[topic_name].get(year, 0) + 1
    top_topics = sorted(topic_years.items(), key=lambda kv: -sum(kv[1].values()))[:12]
    return {
        "topic": topic,
        "papers_per_year": dict(sorted(years.items())),
        "topics_over_time": {name: dict(sorted(counts.items())) for name, counts in top_topics},
    }


# ---------------------------------------------------------- research explorer
@router.get("/explorer", tags=["explorer"], summary="Explorer metrics for a scope (§9)")
def explorer(
    topic: str | None = Query(default=None, max_length=160),
    year_min: int | None = Query(default=None, ge=1900, le=2100),
    year_max: int | None = Query(default=None, ge=1900, le=2100),
    field: str | None = Query(default=None, max_length=120),
) -> dict[str, Any]:
    payload = _explorer.overview(topic, year_min, year_max, field)
    payload["status"] = _status()
    return payload


@router.get("/search", tags=["explorer"], summary="Search nodes (papers, topics, methods, datasets, authors)")
def search(
    q: str = Query(min_length=1, max_length=200),
    labels: list[str] | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    allowed = {"Paper", "Author", "Topic", "Method", "Dataset", "Claim"}
    selected = [label for label in (labels or []) if label in allowed] or None
    results = _store.search(q, labels=selected, limit=limit)
    return {"query": q, "count": len(results), "results": results}


@router.get("/papers", tags=["papers"], summary="Paged paper list with filters")
def papers(
    q: str | None = Query(default=None, max_length=200),
    topic: str | None = Query(default=None, max_length=160),
    field: str | None = Query(default=None, max_length=120),
    year_min: int | None = Query(default=None, ge=1900, le=2100),
    year_max: int | None = Query(default=None, ge=1900, le=2100),
    sort: Literal["pagerank", "betweenness", "degree", "year"] = "pagerank",
    order: Literal["desc", "asc"] = "desc",
    limit: int = Query(default=40, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=5000),
) -> dict[str, Any]:
    page = _store.list_nodes(label="Paper", limit=limit, offset=offset, sort=sort, order=order,
                             year_min=year_min, year_max=year_max, field=field)
    if topic:
        paper_ids, _, _ = _gap_engine.scope(topic, year_min, year_max, min_papers=1)
        keep = set(paper_ids)
        page["items"] = [item for item in page["items"] if item["id"] in keep]
        page["total"] = len(page["items"])
        page["filtered_by_topic"] = topic
    if q:
        hits = {hit["id"] for hit in _store.search(q, labels=["Paper"], limit=500)}
        page["items"] = [item for item in page["items"] if item["id"] in hits]
        page["filtered_by_query"] = q
        page["total"] = len(page["items"])   # `total` describes the rows the caller gets
    page["sort"] = sort
    return page


@router.get("/papers/{paper_id}", tags=["papers"], summary="One paper with metrics, evidence and neighbourhood")
def paper(paper_id: str) -> dict[str, Any]:
    detail = _store.node_detail(paper_id)
    if detail is None or detail["type"] != "Paper":
        raise HTTPException(status_code=404, detail=f"paper {paper_id} not found")
    detail["community"] = next(
        (profile for profile in _store.communities() if profile["community_index"] == _store.community_of(paper_id)),
        None,
    )
    detail["predicted_links"] = [
        {**link, "direction": "out" if link["source"] == paper_id else "in"}
        for link in _store.predicted_links
        if paper_id in {link["source"], link["target"]}
    ]
    detail["conflicts"] = [
        conflict
        for conflict in _store.conflicts_payload(limit=200)
        if paper_id in {conflict.get("paper_a"), conflict.get("paper_b")}
    ]
    detail["provenance"] = {
        "record": "arXiv metadata (id, title, url) as listed in the public corpus index",
        "summary": detail["properties"].get("summary_source", "unknown"),
        "authors": detail["properties"].get("author_status", "not-collected"),
    }
    return detail


# ------------------------------------------------------------------- nodes
@router.get("/nodes", tags=["graph"], summary="Paged node listing for any label")
def nodes(
    label: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
    sort: Literal["pagerank", "betweenness", "degree", "year", "name"] = "pagerank",
    order: Literal["desc", "asc"] = "desc",
    year_min: int | None = Query(default=None, ge=1900, le=2100),
    year_max: int | None = Query(default=None, ge=1900, le=2100),
    field: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=10000),
) -> dict[str, Any]:
    page = _store.list_nodes(label=label, limit=limit, offset=offset, sort=sort, order=order,
                             year_min=year_min, year_max=year_max, field=field)
    if q:
        hits = {hit["id"] for hit in _store.search(q, labels=[label] if label else None, limit=500)}
        page["items"] = [item for item in page["items"] if item["id"] in hits]
        page["total"] = len(page["items"])
    return page


@router.get("/nodes/{node_id:path}", tags=["graph"], summary="Node detail with metrics explained and neighbours")
def node_detail(node_id: str) -> dict[str, Any]:
    detail = _store.node_detail(node_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"node {node_id} not found")
    return detail


# ------------------------------------------------------------------- graph
def _graph_payload(request: GraphRequest) -> dict[str, Any]:
    sub = _store.subgraph(
        seeds=request.seeds,
        query=request.query,
        depth=request.depth,
        node_types=request.node_types,
        rel_types=request.rel_types,
        year_min=request.year_min,
        year_max=request.year_max,
        include_predicted=request.include_predicted,
        max_nodes=request.max_nodes,
        max_edges=request.max_edges,
    )
    payload = sub.to_json()
    payload["focus"] = None
    if request.focus:
        focus_id = request.focus if request.focus in {n.id for n in sub.nodes} else None
        if focus_id is None:
            for node in sub.nodes:
                if node.name.lower() == request.focus.lower():
                    focus_id = node.id
                    break
        if focus_id:
            payload["focus"] = {"id": focus_id, "neighbours": _store.neighbours(focus_id)}
    payload["legend"] = {
        "node_colors": NODE_COLORS,
        "rel_types": REL_TYPES,
        "predicted_rels": ASSOCIATIVE_RELS,
    }
    return payload


@router.post("/graph", tags=["graph"], summary="Bounded graph payload (paged / seeded)")
def graph(request: GraphRequest) -> dict[str, Any]:
    payload = _graph_payload(request)
    payload["status"] = _status()
    payload["request"] = request.model_dump(exclude_none=True)
    return payload


@router.get("/graph", tags=["graph"], summary="Default graph view (most influential nodes)")
def graph_default(
    topic: str | None = Query(default=None, max_length=160),
    year_min: int | None = Query(default=None, ge=1900, le=2100),
    year_max: int | None = Query(default=None, ge=1900, le=2100),
    depth: int = Query(default=1, ge=0, le=3),
    max_nodes: int = Query(default=260, ge=10, le=400),
) -> dict[str, Any]:
    request = GraphRequest(query=topic, year_min=year_min, year_max=year_max, depth=depth, max_nodes=max_nodes)
    payload = _graph_payload(request)
    payload["status"] = _status()
    return payload


@router.post("/graph/expand", tags=["graph"], summary="Expand-on-demand: one hop from the given nodes")
def graph_expand(request: ExpandRequest) -> dict[str, Any]:
    sub = _store.expand(request.node_ids, rel_types=request.rel_types, limit=request.limit)
    payload = sub.to_json()
    payload["status"] = _status()
    return payload


@router.get("/graph/path", tags=["graph"], summary="Shortest relationship path (evidence path)")
def graph_path(
    source: str = Query(min_length=2),
    target: str = Query(min_length=2),
    max_hops: int = Query(default=4, ge=1, le=6),
) -> dict[str, Any]:
    path = _graphrag.paths_between(source, target, max_hops=max_hops)
    if path is None:
        return {"found": False, "reason": f"No path of {max_hops} hops or fewer between {source} and {target}", "hops": []}
    return {"found": True, **path}


@router.get("/graph/neighbours/{node_id:path}", tags=["graph"])
def neighbours(node_id: str, limit: int = Query(default=60, ge=1, le=300)) -> dict[str, Any]:
    if node_id not in _store.nodes:
        raise HTTPException(status_code=404, detail=f"node {node_id} not found")
    return {
        "center": _store.nodes[node_id].to_json(),
        "neighbours": _store.neighbours(node_id)[:limit],
        "count": len(_store.neighbours(node_id)),
    }


# ------------------------------------------------------------- communities
@router.get("/communities", tags=["graph"], summary="Detected research communities")
def communities() -> dict[str, Any]:
    profiles = _store.communities()
    return {
        "count": len(profiles),
        "algorithm": _store.analytics.engines.get("louvain", "louvain") if _store.analytics else "louvain",
        "communities": profiles,
        "note": "Communities are algorithmic clusters of the analyzed corpus; they are not claims about the field.",
    }


@router.get("/communities/{index}", tags=["graph"], summary="One community with its members")
def community(index: int) -> dict[str, Any]:
    profile = next((p for p in _store.communities() if p["community_index"] == index), None)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"community {index} not found")
    members = [
        node.to_json()
        for node in _store.nodes.values()
        if _store.community_of(node.id) == index and node.label in {"Paper", "Topic", "Method"}
    ]
    return {**profile, "members": members[:160], "member_count": len(members)}


# ----------------------------------------------------- evidence & predictions
@router.get("/conflicts", tags=["intelligence"], summary="Claim-level tensions (potential contradictions)")
def conflicts(limit: int = Query(default=40, ge=1, le=200)) -> dict[str, Any]:
    payload = _store.conflicts_payload(limit=limit)
    return {
        "count": len(payload),
        "engine": getattr(_store, "claim_engine", "unknown"),
        "method": "explicit opposition detection (polarity / effect direction) with shared normalised concept tokens",
        "disclaimer": "These are AI-detected hypotheses to check by reading the papers — not established disagreements.",
        "conflicts": payload,
    }


@router.get("/predictions", tags=["intelligence"], summary="Predicted research connections (hypotheses)")
def predictions(limit: int = Query(default=25, ge=1, le=200)) -> dict[str, Any]:
    rows = []
    for link in _store.predicted_links[:limit]:
        source = _store.nodes.get(link["source"])
        target = _store.nodes.get(link["target"])
        if not source or not target:
            continue
        rows.append(
            {
                **link,
                "source_label": source.name,
                "target_label": target.name,
                "source_type": source.label,
                "target_type": target.label,
                "label": "Predicted research connection — hypothesis, not an observed relationship",
            }
        )
    return {
        "count": len(rows),
        "engine": _store.analytics.engines.get("link_prediction", "heuristic") if _store.analytics else "heuristic",
        "method": "Adamic–Adar, Jaccard and common-neighbour link prediction over the topic co-occurrence projection",
        "disclaimer": "Predicted links are algorithmic hypotheses. They must never be read as facts about the literature.",
        "predictions": rows,
    }


@router.get("/centrality", tags=["intelligence"], summary="Centrality leaderboards (never a claim of scientific quality)")
def centrality(
    metric: Literal["pagerank", "betweenness", "degree"] = Query(default="pagerank"),
    label: Literal["Paper", "Topic", "Method", "Dataset", "Author"] = Query(default="Paper"),
    limit: int = Query(default=15, ge=1, le=100),
) -> dict[str, Any]:
    return {
        "metric": metric,
        "label": label,
        "engine": _store.analytics.engines.get(metric, "unknown") if _store.analytics else "unknown",
        "meaning": {
            "pagerank": "structural influence inside the analyzed corpus",
            "betweenness": "how often a node lies on shortest paths — a bridge between research areas",
            "degree": "number of recorded connections",
        }[metric],
        "items": _store.top(metric, label, limit),
        "note": "Metrics describe this corpus' structure, not the scientific value of a paper.",
    }


# --------------------------------------------------------------- gap finder
@router.post("/gaps", tags=["intelligence"], summary="FIND RESEARCH GAPS — the NEXUS gap engine")
def gaps(request: GapRequest) -> dict[str, Any]:
    payload = _gap_engine.find_gaps(
        request.topic,
        request.year_min,
        request.year_max,
        field=request.field,
        min_papers=request.min_papers,
        top_k=request.top_k,
    )
    payload["generated_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    payload["status"] = _status()
    return payload


@router.post("/gaps/{index}/evidence", tags=["intelligence"], summary="Evidence bundle for one candidate")
def gap_evidence(index: int, request: GapRequest) -> dict[str, Any]:
    payload = _gap_engine.find_gaps(request.topic, request.year_min, request.year_max, field=request.field,
                                     min_papers=request.min_papers, top_k=max(index + 1, 5))
    opportunities = payload.get("opportunities", [])
    if index >= len(opportunities):
        raise HTTPException(status_code=404, detail=f"opportunity {index} not in this result set")
    opportunity = opportunities[index]
    cluster_names = [opportunity["cluster_a"]["name"], opportunity["cluster_b"]["name"]]
    papers = opportunity["evidence_papers"]
    ids = [paper["id"] for paper in papers][:40]
    sub = _store.expand(ids, limit=120)
    return {
        "opportunity": opportunity,
        "evidence_subgraph": sub.to_json(),
        "cluster_profiles": [
            profile for profile in _store.communities() if profile["name"] in cluster_names
        ],
        "score_formula": payload.get("score_formula"),
        "safety_notice": payload.get("safety_notice"),
    }


# ------------------------------------------------------------------- agent
@router.post("/agent", tags=["agent"], summary="Ask the AI Research Agent (§21-§24)")
def agent(request: AgentRequest) -> dict[str, Any]:
    result = _agent.ask(request.question, depth=request.depth, top_k=request.top_k, topic_hint=request.topic_hint)
    payload = result.to_json()
    payload["status"] = _status()
    return payload


@router.post("/agent/stream", tags=["agent"], summary="Same answer, streamed as pipeline stages (SSE)")
def agent_stream(request: AgentRequest):
    import json as _json

    from fastapi.responses import StreamingResponse

    stages = _agent.ask_stages(request.question, depth=request.depth, top_k=request.top_k,
                               topic_hint=request.topic_hint)

    def events():
        try:
            for event in stages:
                yield f"event: {event['stage']}\ndata: {_json.dumps(event, default=str)}\n\n"
        except Exception as exc:  # noqa: BLE001 - the stream must always terminate cleanly
            yield f"event: error\ndata: {_json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ reports
@router.post("/report", tags=["reports"], summary="Research Opportunity Report payload (§28)")
def report(request: ReportRequest) -> dict[str, Any]:
    summary = request.executive_summary
    if summary is None and LLMClient().available:
        summary = _llm_summary(request)
    payload = _reports.build(request.topic, request.year_min, request.year_max, top_k=request.top_k,
                             executive_summary=summary)
    payload["status"] = _status()
    return payload


@router.post("/report/markdown", tags=["reports"], response_class=PlainTextResponse,
             summary="The same report as a Markdown document")
def report_markdown(request: ReportRequest) -> PlainTextResponse:
    summary = request.executive_summary
    if summary is None and LLMClient().available:
        summary = _llm_summary(request)
    payload = _reports.build(request.topic, request.year_min, request.year_max, top_k=request.top_k,
                             executive_summary=summary)
    return PlainTextResponse(_reports.to_markdown(payload), media_type="text/markdown; charset=utf-8")


def _llm_summary(request: ReportRequest) -> str | None:
    """Optional executive summary written by the configured LLM over graph evidence."""
    llm = LLMClient()
    if not llm.available:
        return None
    gaps = _gap_engine.find_gaps(request.topic, request.year_min, request.year_max, top_k=3)
    facts = [
        f"topic: {gaps.get('topic')}",
        f"papers in scope: {gaps.get('scope', {}).get('papers')}",
        f"safety notice: {gaps.get('safety_notice')}",
    ]
    for opportunity in gaps.get("opportunities", []):
        facts.append(
            f"{opportunity['title']} — score {opportunity['opportunity_score']}/100, "
            f"confidence {opportunity['confidence']}, trajectory {opportunity['trajectory']['status']}; "
            f"{opportunity['hypothesis']}"
        )
    summary = llm.complete(
        "Write a 120-word executive summary for a research opportunity report. Use ONLY the facts given. "
        "Never claim a topic is unstudied; describe candidates as hypotheses.",
        "\n".join(facts),
    )
    return summary.strip() if summary else None


# ------------------------------------------------------------------ exports
@router.post("/export", tags=["reports"], summary="Export the graph (GraphML / CSV / JSON / Cypher)")
def export(request: ExportRequest) -> Any:
    result = export_graph(_store, format=request.format, node_types=request.node_types,
                          rel_types=request.rel_types, limit=request.limit)
    if request.format == "json":
        return result
    return PlainTextResponse(result, media_type={
        "graphml": "application/graphml+xml",
        "csv": "text/csv",
        "cypher": "text/plain",
    }[request.format])


@router.get("/export/cypher", tags=["reports"], summary="The Cypher that would recreate this graph in Neo4j",
            response_class=PlainTextResponse)
def export_cypher(limit: int = Query(default=400, ge=10, le=2000)) -> PlainTextResponse:
    script = export_graph(_store, format="cypher", limit=limit)
    return PlainTextResponse(script, media_type="text/plain")


@router.post("/ask", tags=["agent"], summary="Alias for /api/agent (used by the demo script)")
def ask(request: AgentRequest) -> dict[str, Any]:
    return agent(request)


@router.post("/plan", tags=["meta"], summary="Plan a graph query — parameterised Cypher, explanation, cost")
def plan(request: PlanRequest) -> dict[str, Any]:
    """The query planner behind the graph views.

    The Kotlin planner (polyglot/kotlin) is the reference implementation; when the
    sidecar is not running the Python port in `backend/services/planner.py` answers.
    Either way the response carries the DSL, the parameterised Cypher, the bind
    parameters, a human explanation and a cost estimate — and names its engine.
    """
    return _plan_payload(request.query)


@router.get("/plan", tags=["meta"], summary="Same planner, reachable with a plain query string")
def plan_get(
    q: str = Query(min_length=1, max_length=400, description="NEXUS query DSL"),
) -> dict[str, Any]:
    return _plan_payload(q)


def _plan_payload(dsl: str) -> dict[str, Any]:
    payload = plan_query(dsl)
    payload["dsl"] = dsl
    if not payload.get("ok"):
        raise HTTPException(status_code=422, detail=payload.get("error", "query rejected"))
    payload["note"] = (
        "Cypher here is generated, not executed: labels and relationship types come from the "
        "whitelist and every value is a bind parameter."
    )
    return payload


@router.get("/services", tags=["meta"], summary="Polyglot sidecars: what is live, what falls back to Python")
def services() -> dict[str, Any]:
    return sidecar_status()


@router.get("/echo", tags=["meta"], include_in_schema=False)
def echo() -> dict[str, Any]:
    return {"ok": True, "version": _settings.version, "time": time.time()}
