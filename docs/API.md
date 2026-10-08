# NEXUS HTTP API

Every endpoint below is live on the running server; the authoritative, always-current
schema is served by FastAPI at **`/openapi.json`** with interactive docs at **`/docs`**.

Base URL: `http://localhost:8000`. All responses are JSON unless noted. CORS is
allow-listed (`NEXUS_CORS_ORIGINS`), requests are rate limited
(`NEXUS_RATE_LIMIT_PER_MINUTE`, default 240/min → `429` with `Retry-After`), and every
response carries `X-NEXUS-Engine` and `X-Response-Time-ms`.

> Graph-shaped responses are **bounded by design**: `max_nodes` ≤ 400, `max_edges` ≤ 1200,
> `depth` ≤ 3. Use `/api/graph/expand` for interactive exploration instead of asking for
> the whole graph.

---

## Meta

### `GET /api/health`

Engine in use, why, capability flags and graph counts. The first thing to check when the
UI looks empty.

```json
{
  "status": "ok",
  "capabilities": { "neo4j_configured": false, "llm_configured": false, "embeddings_provider": false },
  "store": {
    "engine": "in-process",
    "backend": { "backend": "in-process", "requested_backend": "in-process",
                 "degraded": false, "reason": "Neo4j is not configured …", "warnings": [] }
  },
  "counts": { "nodes": 670, "edges": 3003, "labels": { "Paper": 198, "…": 0 },
              "communities": 14, "predicted_links": 60, "conflicts": 21 }
}
```

### `GET /api/algorithms`

Every graph algorithm in use, which engine executed it, and what it is for. Centrality
entries explicitly state that attention ≠ quality.

### `GET /api/opportunity-score`

The published opportunity-score model: five components with labels, weights (30/25/20/15/10),
plain-language explanations, the ranking tie-break rule and the safety notice. This is the
transparency contract behind `/api/gaps` — the UI renders this endpoint directly.

### `GET /api/safety`

Safety, provenance and data-honesty notices (also embedded in gap/report/prediction
payloads). Includes the analyzed-corpus scope language and the "verify at source" rule.

### `GET /api/tools`

The 13 shared research tools with JSON parameter schemas — the same catalogue the internal
agent and the MCP server use.

### `GET /api/mcp`

MCP manifest: server identity, protocol version and the tool list with `input_schema`.

---

## Home and discovery

### `GET /api/dashboard`

Everything the Home view renders: corpus cards, community map, publishing timeline,
centrality leaderboards, conflicts, predicted links and the engine status block.

### `GET /api/timeline?topic=`

Papers per year overall and for a topic — the activity curve used by the Home view and by
the gap engine's research-activity component.

### `GET /api/search?q=&labels=&limit=`

Search across papers, topics, methods, datasets, authors and claims. `labels` is
repeatable (`?labels=Paper&labels=Topic`). Returns `{query, count, results[]}` with
highlighted matches and provenance fields.

### `GET /api/explorer?topic=&year_min=&year_max=&field=`

Field-level analytics for a scope: publishing pulse, topic/method/dataset adoption,
community structure, plus the resolution and methodology notes that produced them.

---

## Papers and nodes

### `GET /api/papers?q=&topic=&field=&year_min=&year_max=&sort=pagerank&order=desc&limit=40&offset=0`

Paged paper list. `sort` accepts `pagerank`, `betweenness`, `degree`, `year`, `name`.
Returns `{total, offset, limit, items[]}` so the UI can page without loading the corpus.

### `GET /api/papers/{paper_id}`

One paper with everything the detail panel shows: properties and provenance
(`summary_source`, `author_status`, `url`), computed metrics **with explanations**,
neighbourhood by relationship type, claims, community membership, conflicts, predicted
links and the shortest evidence paths to related topics. Unknown ids return `404`
(never a silent empty `200`).

### `GET /api/nodes?label=&q=&sort=&order=&year_min=&year_max=&field=&limit=50&offset=0`

Generic paged listing for any label (`Paper`, `Topic`, `Method`, `Dataset`, `Author`,
`Claim`, `Community`) — used by the Explorer tables and the export panel.

### `GET /api/nodes/{node_id}`

Node detail with metric explanations and neighbours. Node ids are **hyphenated slugs**:
`topic:agent-memory`, `method:reinforcement-learning`, `paper:2310.08560`.

---

## Graph

### `POST /api/graph`

```json
{ "query": "agent memory", "depth": 1, "node_types": ["Paper", "Topic"],
  "rel_types": ["STUDIES"], "year_min": 2020, "include_predicted": true,
  "focus": "topic:agent-memory", "max_nodes": 260, "max_edges": 800 }
```

