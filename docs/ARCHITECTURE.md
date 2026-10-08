# Architecture

NEXUS is built around one idea: **the graph is the intelligence, everything else is a
view onto it**. This document explains how the pieces fit, why each choice was made, and
what happens when a dependency is missing.

---

## 1. Layers

```
HTTP / UI  →  intelligence  →  tools  →  query interface  →  store + algorithms  →  corpus
```

| Layer | Code | Responsibility |
| --- | --- | --- |
| Presentation | `frontend/` | 7 views, vanilla ES modules + vendored three.js; no build step, no CDN |
| API | `backend/api/` | 30+ endpoints, validation, rate limiting, CORS, static hosting |
| Intelligence | `backend/engine/`, `backend/agents/`, `backend/rag/`, `backend/services/` | Gap scoring, agent loop, GraphRAG retrieval, reports/exports |
| Tools | `backend/agents/tools.py` | 13 research tools — one implementation, three consumers (API, agent, MCP) |
| Query interface | `backend/graph/factory.py`, `backend/store/memory_store.py`, `backend/graph/neo4j_store.py` | One interface, two interchangeable engines |
| Algorithms | `native/` + `backend/algorithms/kernel.py` | C++ kernel with process isolation and Python fallbacks |
| Corpus | `scripts/build_corpus.py`, `data/demo/` | Validated, provenance-labelled dataset |

The dependency arrows only ever point **down**. The front end knows nothing about Neo4j;
the gap engine knows nothing about HTTP; the tools know the store interface and nothing else.

---

## 2. One interface, two engines

`backend/graph/factory.py` decides which store answers:

```
NEO4J_URI + NEO4J_PASSWORD set and reachable?
  ├── yes → Neo4jGraphStore   (store of record; bolts, parameterised Cypher, GDS)
  └── no  → MemoryGraphStore  (embedded; same schema, same methods, zero infra)
```

Both implement the same surface — `list_nodes`, `node`, `node_detail`, `search`,
`neighbours`, `subgraph`, `expand`, `path`, `stats`, `top`, `communities`,
`conflicts`, `predicted_links`, `documents`, `engine_status`. The rest of the system
cannot tell them apart, which is why the demo survives a missing database and why
judges can run the Neo4j path without touching a line of application code.

**Degradation is explicit, never silent.** The factory records *why* it fell back and
`/api/health` surfaces `store.backend`, `store.degraded`, `store.reason` and
`store.warnings`; the UI shows the same badge. A fallback that hides itself would be a
lie about what the demo is proving.

### Neo4j path (`backend/graph/neo4j_store.py`)

Loader → constraints + full-text index → node/edge hydration → GDS projection
(`gds.graph.project` / `gds.pageRank.stream` / `gds.louvain.stream` /
`gds.betweenness.stream`) → metric write-back → `PREDICTED_LINK` materialisation. If GDS
is absent, the same kernels run through the C++ binary or Python; the engine per metric
is reported in `/api/algorithms`. All Cypher lives in `cypher/queries/*.cypher` and every
value is a bound parameter — there is no string-concatenated query in the code base.

---

## 3. Algorithms: out-of-process, therefore never-fatal

`backend/algorithms/kernel.py` shells out to `native/build/bin/nexus-kernel` with a JSON
job on stdin. Benefits:

- **Crash isolation** — a bad graph cannot take the API process down.
- **Honest performance** — PageRank over 20k nodes/120k edges in ~104 ms.
- **Interchangeable implementations** — GDS when present, C++ otherwise, pure Python last.

Each wrapper returns `(result, engine)` so the caller records *which* implementation
produced a number, and the API publishes that. Where approximations are involved (sampled
betweenness above 400 nodes) the response says so rather than pretending to be exact.

---

## 4. The gap engine

`backend/engine/gaps.py` composes the graph into a concept graph, runs Louvain at two
resolutions, enumerates disjoint cluster pairs, and scores them on five absolute
features (30/25/20/15/10 — see [METHODOLOGY.md](METHODOLOGY.md)). Design constraints:

- **Deterministic** — seeded Louvain, sorted iteration, so the same scope yields the same
  ranking on every machine and every run.
- **Query wording must not move the score** — ordering blends score with relevance
  (`0.75/0.25`), but the published number is untouched.
- **No degenerate candidates** — a pair must be disjoint, must not share a topic, and
  pairs are deduplicated by cluster name.
- **Everything is inspectable** — each candidate carries its components, the papers that
  define each cluster, existing bridges, conflicts and a trajectory.

---

## 5. Agent and GraphRAG

`backend/agents/agent.py` runs a small, auditable loop: intent → plan → tools →
retrieval → synthesis → explanation. With an LLM key it asks the model to plan a tool
sequence; without one it uses 13 deterministic intent rules. Either way the *answer* is
assembled from `GraphContext`, and the deterministic template path is a first-class
output, not an error state.

`backend/rag/graphrag.py` is deliberately **not** vector-only: retrieval merges three
channels (vector similarity, bounded graph expansion, structural signals such as
centrality/conflicts/predicted links) into one `GraphContext`. The context is rendered to
a ≤ 9000-character prompt in which every paper carries an id, so generated prose is
anchored to nodes that exist. See [GRAPHRAG.md](GRAPHRAG.md).

---

## 6. Front end

A single HTML file, one stylesheet, one ES module and a vendored three.js r160. No
framework, no bundler, no CDN — the UI works offline, which matters for a demo on
conference Wi-Fi. The 3D view loads a bounded graph, expands on demand, highlights
evidence paths, and colour-codes by node type/community/metric.

---

## 7. Security and safety

- Secrets only via environment variables; `.env` git-ignored; the API exposes **capability
  booleans**, never keys.
- Parameterised Cypher throughout; no `f"MATCH … {user_input}"` anywhere.
- Input validation at the boundary (pydantic) plus hard server-side bounds on payload size
  and traversal depth.
- Rate limiting and CORS allow-listing on by default.
- Safety language is part of the response contract, not a UI string: gaps/reports carry
  `safety_notice`, predicted links carry a hypothesis disclaimer, and centrality payloads
  state that attention is not quality (see [SAFETY.md](SAFETY.md)).

---

## 8. Why this survives a demo

| Failure | Behaviour |
| --- | --- |
| No Neo4j | Embedded engine, identical API, `degraded` reason published |
| No LLM key | Deterministic graph-template answers with the same explainability structure |
| No embeddings endpoint | Deterministic local lexical baseline (labelled as such) |
| No native kernel | Python fallbacks behind the same function signatures |
| No network at all | Vendored three.js + local corpus — the full UI and API still run |
| Bad graph request | Bounded, validated and rejected with a readable error |
