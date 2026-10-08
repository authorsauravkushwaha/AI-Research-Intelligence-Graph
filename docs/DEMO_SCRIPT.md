# Demo script (3–5 minutes)

Everything below runs from a clean checkout with **no Neo4j, no API key and no network**:

```bash
pip install -r backend/requirements.txt
python run.py                  # → http://localhost:8000
./scripts/smoke.sh             # optional: prove all 46 endpoints respond before you present
```

Have two terminals ready: one running the server, one free for `curl` and exports.

---

## 0 · Opening line (10 s)

> "This is not a chatbot over papers. It is a **knowledge graph** of research, and every
> answer you'll see is computed from graph structure: 204 papers, 713 nodes, 2,963
> relationships, 15 research communities, 51 claim records."

**Say this while the Home view loads** — the status bar already tells the audience what
they are looking at: engine (`in-process`), embedding baseline, LLM (`graph-template`),
corpus path.

## 1 · Home (20 s)

Point at three things, in this order:

1. **Scope honesty**: "204 papers, 2013–2025, every id a real arXiv record, summaries are
   editorial and labelled as such — the provenance audit is in the repo."
2. **Community map**: 15 detected communities with sizes and top topics.
3. **Activity timeline**: papers per year — the input the gap engine's *research activity*
   component uses.

## 2 · Knowledge Graph (45 s)

Switch to **Knowledge Graph**. It renders in 3D (drag to rotate, wheel to zoom).

1. Colour by **node type**, then toggle to **community** — "colour is structure, not
   decoration: this is Louvain output over the concept graph."
2. Click **ReAct** (`paper:2210.03629`): the side panel shows PageRank, betweenness,
   degree and *what those numbers mean*.
3. **Expand on demand** from that node — "the graph is paged and bounded; we never load
   the whole graph into the browser."
4. In the path tool, connect `paper:2210.03629` → `topic:agent-memory` and watch the
   evidence path highlight. "That highlight is the shortest relationship path the engine
   found — the same primitive the gap engine uses."
5. Open **Query planner**, press **Plan** on the prefilled DSL: the card shows the
   *parameterised* Cypher, the bind parameters, the explanation and the cost estimate.
   "That plan came from the Kotlin sidecar on :8092 — `engine: nexus-kotlin-planner`.
   Every value is a bind parameter, labels come from a whitelist: try
   `type in (Paper, SecretVault)` and it refuses instead of guessing. If the JVM is not
   running, the Python port answers the identical plan — the badge says which one ran."

## 3 · FIND RESEARCH GAPS — the killer feature (75 s)

Switch to **Research Gap Finder**. Leave the topic as **AI Agents**, `top_k = 5`, Run.

1. **Read the banner**: prototype heuristic, safety notice, scope (101 papers, 46 topics).
2. **Open the top candidate** and walk the score decomposition aloud:
   - *Community Separation* 30% — how far apart the two clusters sit in the structure
   - *Semantic Similarity* 25% — related enough to combine, distinct enough to matter
   - *Relationship Sparsity* 20% — how few recorded edges cross the boundary
   - *Research Activity* 15% — is anyone working around this at all
   - *Bridge Potential* 10% — is there structure that could carry a new connection
   "Five numbers, published weights, raw values next to the normalised ones — you can
   argue with each of them."
3. **Evidence**: the papers defining each cluster, the bridge papers (with years), any
   detected claim tensions in the area, and the trajectory (`no bridges in corpus` /
   `persistent gap` / `emerging bridge` / `closing gap`).
4. **The honesty beat** — type **Agent Memory** and run it again. The scope is 17 papers
   and *every* pair is entangled (most memory papers in this slice also study multi-agent
   systems). NEXUS returns **zero candidates and lists the excluded pairs with the
   reason**. Say: "It refuses to invent a gap. That's the difference between a demo and a
   tool — the bar is visible, and it doesn't move when the graph is thin."
5. Optional: narrow `year_min = 2023` on *AI Agents* and show the ranking change.

## 4 · AI Research Agent (60 s)

Switch to **AI Research Agent**. Ask:

> "Which claims contradict each other about agent memory?"

1. Watch the **stage trace** stream: `intent → plan → tool → retrieval → synthesis →
   answer`, each with milliseconds. "You're seeing the pipeline, not a spinner."
2. Read the answer header: `answer_engine graph-template`, `used_llm false`.
   "There is no API key in this environment. The answer is composed from graph evidence,
   and the system says so."
3. Open **Tool calls** and **Graph evidence**: which tool ran, what it returned, which
   papers support the claim — 18 detected claim tensions, each with score and reasons.
4. Ask one follow-up from the suggested list, e.g. *"What are the most important
   papers?"* → shows ReAct's betweenness 0.1485 and degree 33, with the *"attention, not
   quality"* caveat.

## 5 · Research Opportunity Report (30 s)

Switch to **Research Opportunity Report**, run for *AI Agents*, then:

1. **Download Markdown** and open it — a complete brief: scope, executive summary, ranked
   candidates with component tables and evidence links, contradictions, limitations,
   sources, and the safety notice.
2. **Export the graph** as GraphML (open in Gephi/yEd) and as Cypher — "the export emits
   the parameterised Cypher that recreates this slice, so the demo graph and the Neo4j
   graph are the same object."

## 6 · Close (20 s)

> "Two engines, one query interface: the in-process engine you just watched, and Neo4j 5
> with GDS behind `docker compose up`. Same schema, same algorithms, same answers — the
> badge in the status bar changes from *in-process* to *neo4j*. And the C++ kernel that
> ran Louvain and PageRank in front of you is a static binary with no dependencies."

Optionally show `docker compose up -d`, then `/api/health` reporting
`requested_backend: neo4j` with `degraded: false`.

---

## Talking points if a judge pushes

| Challenge | Answer |
| --- | --- |
| "Isn't this just an LLM prompt?" | No LLM key is configured. Switch it off entirely and every view still works; the answer says `graph-template`. |
| "How is this different from vector search?" | Gaps, centrality, bridges and conflicts are structural facts. Similarity can only return what already exists. |
| "Is the score scientifically validated?" | No — it says *prototype heuristic* everywhere, the weights are published, and the components are inspectable. |
| "Where does the data come from?" | Real arXiv ids and titles; editorial summaries and labels, each labelled; provenance audit in `data/demo/PROVENANCE.md`. |
| "Could it be gamed?" | The corpus-bounded language, the excluded-pairs list and the per-record provenance are all designed so a wrong claim is visible, not persuasive. |
| "What breaks at scale?" | The store interface and schema don't change; Neo4j + GDS replaces the embedded engine, and the kernel already handles 20k nodes at ~100 ms PageRank. |

## Pre-flight checklist

```bash
python run.py --check          # dependencies, corpus, kernel, capability flags
python -m pytest -q            # 75 tests
./scripts/smoke.sh             # 46 live endpoint checks
curl -s localhost:8000/api/health | python3 -m json.tool | head -30
```

Browser: full-window, zoom 100 %, and **no other tabs** — the 3D view is the thing people
photograph.
