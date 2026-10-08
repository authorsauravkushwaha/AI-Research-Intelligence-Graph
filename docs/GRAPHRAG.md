# GraphRAG pipeline

NEXUS answers research questions from **graph structure plus retrieval**, and it can do so
with no language model at all. This document describes the pipeline, what each stage
contributes, and why a purely vector-based retriever cannot replace it.

---

## Why not vector-only retrieval

A vector index answers *"which documents look like this question?"*. The questions this
project exists to answer are not of that shape:

| Question | Vector-only result | Graph result |
| --- | --- | --- |
| "Where is this field under-explored?" | The most similar existing papers — by definition, work that already exists | Two clusters with measured separation, sparsity and no recorded bridges |
| "Which findings conflict?" | Papers whose text is similar (often agreeing ones) | Claim pairs with opposing polarity/direction over shared vocabulary |
| "What connects these two areas?" | Papers similar to *both* (usually neither) | Bridge nodes and shortest paths between the two concepts |
| "Which works are pivotal?" | Nothing (popularity ≠ topology) | PageRank / betweenness / degree over the concept graph |
| "Why should I believe this?" | Model output, no evidence chain | Component values, supporting papers, traversed paths |

So retrieval in NEXUS is deliberately **three-channel**:

```
question ──▶ 1. UNDERSTAND ─▶ intent + entities + optional LLM plan
                    │
                    ▼
        2. RETRIEVE (three channels, merged)
           ├── a) VECTOR      VectorIndex.search()        semantically similar papers
           ├── b) GRAPH       bounded expansion (depth ≤ 2) over STUDIES / USES_METHOD /
           │                  USES_DATASET + community membership + bridge nodes + paths
           └── c) STRUCTURE   centrality, detected claim tensions, predicted links
                    │
                    ▼
           GraphContext { seeds, papers, topics, methods, datasets, authors,
                          communities, claims, conflicts, predicted_links,
                          paths, metrics, retrieval_engine, expansion_engine }
                    │
                    ▼
        3. SYNTHESISE  GraphContext.to_prompt(max_chars=9000)
           ├── LLM configured → grounded prose from the context
           └── otherwise      → deterministic graph template (same structure)
                    │
                    ▼
        4. EXPLAIN   claim · reasoning[] · graph_evidence · supporting_papers ·
                     predicted_links · conflicts · confidence + confidence_basis ·
                     safety_notice · follow_up_questions
```

---

## Stage 1 — understand

`backend/agents/agent.py` classifies the question into one of 13 intents with regex rules
(`research_gaps`, `evidence_for_gap`, `experiment_design`, `bridge_papers`, `communities`,
`methods_across_fields`, `growing_topics`, `important_papers`, `centrality`,
`connect_two_areas`, `contradictions`, `summarize`, plus `general_qa` as the fallback).
Each intent declares which tools it needs. When an LLM key is configured the model may
re-plan the tool sequence; the rule plan remains the fallback and the *shape* of the
answer never changes.

Intent detection is reported in the response (`intent.name`, `intent.confidence`,
`intent.matched`), so a wrong plan is visible rather than hidden inside a plausible
paragraph.

## Stage 2 — retrieve

`backend/rag/graphrag.py::retrieve(question, labels, top_k, depth, include_conflicts,
extra_seed_ids, focus_nodes)` merges the three channels.

* **Vector channel** — `backend/rag/embeddings.py` provides either a configured
  OpenAI-compatible embeddings endpoint or a deterministic local lexical baseline
  (reported as `embedding_engine: local:lexical-baseline`). Vectors are queried with the
  native kernel's kNN (cosine, threshold 0.05).
* **Graph channel** — bounded expansion from the seed set over associative relationship
  types, plus community co-membership, bridge nodes (high betweenness *between* the two
  sides of a candidate) and shortest paths. Depth is capped at 3 and every expansion is
  budget-limited, so a broad question cannot pull the whole graph into the prompt.
* **Structure channel** — metrics for every node in context (PageRank, betweenness,
  degree, community), detected claim tensions, and algorithmic link predictions
  (always labelled as hypotheses).

The result is a `GraphContext` object, not a text blob: the same structure is used to
build the prompt, the UI evidence panel, and the JSON response.

## Stage 3 — synthesise

`GraphContext.to_prompt(max_chars=9000)` renders the context with **explicit node ids**.
Two paths:

* **With an LLM** (`LLM_API_KEY` set) — the model writes the prose. The prompt lists graph
  facts with ids and instructs the model to use only those papers; anything it names that
  is not in the context is a bug the evidence panel exposes immediately.
* **Without an LLM** — `answer_offline()` produces a deterministic answer from the same
  context: which papers are central and why, which clusters exist, which bridges and
  tensions were found, with counts. The response says
  `answer_engine: "graph-template"`, `used_llm: false`, so nobody mistakes it for model
  output. The demo runs entirely in this mode.

Both paths return `confidence` plus a `confidence_basis` string (evidence volume, scope
size, whether the answer rests on metrics or on detected tensions).

## Stage 4 — explain

Every answer carries:

| Field | Meaning |
| --- | --- |
| `explainability.claim` | One-sentence statement of what the system concluded |
| `explainability.reasoning[]` | The ordered steps that produced it (which tool, what it returned) |
| `explainability.graph_evidence` | The concrete graph facts: ids, metrics, communities, paths |
| `explainability.supporting_papers[]` | Papers with title/year/url — the human verification path |
| `explainability.confidence`, `confidence_basis` | How strong the evidence is, and why |
| `explainability.safety_notice` | The corpus-bounded, hypothesis-only framing |
| `tool_calls[]` | Instrumented calls: tool, arguments, engine, elapsed ms, ok/failed |
| `paths[]`, `predicted_links[]`, `conflicts[]` | The traversals and inferences behind the answer |
| `follow_ups[]` | Next questions the evidence supports |

## Streaming

`POST /api/agent/stream` emits the pipeline as server-sent events —
`intent → plan → tool (per call) → retrieval → synthesis → done → answer|error` — each
with `elapsed_ms`. The UI renders them live, which turns "trust me" into "watch it work".

## MCP

The same 13 tools are exposed over stdio JSON-RPC by `mcp/server.py`
(`initialize`, `tools/list`, `tools/call`), so an external agent can use NEXUS as its
research-tool server without touching the HTTP API. One implementation, three consumers
(the HTTP API, the internal agent, MCP).

## Failure behaviour

| Missing | Effect |
| --- | --- |
| LLM key | Deterministic template answers; `answer_engine: graph-template` |
| Embeddings endpoint | Local lexical baseline; labelled in the response |
| Native kernel | Python fallbacks; the engine per metric is reported |
| Neo4j | Embedded store; `store.degraded` + reason in `/api/health` and the UI |
| Network | Nothing in the retrieval path needs it — corpus and vectors are local |
