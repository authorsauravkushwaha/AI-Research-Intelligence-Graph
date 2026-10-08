"""End-to-end HTTP surface (offline: in-process engine, no LLM key)."""

from __future__ import annotations

import pytest


def test_health_reports_engine_and_capabilities(api):
    payload = api.get("/api/health").json()
    assert payload["status"] in {"ok", "degraded"}
    assert payload["store"]["engine"]
    assert "llm_configured" in payload["capabilities"]
    assert payload["counts"]["nodes"] > 400
    assert payload["safety_notice"]


def test_dashboard_payload(api):
    payload = api.get("/api/dashboard").json()
    assert payload["cards"]["papers"] > 100
    assert payload["communities"]
    assert payload["safety_notice"]
    assert payload["provenance"]["notice"]


def test_gaps_endpoint_carries_the_full_audit_trail(api):
    response = api.post("/api/gaps", json={"topic": "AI Agents", "top_k": 3})
    assert response.status_code == 200
    payload = response.json()
    assert payload["generated_at"]
    assert payload["score_formula"] and payload["methodology"] and payload["limitations"]
    assert payload["opportunities"]


def test_graph_payload_is_bounded(api):
    payload = api.post("/api/graph", json={"query": "memory", "max_nodes": 60, "max_edges": 200}).json()
    assert len(payload["nodes"]) <= 60 and len(payload["edges"]) <= 200
    assert payload["legend"]["node_colors"]


def test_expand_and_node_detail(api):
    detail = api.get("/api/nodes/paper:2310.08560").json()
    assert detail["label"].startswith("MemGPT")
    expanded = api.post("/api/graph/expand", json={"node_ids": ["paper:2310.08560"], "limit": 10}).json()
    assert expanded["nodes"]


def test_predictions_and_conflicts_are_labelled(api):
    predictions = api.get("/api/predictions").json()
    assert "hypothes" in predictions["disclaimer"].lower()
    conflicts = api.get("/api/conflicts").json()
    assert "not established" in conflicts["disclaimer"].lower()


def test_agent_endpoint(api):
    payload = api.post("/api/agent", json={"question": "Which papers are the most important?"}).json()
    assert payload["answer"]
    assert payload["used_llm"] is False
    assert payload["explainability"]["claim"]


def test_report_markdown_and_exports(api):
    markdown = api.post("/api/report/markdown", json={"topic": "AI Agents", "top_k": 2})
    assert markdown.status_code == 200
    body = markdown.text
    assert "NEXUS RESEARCH OPPORTUNITY REPORT" in body
    assert "prototype heuristic" in body
    assert "independently validated" in body
    graphml = api.post("/api/export", json={"format": "graphml", "limit": 40})
    assert graphml.status_code == 200 and "<graphml" in graphml.text
    cypher = api.get("/api/export/cypher?limit=40").text
    assert "MERGE" in cypher


def test_meta_endpoints(api):
    assert api.get("/api/tools").json()["count"] >= 10
    assert api.get("/api/opportunity-score").json()["formula"]["components"]
    assert api.get("/api/safety").json()["rules"]
    manifest = api.get("/api/mcp").json()
    assert manifest["tools"] and manifest["tools"][0]["input_schema"]


def test_unknown_node_returns_404(api):
    assert api.get("/api/nodes/paper:nope").status_code == 404
    assert api.get("/api/papers/paper:nope").status_code == 404


def test_rate_limit_headers_present(api):
    response = api.get("/api/health")
    assert "x-response-time-ms" in {k.lower() for k in response.headers}
