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
  <img alt="tests" src="https://img.shields.io/badge/tests-94%20passing-brightgreen">
  <img alt="website" src="https://img.shields.io/badge/site-live%20on%20GitHub%20Pages-2ea44f">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-blue">
</p>

---

## Table of contents

- [What it does](#what-it-does)
- [Quickstart](#quickstart)
- [Website & app (published)](#website--app-published)
- [The eight views](#the-eight-views)
- [Question-to-evidence Investigator](#question-to-evidence-investigator)
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

Run commands **from the repository root** — the folder containing both `run.py` and
`backend/`. If `backend/requirements.txt` or `run.py` cannot be found, check `pwd` and
change into your clone first. In Git Bash, `/` is the Git installation directory, not the
NEXUS repository; create `.venv` only after entering the clone.

### macOS / Linux

```bash
git clone https://github.com/authorsauravkushwaha/AI-Research-Intelligence-Graph
cd AI-Research-Intelligence-Graph
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
python run.py --check
python run.py                 # → http://localhost:8000
```

### Windows with Git Bash

If you have not cloned the project yet:

```bash
cd /c/Users/SAURAV
git clone https://github.com/authorsauravkushwaha/AI-Research-Intelligence-Graph
cd AI-Research-Intelligence-Graph
```

If it is already cloned elsewhere, skip `git clone` and `cd` to that existing folder instead.
Then, from the folder containing `run.py` and `backend/`, run:

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install -r backend/requirements.txt
python run.py --check
python run.py                 # → http://localhost:8000
```

Use plain shell commands such as `python run.py --check`; do not include Markdown link
syntax like `[run.py](...)`. The activation command above is for Git Bash; PowerShell and
Command Prompt use their respective Windows activation scripts.

That is the whole setup. **No Neo4j, no API key, no Docker, no network.** The launcher
reports the active backend and capabilities:

```
NEXUS — AI Research Intelligence Graph
  · neo4j: not configured (in-process engine) · llm: not configured (graph-template answers) · embeddings: local lexical baseline
  ✓ installation looks good
```

Three optional upgrades, each independent:

```bash
cd native && make -j4            # build the C++ analytics kernel (auto-built on demand otherwise)
cp .env.example .env             # add NEO4J_URI / NEO4J_PASSWORD to use Neo4j
docker compose up                # Neo4j 5 + GDS + APOC + the API container
```

---

## Website & app (published)

**Live site: <https://authorsauravkushwaha.github.io/AI-Research-Intelligence-Graph/>** —
the eight-view static snapshot, with no backend and an installable phone/desktop shell.

GitHub Pages cannot run FastAPI, so the website is not a mock-up of NEXUS: it is NEXUS,
computed ahead of time. `scripts/build_site.py` drives the **real** application in-process —
the same store, analytics, gap engine, GraphRAG pipeline, planner and agent the API serves —
and records their answers as JSON next to a snapshot of the graph:

```
scripts/build_site.py ──► TestClient(create_app()) ──► data/graph.json (712 nodes / 2,981 edges)
                                                     data/index.json (counts, references, questions)
                                                     data/scope--*.json   (gap-engine resolutions)
                                                     data/gaps--*.json    (scored opportunities)
                                                     data/report--*.json  (+ .md report exports)
                                                     data/agent--*.json   (+ SSE stage traces)
```

In the browser, [frontend/assets/site.js](frontend/assets/site.js) re-implements the read-only
endpoints over that snapshot — subgraph selection, BFS paths, search, listings, the timeline and
the explorer are **computed live** on every click, not replayed from a screenshot. The gap
scopes, agent answers and reports are build-time engine output, and the site says so:

| what a visitor gets | how it is produced | how it is labelled |
| --- | --- | --- |
| knowledge graph, expand, neighbours, shortest paths | browser-side BFS over `graph.json` | `static_snapshot.backend: "static-snapshot"` |
| paper/node detail, search, listings, timeline, explorer | browser-side engine mirror | same numbers as the API (verified below) |
| FIND RESEARCH GAPS, opportunity reports, agent answers | the real engines at build time | `static_snapshot.mode: "recorded"` |
| Investigator | three exact question/scope pairs generated by the live API at build time | `static_snapshot.mode: "recorded"`; other pairs return HTTP 501 |
| any other scope/question | clear unsupported response plus local-run hint | no nearest-match substitution and no fabricated result |

It is a real app, not a page: `manifest.webmanifest` plus a service worker precise the shell and
the demo scopes, so it **installs** (`Add to Home Screen` / the install button in the header) and
**opens offline**. The build also ships the exported Cypher for the graph it describes
(`data/export-cypher.txt`), so the snapshot can be replayed into Neo4j.

```bash
pip install -r backend/requirements.txt -r scripts/requirements-site.txt
python scripts/build_site.py --out site            # writes generated snapshot to ./site
python -m http.server 8080 --directory site        # http://localhost:8080
```

### The published build is verified, not trusted

One script stands between the build and the URL, and `.github/workflows/pages.yml` runs it — a
failed check fails the deploy:

```bash
python scripts/build_site.py --out site                        # the engines run in-process
node scripts/check_site_data.mjs --site site --json /tmp/checks.json
```

```text
NEXUS static snapshot check — /home/user/AI-Research-Intelligence-Graph/site
  shell        7 pass
  snapshot     6 pass
  boot         2 pass
  parity       38 pass
  corpus       2 pass
  interactive  12 pass

67/67 checks passed · 22167 values compared against the engine's own recorded answers
```

The checker boots the **shipped** `assets/site.js` against the **shipped** snapshot and replays
every recorded engine answer through it: the default graph view, the timeline and explorer
overviews, all six planner fixtures, three exact Investigator snapshots, five paper details,
five node details and four searches — plus the whole 204-row paper list page by page. The
checker permits only the expected runtime `status` envelope, the browser-added
`static_snapshot` note, and the JS planner's `engine`/`source` values; numeric differences
caused by re-rounding an already-rounded float are counted and reported. Other differences
fail the build. Shortest paths are compared by endpoints, length and edge validity rather
than by a particular tie-break, because more than one shortest chain usually exists.

`site/` is generated and not committed; the workflow rebuilds it on every push to `main`.
The static layer is a snapshot of the curated demo corpus — it answers questions about those
204 papers, and it says so instead of guessing.

## The eight views

| # | View | What it shows |
| --- | --- | --- |
| 1 | **Home** | Corpus health, engine status, community map, activity timeline, top entities |
| 2 | **Investigator** | Research question → scoped paper retrieval/reranking → candidate opportunities → source evidence, observed graph paths, caveats, and falsifiable experiment proposal |
| 3 | **Research Explorer** | Field-level metrics: publishing pulse, method/dataset adoption, community structure |
| 4 | **Knowledge Graph** | Interactive **3D** graph — node types, communities, metrics, expand-on-demand, evidence highlighting |
| 5 | **Research Gap Finder** | Ranked candidate opportunities with a decomposed prototype-heuristic score |
| 6 | **Paper Explorer** | Every paper with its neighbourhood, claims, metrics and provenance |
| 7 | **AI Research Agent** | GraphRAG question answering with visible tool calls and a stage-by-stage trace |
| 8 | **Research Opportunity Report** | Exportable brief: scope, candidates, evidence, contradictions, next steps |

---

## Question-to-evidence Investigator

The Investigator is the end-to-end workflow for a research question. It extends the existing
FastAPI, GraphRAG, GapEngine, graph stores, and schema rather than introducing a second
corpus or graph model.

```bash
curl -s -X POST http://localhost:8000/api/investigate \
  -H 'content-type: application/json' \
  -d '{"question":"How can agent memory improve coordination in multi-agent systems?","scope_topic":"AI Agents"}'
```

The response reports the selected scope and its resolver matches, corpus coverage, the
actual GraphRAG retrieval-engine label, graph-store name, and an independent question-term
ranking of source-linked paper records. GraphRAG context is treated as recall-oriented input,
not as a final relevance ranking. GapEngine candidate scores remain their published
prototype heuristic; separate question-relevance evidence filters/ranks candidates without
rewriting those scores. No candidate is a novelty verdict.

Each surfaced candidate includes its score components, matched question terms, linked
source papers, observed relationship path(s) with provenance, claim-tension leads and
counterevidence status, corpus-coverage caveats, and a falsifiable experiment proposal.
The experiment describes an intervention, a source-literature baseline to select and
reproduce, a predeclared primary outcome, controls, and a criterion that would fail to
support the directional hypothesis. Unknown benchmark details are explicitly left for
full-text review; no outcomes are invented. Missing graph edges or claim tensions do not
prove prior work or disagreement is absent.

When the configured backend is Neo4j, the scope, GraphRAG, ranking, and analytics run over
the Neo4j-hydrated local graph projection. Reported Investigator paths are checked with a
bounded, timed, parameterized Neo4j traversal over observed corpus relationships. The
in-process mode uses bounded BFS and labels the path source. Neither path treats predicted
links as evidence. See [docs/API.md](docs/API.md) and
[docs/AUDIT_AND_IMPLEMENTATION_PLAN.md](docs/AUDIT_AND_IMPLEMENTATION_PLAN.md).

The static site records exactly three unfiltered question/scope runs (the buttons in the
Investigator form). They are build-time snapshots, not live browser retrievals. Any other
question/scope pair or filtered request returns an explicit HTTP 501 unsupported response;
there is no closest-question fallback.

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

> Reproducible API example — `POST /api/gaps {"topic": "AI Agents", "top_k": 5}`.
> The response carries the selected-scope counts, candidate pairs, component values, and
> the reason when no supported pair is available. Use the Investigator for question-specific
> filtering and paper reranking; neither workflow makes a claim of novelty.

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
                                    │  8 views · vanilla ES modules · three.js 3D  │
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
             │   validate arXiv ids · dedupe · label provenance · 204 papers / 51 claims│
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
| **C++17** | `native/` graph kernel (`nexus-kernel`) | Dependency-light graph algorithms, process-isolated; see `native/test/bench.sh` to run the synthetic benchmark locally |
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
(`echo '{"job":"pagerank","nodes":[...],"edges":[...]}' | native/build/bin/nexus-kernel`).
Run `native/test/bench.sh` to reproduce a synthetic benchmark on the current machine; its
timing output is environment-dependent and is not reported here as a portable guarantee.
When Neo4j/GDS is configured, the store may use GDS for supported metrics; Investigator
paths use bounded Neo4j traversal, while most Python graph workflows still use the hydrated
local projection.

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
- Neo4j data values and identifiers are bound parameters. Structural syntax comes from
  fixed/allow-listed labels and relationships; the bounded path pattern has a static
  four-hop ceiling and a bound caller limit. See `backend/graph/neo4j_store.py` and
  `cypher/queries/02_papers.cypher`.
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
| `POST` | `/api/investigate` | Question → scoped, reranked sources → explainable candidates, paths, caveats, experiment |
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

`backend/graph/factory.py` selects Neo4j only when it is enabled, configured, and
connectivity-verified; otherwise the API uses the embedded engine and reports the reason
in `/api/health`. Neo4j imports use stable IDs, `UNWIND`/`MERGE`, and bound row values,
then hydrate a local working projection for Python, GraphRAG, scope, and gap computations.
When configured and available, GDS is used for supported analytics; engine labels and
fallbacks remain visible. The Investigator verifies reported paths with a bounded,
time-limited, parameterized Neo4j query over observed relationships. **This repository's
Neo4j path is covered with offline fakes; no live Neo4j service was verified in this
implementation session.**

---

## Configuration

Everything is an environment variable; nothing is required for the demo. Full list with
comments in [`.env.example`](.env.example).

| Variable | Default | Purpose |
| --- | --- | --- |
| `NEO4J_URI` / `NEO4J_USERNAME` / `NEO4J_PASSWORD` / `NEO4J_DATABASE` | URI/password empty; database `neo4j` | Select the Neo4j store; connectivity is verified before use, otherwise the fallback reason is reported |
| `NEO4J_QUERY_TIMEOUT_SECONDS` | 30 | Per-query Neo4j timeout, including bounded Investigator path checks |
| `NEXUS_ENABLE_NEO4J` / `NEXUS_USE_GDS` | true / true | Enable Neo4j selection and optional GDS analytics |
| `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` | empty | Any OpenAI-compatible endpoint; empty → graph-template answers |
| `NEXUS_EMBEDDINGS_URL` | empty | Real embeddings; empty → deterministic local baseline |
| `NEXUS_MAX_GRAPH_NODES` / `NEXUS_MAX_GRAPH_EDGES` | 400 / 1200 | Hard bounds on graph payloads |
| `NEXUS_RATE_LIMIT_PER_MINUTE` | 240 | Per-IP request budget |
| `NEXUS_CORS_ORIGINS` | empty | Extra allowed origins (comma-separated) |
| `NEXUS_DEMO_MODE` | true | Deterministic demo behaviour |

---

## Testing

```bash
python -m pytest -q                    # offline unit/API/frontend suite
python run.py --check                  # installation preflight
python scripts/build_corpus.py --check # corpus validator (ids, dedupe, provenance)
make -C native test                    # native kernel self-tests
python run.py                          # start the API, then in another terminal:
./scripts/smoke.sh                     # live HTTP checks (including investigation and SSE)
```

CI runs all of the above plus a `docker build` and a vendored-frontend/no-CDN audit on
every push — see [.github/workflows/ci.yml](.github/workflows/ci.yml).

The published website and app are tested the same way (and the deploy is gated on it):

```bash
python scripts/build_site.py --quiet                              # build the snapshot from the engines
node scripts/check_site_data.mjs --site site --json /tmp/checks.json  # replay it, 67 checks in the verified build
```

See [Website & app (published)](#website--app-published) — `site/` is generated, so a fresh
clone builds it rather than carrying it.

---

## Demo script (3–5 minutes)

The walkthrough in [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) uses the local offline mode
and makes no live-Neo4j claims. Short version:

1. **Home / health** — show corpus counts, actual graph backend, GraphRAG/embedding labels,
   and whether the optional LLM is configured.
2. **Investigator** — run *“How can agent memory improve coordination in multi-agent systems?”*
   in *AI Agents*. Walk through the scope, independently reranked source papers, candidate
   score decomposition, evidence links, graph path/provenance, caveats, and falsification
   criterion. Treat the candidate as a lead for literature review, not a novelty claim.
3. **Trace the path** — use a result's graph-trace action; the graph view is seeded from the
   evidence path and labels the observed relationship types.
4. **Compare exact demos** — use the *Tool Use* and *Retrieval-Augmented Generation*
   examples. The latter may have no supported opportunity candidate; its scoped sources and
   honest empty-state reason are still useful results.
5. **Existing views** — show the gap engine or agent as a separate workflow and retain each
   view's heuristic/conflict caveats. Exports and reports remain available.

When using the static site, explain that the three Investigator answers are exact build-time
snapshots; an unrecorded question/scope pair returns HTTP 501 rather than a substituted answer.

---

## Project layout

```
AI-Research-Intelligence-Graph/
├── backend/
│   ├── api/            FastAPI app, 30+ routes, pydantic schemas
│   ├── agents/         agent loop, 13 research tools, optional LLM client
│   ├── algorithms/     native-kernel bridge + pure-Python fallbacks
│   ├── engine/         question Investigator, gap engine, explorer analytics
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
├── scripts/            corpus builder/validator, live smoke sweep, static-site builder
├── site/               generated website + PWA (gitignored; built by scripts/build_site.py)
├── tests/              offline pytest suite (run locally for the current count)
├── docker-compose.yml  Neo4j 5.26 + GDS + APOC + API
└── run.py              launcher with preflight checks
```

---

## Limitations and what we would do next

Honest list, because a research prototype that hides its limits is not a research prototype:

- **The corpus is a curated demo set (204 paper records), not a literature review.** The
  shipped app loads and validates this local corpus; the presence of ingestion/sidecar code
  does not mean this run fetched or continuously synchronizes external sources. Inspect
  `data/demo/PROVENANCE.md` for field-level provenance. The existing graph schema is reused
  for the Neo4j loader.
- **Opportunity and relevance scores are uncalibrated heuristics.** Read component values
  and within-scope ranking signals; do not treat them as probabilities, effect sizes, or
  scientific merit. There is no expert-labelled validation study documented here.
- **Retrieval defaults to a local lexical baseline** so the demo works offline. The API
  reports the actual retrieval/embedding engine; an external embeddings service is optional.
- **Claim extraction is rule-based**, tuned for precision (it misses contradictions
  rather than inventing them).
- **Neo4j/GDS is not live-verified in this workspace.** Offline fake-driver tests cover
  connectivity errors, parameter binding, import rows, claim-provenance hydration, and
  bounded-path query construction. A real configured Neo4j service is still required to
  validate deployment, permissions, GDS installation, and live query behavior. When selected,
  Neo4j backs import and path queries while scope/GraphRAG/GapEngine computations use a
  Neo4j-hydrated local projection.
- Next: streaming corpus ingestion from arXiv/S2/Crossref with incremental graph
  upserts, GDS-native similarity pipelines, an evaluation harness against expert-labelled
  gap lists, and multilingual claim normalisation.

---

## Documentation map

| Document | What it covers |
| --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layering, the two engines, Neo4j projection semantics, security posture |
| [docs/INVESTIGATOR.md](docs/INVESTIGATOR.md) | Scope/reranking method, candidate support, paths/provenance, falsifiable proposal, static snapshots |
| [docs/METHODOLOGY.md](docs/METHODOLOGY.md) | Corpus → graph, analytics, claim scoring, the full opportunity-score model, limitations |
| [docs/GRAPHRAG.md](docs/GRAPHRAG.md) | The three-channel retrieval pipeline, synthesis paths, explainability contract, MCP |
| [docs/API.md](docs/API.md) | Every endpoint with request/response shapes and error behaviour |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | Node labels, the 14 relationship types, wire format, corpus record format |
| [docs/UI.md](docs/UI.md) | The eight views, graph controls, static-snapshot boundary, accessibility and degradation behaviour |
| [docs/SAFETY.md](docs/SAFETY.md) | The honesty rules and where each one is enforced in code |
| [scripts/build_site.py](scripts/build_site.py) | The static-site builder: what it records, how the snapshot is laid out, the PWA shell |
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
