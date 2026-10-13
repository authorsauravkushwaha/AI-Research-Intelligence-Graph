# Demo script — evidence-led Investigator (3–5 minutes)

This walkthrough uses the local in-process backend. It needs no Neo4j or LLM credentials.
It does not imply that a Neo4j server is live or that candidate opportunities are novel.

## Start the app

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r backend/requirements.txt
python run.py
```

Open <http://localhost:8000>. To verify the offline HTTP surface in another terminal, run
`./scripts/smoke.sh` while the server is up.

## 1. Establish provenance and backend (30 seconds)

On Home, point out the corpus counts, active graph backend, retrieval/embedding labels, and
LLM status. Open `/api/health` if asked. Say: **“This run uses the in-process graph store;
GraphRAG reports its actual retrieval engine. Opportunity scores and claim tensions are
heuristics or review leads, not validated findings.”**

## 2. Ask a question (90 seconds)

Open **Investigator**. Run the included question with scope **AI Agents**:

> How can agent memory improve coordination in multi-agent systems?

Walk through the response in this order:

1. **Scope and coverage** — the requested topic, resolver matches, number of records in
   scope, year coverage, and question-term coverage. Note any corpus limitations.
2. **Retrieval provenance** — the actual GraphRAG engine label and selected graph backend.
   GraphRAG provides context; source papers are independently reranked against the question
   and selected scope.
3. **Candidate opportunities** — read a candidate as “worth reviewing,” never “new.” The
   NEXUS Opportunity Score is the existing prototype heuristic. Its component weights and
   score are shown separately from the Investigator's question-relevance signal.
4. **Source records** — open the linked arXiv records. The corpus may carry editorial scope
   notes rather than full abstracts, and the UI labels that provenance.
5. **Graph path and counterevidence** — read the observed relationship types and provenance.
   Select **Trace these nodes in the graph** to seed the interactive graph view. A missing
   path or detected tension is not evidence that prior work or disagreement is absent.
6. **Experiment proposal** — distinguish the suggested intervention, baseline selection,
   predeclared primary outcome, controls, and falsification criterion from measured results.
   The proposal lists unknowns that must be resolved by full-text review.

## 3. Compare two exact demo questions (45–60 seconds)

Select **Planning + API tool use** with scope **Tool Use**. The candidate list is independently
filtered by the question's concept terms; it is not a copy of the corpus-wide gap ranking.
Then select **RAG + factual reliability** with scope **Retrieval-Augmented Generation**.
This scope is small and can return no supported candidate. The response should still show
relevant records, coverage, and an explicit empty-state reason rather than filling the panel
with unrelated candidates.

## 4. Show the static-site boundary (30 seconds)

The static build records exactly the three question/scope pairs above at build time. Its
Investigator response is labelled as a snapshot; the browser does not run a new retrieval,
graph traversal, or Neo4j query. Change the question or add a filter: the static route returns
HTTP 501 and lists the supported exact pairs. **No nearest-question answer is substituted.**
Run the local API for a live investigation.

## 5. Close with the backend distinction (30 seconds)

When Neo4j is configured and connectivity-verified, the Investigator reports that scope,
GraphRAG, ranking, and analytics use a Neo4j-hydrated local projection. Its reported evidence
paths are checked using a bounded, parameterized traversal against Neo4j; those paths are not
presented as locally computed database paths. The offline test suite exercises the connection,
import, hydration, provenance, and query contracts with fakes. Do not claim live Neo4j
verification unless you have actually run the app against a configured service.

The existing GraphRAG agent, gap finder, graph explorer, and reports remain available as
separate workflows and retain their own limitations and provenance labels.

## Answers to common questions

| Question | Answer |
| --- | --- |
| “Is this a novelty detector?” | No. The opportunity score is an uncalibrated prototype heuristic over a curated corpus. Review the cited work and search outside the corpus. |
| “Does a missing edge mean those fields were never combined?” | No. It means only that this graph did not return that edge/path within its scope and traversal bound. |
| “Are the claim conflicts established contradictions?” | No. Automated tensions are hypotheses for source review, not adjudicated disagreements. |
| “What does GraphRAG contribute?” | A retrieval context with an explicit engine label. The Investigator separately reranks paper records against the question and scope. |
| “Is the Neo4j path live-tested?” | Not in this implementation session. Offline fake-driver tests cover connection verification, parameter binding, safe failures, idempotent import rows, hydration, and the bounded-path query. |
| “What does the experiment proposal prove?” | Nothing yet. It is a falsifiable study design sketch; its baseline and protocol require full-text review, and no outcomes are reported. |
