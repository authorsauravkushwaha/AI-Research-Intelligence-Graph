# NEXUS — AI Research Intelligence Graph

**A graph-native research intelligence prototype.** NEXUS ingests a corpus of AI research
papers, builds a knowledge graph, runs graph algorithms over it, and uses that graph —
not a chat box — to find **research gaps**, explain **contradictions**, predict
**missing connections**, and generate a **transparent research opportunity report**.

`python run.py` → **http://localhost:8000**

<p align="center">
  <img alt="python" src="https://img.shields.io/badge/python-3.11%2B-3776ab">
  <img alt="native kernel" src="https://img.shields.io/badge/graph%20kernel-C%2B%2B%20·%20zero%20deps-00599c">
  <img alt="neo4j" src="https://img.shields.io/badge/Neo4j-5.x%20%2B%20GDS-008cc1">
  <img alt="llm" src="https://img.shields.io/badge/LLM-optional%20(offline%20fallback)-green">
  <img alt="frontend" src="https://img.shields.io/badge/3D-three.js%20r160%20(vendored)-000000">
  <img alt="tests" src="https://img.shields.io/badge/tests-75%20passing-brightgreen">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-blue">
</p>

---

## Table of contents

- [What it does](#what-it-does)
- [Quickstart](#quickstart)
- [The seven views](#the-seven-views)
- [Killer feature — FIND RESEARCH GAPS](#killer-feature--find-research-gaps)
- [Architecture](#architecture)
- [The polyglot split](#the-polyglot-split)
- [Data model](#data-model)
- [GraphRAG pipeline](#graphrag-pipeline)
- [Documentation map](#documentation-map)
- [Graph algorithms](#graph-algorithms)
- [Safety, provenance and honesty](#safety-provenance-and-honesty)
- [API](#api)
- [MCP server](#mcp-server)
- [Running on Neo4j (+ GDS)](#running-on-neo4j--gds)
- [Configuration](#configuration)
- [Testing](#testing)
- [Demo script (3–5 minutes)](#demo-script-35-minutes)
- [Project layout](#project-layout)
- [Limitations and what we would do next](#limitations-and-what-we-would-do-next)
- [Credits and data provenance](#credits-and-data-provenance)

---

## What it does

Most "AI research assistant" demos are a language model with a vector index: retrieve
similar abstracts, summarise, done. That architecture cannot answer the questions that
actually matter to a researcher, because the answers are *structural*:

| Question | Why retrieval alone fails | What NEXUS does |
| --- | --- | --- |
| Where is the field **under-explored**? | A gap is an absence — nothing similar to retrieve | Builds a concept graph, detects communities, measures separation between them |
| Which claims **contradict** each other? | Needs claim-level comparison, not document similarity | Extracts `Claim` nodes and scores explicit opposition |
| Which two papers **should** be connected? | Needs topology (common neighbours, paths) | Link prediction (Adamic–Adar + Jaccard) over the graph |
| Which paper is **pivotal**? | Citation counts are not centrality *in a concept graph* | PageRank / betweenness / degree over the heterogeneous graph |
| *Why* should I believe any of this? | An LLM answer is not evidence | Every number carries its formula, its inputs and the papers behind it |

NEXUS is therefore a **graph engine with a research UI on top**, not a chatbot with a
database attached. The LLM is an optional final-paragraph writer; switch it off and the
system still answers — from graph structure, with deterministic templates.

---

## Quickstart

```bash
git clone https://github.com/authorsauravkushwaha/AI-Research-Intelligence-Graph
cd AI-Research-Intelligence-Graph

python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt

python run.py                 # → http://localhost:8000
```

That is the whole setup. **No Neo4j, no API key, no Docker, no network.** The launcher
checks the environment first (`python run.py --check`) and reports exactly what is
available:

```
  · neo4j: not configured (in-process engine)
  · llm: not configured (graph-template answers)
  · embeddings: local lexical baseline
  · native kernel: nexus-native/1.0.0 (C++, zero deps)
  · corpus: 204 papers · 713 nodes · 2,963 edges
```

Three optional upgrades, each independent:

```bash
cd native && make -j4            # build the C++ analytics kernel (auto-built on demand otherwise)
cp .env.example .env             # add NEO4J_URI / NEO4J_PASSWORD to use Neo4j
docker compose up                # Neo4j 5 + GDS + APOC + the API container
```

---

## The seven views

| # | View | What it shows |
| --- | --- | --- |
| 1 | **Home** | Corpus health, engine status, community map, activity timeline, top entities |
| 2 | **Research Explorer** | Field-level metrics: publishing pulse, method/dataset adoption, community structure |
| 3 | **Knowledge Graph** | Interactive **3D** graph — node types, communities, metrics, expand-on-demand, evidence highlighting |
| 4 | **Research Gap Finder** | **The killer feature.** Ranked opportunity candidates with a decomposed score |
| 5 | **Paper Explorer** | Every paper with its neighbourhood, claims, metrics and provenance |
| 6 | **AI Research Agent** | GraphRAG question answering with visible tool calls and a stage-by-stage trace |
| 7 | **Research Opportunity Report** | A complete, exportable brief: scope, gaps, evidence, contradictions, next steps |

---

## Killer feature — FIND RESEARCH GAPS

The gap engine does not ask a language model "what is missing?". It *measures* it:

1. Scope the corpus (topic, years, field) → concept graph (papers + topics + methods + datasets).
2. Detect **communities** with Louvain (resolution 1.2 for concepts, 1.35 for the sub-cluster pass).
3. Enumerate **candidate pairs** of communities — disjoint clusters, no shared topic, deduplicated by name.
4. Score every pair on five absolute, interpretable features:

| Weight | Component | Question it answers | How it is computed |
| ---: | --- | --- | --- |
| **30%** | Community separation | Are these two clusters structurally far apart? | Concept-graph distance between cluster centroids |
| **25%** | Semantic distance | Do they talk about different things? | Embedding distance (real endpoint, or the local lexical baseline) |
| **20%** | Relationship sparsity | Are there few existing bridges? | Bridge-edge density relative to cluster size |
| **15%** | Research activity | Is the neighbourhood alive enough to sustain work? | Recent paper volume around both clusters |
| **10%** | Bridge potential | Would a bridge even be productive? | Predicted-link potential across the two sides |

5. Rank with a documented tie-break — `0.75 × score + 0.25 × query relevance` — without
   ever rewriting the published score.

Every candidate returns the formula, the component values, the papers that define each
cluster, the bridge papers that already exist, contradictions in the area, and a
trajectory (`persistent gap` / `emerging bridge` / `closing gap`).

> Worked example — `POST /api/gaps {"topic": "AI Agents", "top_k": 5}`: 101 papers in scope,
> 88 candidate pairs scored, 24 rejected with a printed reason, top candidates
> *Scientific and Medical Agents × Agent Memory* (84.8), *Agent Memory × Software Agents*
> (85.4), *Agent Memory × Coordination* (85.2). Narrow the scope to *Tool Use* and the top
> candidate becomes *Planning × API Calling* (92.4) — the ranking is recomputed from the
> structure of the scope you asked for, not recited.

> **This is a prototype heuristic, not a validated measure of scientific value.**
> The score is a hypothesis generator. It tells you where to *look*, never that
> "nobody has researched this". Any candidate must be independently validated by a
> domain expert before it means anything. That label is attached to the API response,
> the UI and the exported report — see [SAFETY.md](docs/SAFETY.md).

---

## Architecture

```
                                    ┌──────────────────────────────────────────────┐
                                    │              Browser (no CDN)                │
                                    │  7 views · vanilla ES modules · three.js 3D  │
                                    │  frontend/index.html + assets/ + vendor/     │
                                    └───────────────────────┬──────────────────────┘
                                                            │ fetch / EventSource (SSE)
                                    ┌───────────────────────▼──────────────────────┐
                                    │        FastAPI application  (backend/api)      │
                                    │  routes.py 30+ endpoints · schemas.py (pydantic)│
                                    │  middleware: CORS · rate limit · engine header │
                                    └───┬──────────┬───────────┬───────────┬────────┘
                                        │          │           │           │
             ┌──────────────────────────▼──┐  ┌────▼─────────┐ │  ┌────────▼────────────┐
             │  AI Research Agent (§21-24) │  │  Gap Engine  │ │  │ Report / Export     │
             │  intent → plan → tools →    │  │  (§17-20)    │ │  │ report.py + export.py│
             │  retrieve → synthesise      │  │  30/25/20/15/│ │  │ md · json · GraphML │
             │  agents/agent.py            │  │  10 scoring  │ │  │ CSV · Cypher        │
             └───────┬─────────────┬───────┘  └──────┬───────┘ │  └──────────────────────┘
                     │             │                 │         │
             ┌───────▼──────┐ ┌────▼─────────────────▼─────────▼───────────────────────┐
             │  GraphRAG    │ │              13 shared research tools                   │
             │  (§25-26)    │ │  agents/tools.py  →  search · related · bridges ·       │
             │  graphrag.py │ │  gaps · evidence · paths · metrics · conflicts · ...    │
             └───────┬──────┘ └────────────────────┬───────────────────────────────────┘
                     │                          │
             ┌───────▼──────────────────────────▼───────────────────────────────────────┐
             │                    Graph query interface  (one API, two engines)         │
             │  ┌──────────────────────────────────┐  ┌───────────────────────────────┐ │
             │  │  Neo4jGraphStore  (store of record)│  │ MemoryGraphStore (embedded) │ │
             │  │  bolt · parameterised Cypher      │  │  same schema, same methods   │ │
             │  │  cypher/queries/*.cypher          │  │  in-process, zero infra      │ │
             │  │  GDS: PageRank · Louvain ·        │  │  algorithms/kernel.py        │ │
             │  │        betweenness (fallbacks)    │  │  (native or pure-Python)     │ │
             │  └───────────────▲──────────────────┘  └───────────────▲───────────────┘ │
             │                  │        backend/graph/factory.py     │                 │
             └──────────────────┼──────────────────────────────────────┼─────────────────┘
                                │                                      │
             ┌──────────────────┴───────────────┐   ┌──────────────────┴────────────────┐
             │   Neo4j 5.26 + GDS + APOC        │   │  Native C++ kernel (§30)          │
             │   docker-compose.yml (opt-in)    │   │  native/src → nexus-kernel        │
             │   Cypher schema: cypher/queries/ │   │  PageRank · Louvain · betweenness  │
             └──────────────────────────────────┘   │  link prediction · kNN · paths     │
                                                    └──────────────────┬────────────────┘
             ┌───────────────────────────────────────────────────────┴────────────────┐
             │   Corpus pipeline (§3-§8)   scripts/build_corpus.py → data/demo/       │
             │   validate arXiv ids · dedupe · label provenance · 198 papers / 78 claims│
             └────────────────────────────────────────────────────────────────────────┘
```

**Read it in one line:** corpus → graph → algorithms → tools → agent/report/UI, with the
Neo4j engine and the embedded engine interchangeable behind a single query interface.

---

## The polyglot split

The project uses each language where it genuinely fits — no token gesture ports:

| Language | Where | Why this language |
| --- | --- | --- |
| **Python 3.11+** | API, GraphRAG, agent, gap engine, ingestion | Fast iteration on the parts that are the product |
| **C++17** | `native/` graph kernel (`nexus-kernel`) | Real algorithms with zero dependencies, process-isolated, ~100 ms PageRank on 20k nodes |
| **Cypher** | `cypher/queries/*.cypher`, Neo4j schema | Parameterised, reviewable queries instead of string-built ones |
| **Kotlin (JVM)** | `polyglot/kotlin/` query planner: NEXUS DSL → parameterised Cypher plan | JVM type safety for a compiler-like component (sealed AST, exhaustive `when`), plus an HTTP fallback traversal engine — runs as the `:8092` sidecar and answers `POST /api/plan` |
| **Ruby** | `polyglot/ruby/claim_resolver.rb` | Text/DSL-friendly scripting for claim normalisation and opposition scoring |
| **Go** | `polyglot/go/` ingestion + embeddings sidecar (`:8090`) | Static binary, cheap concurrency for fetch/parse/embed work |
| **C# / .NET** | `polyglot/dotnet/` export sidecar (`:8091`) | First-class XML/GraphML tooling for report export |
| **JavaScript (ES modules)** | `frontend/` + three.js r160 (vendored) | A 3D graph with no build step, no framework, no CDN |
| **Java** | `polyglot/` Neo4j service variant | Same Cypher model on the JVM where Neo4j itself lives |

Every sidecar is **optional**: NEXUS calls it when it is reachable and falls back to the
Python implementation when it is not (`NEXUS_INGEST_URL`, `NEXUS_EXPORT_URL`,
`NEXUS_PLANNER_URL`, `NEXUS_CLAIM_URL` — see `.env.example`). Nothing in the demo
depends on a language runtime that is not Python.

Which of the sidecars has actually been *executed* is tracked honestly in
[`polyglot/README.md`](polyglot/README.md). The C++ kernel runs under `pytest`, the Kotlin
planner was compiled with kotlinc 2.4.21 and exercised on OpenJDK 25 (`--test`, `/api/plan`,
`GET /api/services`), and the Ruby claim resolver runs on CRuby 3.2 via `ruby.wasm` and is
compared claim-by-claim against the Python engine. Kotlin, Go, .NET and Ruby are also built
and self-tested by the `polyglot` CI job; any step a runner cannot provide is an explicit
warning rather than a silent pass. Two cross-language parity checks make this measurable:
`scripts/compare_claim_engines.py` (Ruby ↔ Python claims) and `scripts/compare_planners.py`
(Kotlin ↔ Python query plans, pinned by `tests/data/planner_parity.json`).

---

## Data model

A heterogeneous property graph. Anything the system asserts is a node with provenance;
anything it *infers* is marked as such.

**Nodes**

| Label | Key properties | Example |
| --- | --- | --- |
| `Paper` | `arxiv_id`, `title`, `year`, `field`, `url`, `summary`, `summary_source`, `author_status` | `paper:2310.08560` |
| `Topic` | `name`, `paper_count` | `topic:agent-memory` |
| `Method` | `name`, `paper_count` | `method:reinforcement-learning` |
| `Dataset` | `name`, `paper_count` | `dataset:alfworld` |
| `Author` | `name`, `author_status` | `author:charles-packer` |
| `Claim` | `text`, `polarity`, `direction`, `subject`, `assertive`, `paper_id` | `claim:2310.08560:0` |
| `Community` | `name`, `size`, `top_topics`, `avg_pagerank` | `community:c3` |

Every node also carries computed metrics where applicable: `pagerank`, `betweenness`,
`degree`, `community_index`.

**Relationships** (14 types, `backend/models/graph.py`)

| Relationship | Meaning | Source |
| --- | --- | --- |
| `AUTHORED` | Author → Paper | arXiv metadata |
| `CITES` | Paper → Paper | Curated corpus citations (30 curated lineage edges) |
| `STUDIES` | Paper → Topic | Corpus labels |
| `USES_METHOD` | Paper → Method | Corpus labels |
| `USES_DATASET` | Paper → Dataset | Corpus labels |
| `AFFILIATED_WITH` | Author → Institution | Where collected |
| `MAKES_CLAIM` | Paper → Claim | Claim extraction |
| `SUPPORTS` / `CONTRADICTS` | Claim ↔ Claim | Opposition scoring (never asserted as fact) |
| `BELONGS_TO` | Paper/Claim → Community | Louvain |
| `RELATED_TO` | Any ↔ Any | Curated thematic links |
| `MEASURED_BY` | Topic/Method → Metric | Analytics |
| `SIMILAR_TO` | Paper ↔ Paper | Vector similarity, thresholded |
| `PREDICTED_LINK` | Paper ↔ Paper | Link prediction — **hypothesis only** |

The same schema is expressed twice: as Cypher constraints/indexes for Neo4j
(`cypher/queries/06_maintenance.cypher`) and as typed Python dataclasses
(`GNode` / `GEdge` / `Subgraph`) for the embedded engine, so both stores answer
identical queries.

---

## GraphRAG pipeline

Retrieval is deliberately **not** vector-only. A vector index finds *similar text*; the
graph finds *structure* — and structure is where the answers live.

```
  question
     │
     ▼
 ┌──────────────────┐   intent + entities   ┌────────────────────────────────────────┐
 │ 1. Understand    │──────────────────────▶│ 13 research tools (agents/tools.py)    │
 │ agents/agent.py  │                       │ search · related · bridges · gaps ·    │
 │ rule or LLM plan │                       │ evidence · paths · metrics · conflicts │
 └──────────────────┘                       └───────────────────┬────────────────────┘
     │                                                         │
     │  ┌──────────────────────────────────────────────────────▼─────────────────────┐
     │  │ 2. Retrieve — three channels, merged and re-ranked                          │
     │  │                                                                             │
     │  │   a) VECTOR     VectorIndex.search()  → lexically/embedding-similar papers   │
     │  │   b) GRAPH      bounded expansion over STUDIES/USES_METHOD/USES_DATASET      │
     │  │                 plus community membership, bridge nodes, shortest paths      │
     │  │   c) STRUCTURE  centrality metrics, detected conflicts, predicted links       │
     │  │                                                                             │
     │  │   → GraphContext: seeds, papers, topics, methods, datasets, authors,        │
     │  │     communities, claims, conflicts, predicted_links, paths, metrics         │
     │  └──────────────────────────────────────┬──────────────────────────────────────┘
     └─────────────────────────────────────────┼──────────────────────────────────────
                                               ▼
                          ┌────────────────────────────────────────────┐
                          │ 3. Synthesise                              │
                          │ GraphContext.to_prompt() (≤ 9000 chars)    │
                          │  ├─ LLM configured → grounded answer       │
                          │  └─ no LLM → deterministic graph template  │
                          └───────────────────┬────────────────────────┘
                                              ▼
                          ┌────────────────────────────────────────────┐
                          │ 4. Explain — for every answer:             │
                          │ claim · reasoning steps · graph evidence   │
                          │ supporting papers · predicted links        │
                          │ confidence + confidence_basis              │
                          │ safety notice · follow-up questions        │
                          └────────────────────────────────────────────┘
```

The prompt is assembled from graph facts with explicit ids, so the LLM cannot invent
papers: every sentence it writes is anchored to nodes that exist in the context, and
the deterministic template path produces the same structure with no model at all.

Agent responses are also streamed as pipeline stages over SSE
(`POST /api/agent/stream`): `intent → plan → tool → retrieval → synthesis → done → answer`.
The UI shows each stage live, which makes the reasoning auditable rather than magical.

---

## Graph algorithms

| Algorithm | Used for | Where | Notes |
| --- | --- | --- | --- |
| PageRank | Pivotal papers/topics | C++ kernel, GDS, Python fallback | Weighted, damping 0.85 |
| Louvain communities | Research clusters, gap candidates | C++ kernel, GDS, Python fallback | Resolution 1.2 / 1.35, seeded |
| Betweenness centrality | Bridge entities between fields | C++ kernel (sampled), GDS | Approximation above 400 nodes, stated in the API response |
| Adamic–Adar + Jaccard | Predicted links between papers | C++ kernel | Always labelled `PREDICTED_LINK`, never a fact |
| kNN over embeddings | Similar-paper retrieval | C++ kernel, local baseline | Cosine, thresholded |
| BFS shortest path | Evidence paths between two entities | C++ kernel / Python | Bounded hops, returned as node ids |
| Connected components, degree | Coverage and isolation checks | C++ kernel | Cheap sanity metrics |

The native kernel is a single static binary with a JSON job protocol
(`echo '{"job":"pagerank","nodes":[...],"edges":[...]}' | native/build/bin/nexus-kernel`),
so the heavy analytics never block the API process and can be swapped for GDS when
Neo4j is present. Measured: PageRank over 20k nodes / 120k edges in **104 ms**, Louvain
in **896 ms** — see `native/bench`.

---

## Safety, provenance and honesty

These rules are enforced in code, tests and copy — not just in the README:

- **Never** "nobody has researched this". Gaps are described as *"relatively
  under-explored within the selected corpus"*, with the analyzed-corpus scope attached.
- Predicted links are **hypotheses**: `PREDICTED_LINK` edges, `is_hypothesis` flags and a
  disclaimer on `/api/predictions` in the UI.
- The Opportunity Score is labelled a **prototype heuristic** everywhere it appears.
- Centrality is **attention, not quality** — every leaderboard says so.
- Claim conflicts are **tensions worth checking**, at claim level, with the pair and the
  score shown; they are never presented as settled disagreements.
- Corpus provenance is per record: real arXiv ids/titles; summaries are editorial
  (`summary_source: editorial | title-derived`); authorship is only claimed when
  verified (`author_status: verified | not-collected`). Full audit in
  [data/demo/PROVENANCE.md](data/demo/PROVENANCE.md).
- **No secrets in the front end**, no credentials in source. Everything is an env var
  (`.env.example`), `.env` is git-ignored, and the API reports capability flags instead
  of keys.
- All Neo4j access is parameterised Cypher (`cypher/queries/*.cypher`, bound params in
  `backend/graph/neo4j_store.py`) — no string-concatenated queries.
- Graph requests are bounded (`NEXUS_MAX_GRAPH_NODES`, `NEXUS_MAX_GRAPH_EDGES`, depth
  ≤ 3, paged listing APIs) so an impatient judge cannot DoS the demo.
- Rate limiting, CORS allow-listing and JSON error handling are enabled by default.

---

## API

30+ endpoints, all JSON, all documented in [docs/API.md](docs/API.md). Highlights:

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Engine in use, capability flags, graph counts, degraded reason |
| `GET` | `/api/dashboard` | Everything the Home view renders |
| `POST` | `/api/graph` | Bounded, paged graph payload (filter by type/year/focus) |
| `POST` | `/api/graph/expand` | Expand-on-demand (one hop from selected nodes) |
| `GET` | `/api/graph/path` | Shortest evidence path between two entities |
| `POST` | `/api/gaps` | **The gap engine** — ranked candidates + full score decomposition |
| `POST` | `/api/gaps/{i}/evidence` | The evidence bundle behind one candidate |
| `POST` | `/api/agent` | GraphRAG answer with explainability + tool trace |
| `POST` | `/api/agent/stream` | The same, streamed as stages (SSE) |
| `POST` | `/api/report` | Research Opportunity Report (JSON) |
| `POST` | `/api/report/markdown` | The same report as Markdown |
| `POST` | `/api/export` | GraphML · CSV · JSON · Cypher |
| `POST` | `/api/plan` | NEXUS DSL → parameterised Cypher + explanation + cost (Kotlin planner, Python fallback) |
| `GET` | `/api/services` | Which polyglot sidecars are live, and what answers instead |

```bash
curl -s localhost:8000/api/health | jq .store
curl -s -X POST localhost:8000/api/gaps \
     -H 'content-type: application/json' \
     -d '{"topic":"AI Agents","top_k":3}' | jq '.opportunities[] | {title, score}'
```

---

## MCP server

NEXUS speaks the [Model Context Protocol](https://modelcontextprotocol.io) so external
agents can use it as a research tool server:

```bash
python mcp/server.py --list          # print the 13-tool catalogue
python mcp/server.py                 # stdio JSON-RPC (2024-11-05)
```

It exposes the *same* tools the API and the internal agent use — one implementation,
three consumers (`/api/tools`, `agents/tools.py`, `mcp/server.py`).

---

## Running on Neo4j (+ GDS)

```bash
cp .env.example .env      # set NEO4J_PASSWORD=... and NEO4J_URI=bolt://localhost:7687
docker compose up -d      # neo4j 5.26-community + GDS + APOC, then the API container
python run.py             # or just use the api container on :8000
```

`backend/graph/factory.py` tries Neo4j first, projects the graph with GDS when
available, and falls back to the embedded engine if the database is unreachable —
recording *why* in `/api/health` (`store.degraded`, `store.reason`) and in the UI status
bar. A judge always knows which engine answered. The loader
(`backend/graph/neo4j_store.py`) creates constraints and a full-text index, hydrates
nodes/edges from the corpus, streams PageRank/Louvain/betweenness, writes metrics back,
and materialises `PREDICTED_LINK` edges.

---

## Configuration

Everything is an environment variable; nothing is required for the demo. Full list with
comments in [`.env.example`](.env.example).

| Variable | Default | Purpose |
| --- | --- | --- |
| `NEO4J_URI` / `NEO4J_USERNAME` / `NEO4J_PASSWORD` | empty | Neo4j store of record; empty → embedded engine |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | empty | Any OpenAI-compatible endpoint; empty → graph-template answers |
| `NEXUS_EMBEDDINGS_URL` | empty | Real embeddings; empty → deterministic local baseline |
| `NEXUS_MAX_GRAPH_NODES` / `NEXUS_MAX_GRAPH_EDGES` | 400 / 1200 | Hard bounds on graph payloads |
| `NEXUS_RATE_LIMIT_PER_MINUTE` | 240 | Per-IP request budget |
| `NEXUS_CORS_ORIGINS` | empty | Extra allowed origins (comma-separated) |
| `NEXUS_DEMO_MODE` | true | Deterministic demo behaviour |

---

## Testing

```bash
python -m pytest -q          # 75 tests: corpus, store, kernel, gaps, agent, planner, API, MCP, frontend
./scripts/smoke.sh           # 46 live HTTP checks against a running server (incl. SSE)
python run.py --check        # installation preflight
python scripts/build_corpus.py --check   # corpus validator (ids, dedupe, provenance)
make -C native test          # native kernel self-tests
```

CI runs all of the above plus a `docker build` and a vendored-frontend/no-CDN audit on
every push — see [.github/workflows/ci.yml](.github/workflows/ci.yml).

---

## Demo script (3–5 minutes)

The full narration is in [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md). Short version:

1. **Home (20 s)** — 204 papers, 713 nodes, 2,963 edges, 15 communities, live engine status; nothing to log into.
2. **Knowledge Graph (45 s)** — rotate the 3D graph, colour by community, click ReAct,
   expand on demand, then highlight the evidence path to Agent Memory.
3. **FIND RESEARCH GAPS (75 s)** — topic *AI Agents*: 88 pairs scored, five-component
   decomposition, trajectory and evidence for the top candidate. Then retype the topic as
   *Agent Memory*: every pair in that 17-paper scope is entangled, so NEXUS returns **no
   candidate and lists the excluded pairs with reasons** — the bar is visible and it does
   not move.
4. **AI Research Agent (60 s)** — ask *"Which claims contradict each other about agent
   memory?"*, then watch the stage trace and open the supporting papers.
5. **Report (30 s)** — export the Research Opportunity Report as Markdown and the graph as
   GraphML/Cypher; show the safety notice on the same page.
6. **Close (20 s)** — `docker compose up` → the identical API on Neo4j + GDS, with the same
   answers and the engine badge changing from *embedded* to *neo4j*.

---

## Project layout

```
AI-Research-Intelligence-Graph/
├── backend/
│   ├── api/            FastAPI app, 30+ routes, pydantic schemas
│   ├── agents/         agent loop, 13 research tools, optional LLM client
│   ├── algorithms/     native-kernel bridge + pure-Python fallbacks
│   ├── engine/         gap engine, explorer analytics
│   ├── graph/          Neo4j store, store factory (degraded fallback)
│   ├── ingestion/      corpus → graph, claim extraction/scoring
│   ├── models/         GNode / GEdge / Subgraph, relationship vocabulary
│   ├── rag/            embeddings, vector index, GraphRAG retrieval
│   ├── services/       report builder, exporters
│   └── store/          embedded graph store
├── cypher/queries/     parameterised Cypher: overview, papers, communities, gaps, GDS, maintenance
├── data/demo/          corpus.json + PROVENANCE.md + harvested sources
├── frontend/           index.html, assets/app.js (ES modules), vendored three.js r160
├── mcp/                stdio MCP server exposing the same 13 tools
├── native/             C++17 graph kernel (PageRank, Louvain, betweenness, link prediction)
├── polyglot/           Kotlin planner · Ruby claim resolver · Go ingest · C# export · Java service
├── scripts/            corpus builder/validator, live smoke sweep
├── tests/              pytest suite (75 tests)
├── docker-compose.yml  Neo4j 5.26 + GDS + APOC + API
└── run.py              launcher with preflight checks
```

---

## Limitations and what we would do next

Honest list, because a research prototype that hides its limits is not a research prototype:

- **The corpus is a curated demo set (204 papers), not a literature review.** It is
  deliberately small enough to audit and to run offline. The ingestion path is built for
  arXiv/Semantic Scholar/Crossref (`backend/ingestion/`, Go sidecar) and the graph schema
  does not change with scale.
- **Gap scores are compressed** (≈73–89 in the demo scope) and should be read as ranks
  and components, not as calibrated probabilities.
- **Embeddings default to a local lexical baseline** so the demo works offline; set
  `NEXUS_EMBEDDINGS_URL` for real vectors.
- **Claim extraction is rule-based**, tuned for precision (it misses contradictions
  rather than inventing them).
- **Neo4j/GDS code paths are written and import-tested but were not runnable in the
  development sandbox** (no Docker) — the embedded engine is the verified path, and
  `docker compose up` is the one-command way to exercise the other.
- Next: streaming corpus ingestion from arXiv/S2/Crossref with incremental graph
  upserts, GDS-native similarity pipelines, an evaluation harness against expert-labelled
  gap lists, and multilingual claim normalisation.

---

## Documentation map

| Document | What it covers |
| --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layering, the two engines, why algorithms run out-of-process, security posture |
| [docs/METHODOLOGY.md](docs/METHODOLOGY.md) | Corpus → graph, analytics, claim scoring, the full opportunity-score model, limitations |
| [docs/GRAPHRAG.md](docs/GRAPHRAG.md) | The three-channel retrieval pipeline, synthesis paths, explainability contract, MCP |
| [docs/API.md](docs/API.md) | Every endpoint with request/response shapes and error behaviour |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | Node labels, the 14 relationship types, wire format, corpus record format |
| [docs/UI.md](docs/UI.md) | The seven views, graph controls, accessibility and degradation behaviour |
| [docs/SAFETY.md](docs/SAFETY.md) | The honesty rules and where each one is enforced in code |
| [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) | The 3–5 minute live walkthrough, with answers to hard questions |
| [data/demo/PROVENANCE.md](data/demo/PROVENANCE.md) | Per-field audit of what is real and what is editorial |
| [mcp/README.md](mcp/README.md) | Using NEXUS as an MCP research-tool server |

## Credits and data provenance

- Paper metadata (ids, titles) originates from public arXiv listings and the
  community-maintained index archived in `data/demo/sources/awesome-agent-papers.md`.
  Every record links to its `https://arxiv.org/abs/...` page.
- Summaries are **editorial scope notes written for this demo**, labelled per record
  (`summary_source`); authors are only shown when verified (37 of 204 records). See
  [data/demo/PROVENANCE.md](data/demo/PROVENANCE.md) and `/api/safety`.
- Graph algorithms are implemented in `native/` (C++17) with pure-Python fallbacks; no
  GPL or closed-source dependencies.
- three.js r160 is vendored under `frontend/vendor/` (MIT, provenance in
  `frontend/vendor/README.md`) so the UI runs with no network access.
- Built as a hackathon prototype. If you use an idea from it, cite the papers, not us.

**MIT licensed** — see [LICENSE](LICENSE).
