# UI guide — the eight views

A single page, eight views, no framework, no build step, no CDN: `frontend/index.html`
plus `frontend/assets/app.js` (ES module) and `frontend/assets/style.css`, with three.js
r160 vendored in `frontend/vendor/`. The whole front end is served by the same process as
the API, so the demo needs one command and zero network access.

---

## 1 · Home

* **Engine banner** — store engine (`in-process` / `neo4j`), embedding engine, LLM status,
  corpus path and version. If the server fell back to the embedded store, the reason is
  printed here (it comes from `/api/health`).
* **Corpus cards** — papers, authors, topics, methods, datasets; verified-authorship count.
* **Community map** — Louvain communities with size and top topics.
* **Activity timeline** — papers per year, the same series the gap engine's
  *research activity* component uses.
* **Leaderboards** — top papers by PageRank and by betweenness, each with the standing
  caveat that attention is not quality.
* **Live signals** — detected claim tensions and predicted links, both labelled as
  algorithmic hypotheses.
* **Polyglot engines** — one row per sidecar (Kotlin planner, Go ingest, C# export, Ruby
  claim resolver): live, offline, or not configured, the engine that answers, and the
  Python fallback that takes over. Nothing here is required for the demo.

## 2 · Investigator

The question form accepts a required research question, an optional topic (otherwise
inferred only when a corpus-topic match is clear), and optional year/field filters. Example
buttons link to the three static-site snapshots. Submission shows loading and readable error
states; static mode refuses unsupported question/scope pairs rather than returning a nearby
answer.

The result displays the selected-scope resolver and coverage, actual GraphRAG retrieval
engine, graph-store backend, independently reranked source papers with arXiv links, and
separately ranked opportunity candidates. Candidate cards expose unchanged prototype-score
components, the separate question-relevance signal, source-paper roles, observed path hops
with direction/provenance, detected claim-tension leads, and explicit caveats. “Trace these
nodes in the graph” seeds the interactive graph from the selected path. Each experiment
proposal includes an intervention, baseline selection rule, predeclared primary outcome,
controls, and a falsification criterion; unknown study details are left for source review.

When Neo4j is active, the screen states that scope/ranking/analytics use a Neo4j-hydrated
local projection and reported paths are checked with bounded Neo4j Cypher. In-process mode
uses bounded BFS. Neither missing edges nor absent automated tensions prove novelty or
consensus.

## 3 · Research Explorer

Field-level analytics for a chosen scope (topic / year range / field): publishing pulse,
topic, method and dataset adoption, community structure, and top bridge entities.
Every panel states the resolution and method that produced it, so a number can be traced
back to a query.

## 4 · Knowledge Graph

The 3D view (three.js + OrbitControls). Deliberately bounded:

| Control | What it does |
| --- | --- |
| Rotate / zoom / pan | Orbit and dolly the scene |
| Colour by | node type, community, or a metric (PageRank, betweenness, degree) |
| Filter | node types, relationship types, year range |
| Max nodes / depth | enforced server-side too (`max_nodes ≤ 400`, `depth ≤ 3`) |
| Click a node | detail panel: props, provenance, metric explanations, neighbours |
| **Expand** | one hop from the selected node (`/api/graph/expand`) — the graph grows on demand, never loaded whole |
| **Highlight path** | shortest path over observed corpus relationships, rendered as an evidence route; Neo4j queries are bounded and direct when that backend is active |

Predicted links render as dashed edges with a hypothesis tooltip, so inference is visually
distinct from fact.

* **Query planner** — a small card in the same overlay: type the NEXUS DSL (or press
  Enter) and it shows the *parameterised* Cypher the system would run, the bind
  parameters, the planner's explanation and its cost estimate — answered by the Kotlin
  sidecar when it is up and by the Python port when it is not, with the engine named.
  Language clauses outside the whitelist are refused with the allowed values listed.

## 5 · Research Gap Finder — the killer feature

* **Scope form** — topic, year range, `top_k`, minimum papers per cluster.
* **Score model panel** — fetched live from `/api/opportunity-score`: the five components,
  their weights, and one sentence each on what they measure.
* **Ranked candidates** — each card shows rank, title, opportunity score, the decomposed
  bars (raw value next to the normalised one), confidence with its basis, trajectory
  status, granularity (cluster / sub-cluster / topic), bridge papers with years, and the
  generated hypothesis, experiment sketch and "why" text.
* **Evidence drawer** — `/api/gaps/{i}/evidence`: supporting papers with reasons, bridge
  papers, claim tensions, community profiles and the paths connecting the two clusters.
* **Empty state with reasons** — when a scope has no separated pair, the view lists the
  pairs the engine excluded and why (nested concepts, entangled clusters, overlapping
  membership). The bar never silently moves.

## 6 · Paper Explorer

Paged, filterable list of all papers; opening one shows provenance (`summary_source`,
`author_status`, arXiv link), metrics with explanations, its neighbourhood by relationship
type, claims, community membership, detected tensions and predicted links.

## 7 · AI Research Agent

* **Question box** with example prompts covering the 13 intents.
* **Stage trace** — streamed over SSE: `intent → plan → tool (each call) → retrieval →
  synthesis → done → answer`, with `elapsed_ms` per stage.
* **Answer** with `answer_engine` and `used_llm` badges, confidence and its basis.
* **Explainability panel** — the claim, the reasoning steps, the graph evidence, the
  supporting papers (each with its arXiv link), predicted links, conflicts and the safety
  notice.
* **Follow-up suggestions** generated from the evidence that was retrieved.

## 8 · Research Opportunity Report

Builds the full brief for a scope and renders it section by section; downloads as Markdown
(`/api/report/markdown`) and exports the underlying graph as GraphML, CSV, JSON or Cypher
(`/api/export`). The report carries the safety notice, the limitations list and the
provenance block, so a shared report cannot be read out of context.

---

## The published snapshot of these views

The same eight views are published at
<https://authorsauravkushwaha.github.io/AI-Research-Intelligence-Graph/> and install as an app
(`manifest.webmanifest` + service worker, so it opens offline). The provenance differs by
view:

* **computed in the browser** on every interaction: graph subgraph, expand, neighbours,
  graph paths, paper/node detail, search, paper list, timeline, explorer and query planning,
  all over the shipped snapshot. The planner generates a plan but does not execute Cypher.
  `scripts/check_site_data.mjs` compares the shipped browser mirror against recorded API
  responses; current check counts are generated at build time, not promised in this guide;
* **replayed from build-time engine output**: gap scopes, reports, agent answers, and the
  three exact Investigator question/scope pairs. The Investigator response explicitly notes
  it is a precomputed snapshot; no browser retrieval or graph traversal occurs for that
  request;
* **explicit unsupported state**: other Investigator question/scope pairs or filtered
  requests return HTTP 501 with the supported pairs and a local-run hint. Unrecorded gap or
  report scopes are also rejected rather than answered with an unrelated result.

The graph experience remains interactive, but the static site cannot query Neo4j or compute a
new investigation. Use the local FastAPI app for live question answering and paths.

---

## Accessibility and robustness

* Keyboard-reachable controls, visible focus rings, ARIA labels on the 3D canvas and the
  status regions.
* Colour is never the only signal: node type, community and metric are also shown as text;
  predicted links are dashed *and* labelled.
* Degradation is visible: if the native kernel, embeddings endpoint or Neo4j are missing,
  the corresponding badge changes and the reason is shown — no silent fallbacks.
* No external requests of any kind: no CDN, no fonts, no analytics. The UI works offline
  and behind a restrictive firewall, which is what makes it presentable on conference
  Wi-Fi.