Bounded graph payload: `nodes[]`, `edges[]`, `legend`, `focus`, `truncated`, `notes`.
Node objects are flat (`id`, `label`, `type`, `color`, `pagerank`, `betweenness`,
`degree`, `year`, `url`, …) — ready for the 3D view without client-side mapping.

### `GET /api/graph`

Default view (most influential nodes) with `topic`, `year_min`, `year_max`, `depth`
and `max_nodes` query parameters.

### `POST /api/graph/expand`

```json
{ "node_ids": ["paper:2310.08560"], "rel_types": ["CITES"], "limit": 60 }
```

Expand-on-demand: one hop from the given nodes, edge budget enforced. This is how the UI
lets a judge walk the graph without ever loading it whole.

### `GET /api/graph/neighbours/{node_id}?limit=60`

`{center, neighbours[], count}` — the direct neighbourhood of one node.

### `GET /api/graph/path?source=&target=&max_hops=4`

Shortest relationship path between two entities, returned as node ids plus the edges that
form it. Used by "how are these two connected?" and by the evidence panel. `found: false`
when no path exists within the bound.

### `GET /api/communities` · `GET /api/communities/{index}`

Detected research communities (Louvain) with names, sizes, top topics/methods/papers and
average PageRank; the `{index}` form returns full membership.

---

## Intelligence

### `POST /api/gaps` — the killer feature

```json
{ "topic": "AI Agents", "year_min": null, "year_max": null, "field": null,
  "min_papers": 3, "top_k": 5, "max_clusters": 8 }
```

Returns, per candidate: `rank`, `title`, `opportunity_score`, `score_components[]`
(key/label/value/weight/raw), `confidence` + `confidence_reason`, `granularity`, the two
clusters with their defining topics/methods/papers, `bridge_papers`, `evidence_papers`,
`conflicts`, `trajectory`, the generated `hypothesis` and `experiment` sketch, `labels`,
`why` and the `id`.

Also always present: `scope`, `resolution`, `methodology`, `score_formula`,
`score_label` (*prototype heuristic*), `safety_notice`, `limitations`, `engine`,
`ranking`, `clusters[]`, `score_distribution`, `generated_at`.

Scores are **not** altered by query wording: ordering is
`0.75 × score + 0.25 × relevance`, and the published `opportunity_score` is the raw score.

### `POST /api/gaps/{index}/evidence`

The evidence bundle behind one candidate of the same request body: supporting papers with
quotes, bridge papers, detected conflicts, community profiles, metrics and the paths that
connect the two clusters — i.e. exactly what a reviewer should check.

### `POST /api/agent`

```json
{ "question": "Which claims contradict each other about agent memory?",
  "depth": 2, "top_k": 12, "topic_hint": null }
```

GraphRAG answer with full explainability: `intent`, `answer`, `answer_engine`
(`graph-template` or `llm+graph`), `used_llm`, `confidence`, `confidence_basis`,
`explainability { claim, reasoning[], graph_evidence, supporting_papers, confidence,
confidence_basis, safety_notice }`, `tool_calls[]`, `context`, `evidence`, `paths`,
`predicted_links`, `conflicts`, `follow_ups`.

### `POST /api/agent/stream`

Same request, `text/event-stream` response. Events:
`intent → plan → tool (per call) → retrieval → synthesis → done → answer` (or `error`),
each with `elapsed_ms`. `POST /api/ask` is an alias for scripted demos.

---

## Reports and exports

### `POST /api/report`

`{ "topic": "AI Agents", "top_k": 5, "executive_summary": null }` → the complete
Research Opportunity Report as JSON: scope, executive summary, ranked opportunities with
evidence, contradictions, community map, method-gap analysis, dataset gaps, next steps,
provenance and the safety notice.

### `POST /api/report/markdown`

The same report as a Markdown document (`text/markdown`) — ready to paste into a
notebook, issue or paper draft.

### `POST /api/export`

```json
{ "format": "graphml", "node_types": ["Paper", "Topic"], "rel_types": null, "limit": 800 }
```

`graphml` (Gephi/yEd/networkx), `csv` (nodes + edges sheets in one body), `json`
(round-trippable `nexus-graph-json`) and `cypher` (a parameter-free `MERGE` script plus
the parameterised pattern it mirrors, so the exported slice can be recreated in Neo4j).

### `GET /api/export/cypher?limit=400`

Convenience variant of the Cypher export used by the UI's "open in Neo4j" button.

---

## Errors

| Status | Meaning |
| --- | --- |
| `404` | Unknown node/paper id — the message names the id that was not found |
| `422` | Request failed validation (bounds, types) with the offending field |
| `429` | Rate limit; `Retry-After` gives the window |
| `500` | Unexpected error — logged server-side, returned as `{detail}` without internals |
