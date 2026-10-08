# Safety, honesty and data-integrity policy

NEXUS makes claims about research. That is a position of trust, so the rules below are
enforced in code and copy — not just documented. Every rule lists where it lives.

---

## 1. Never claim that nobody has researched something

**Rule.** A finding is always scoped to the analysed corpus and phrased as a hypothesis.

* **In copy.** `SAFETY_NOTICE` (`backend/engine/gaps.py`) travels with every gap, report
  and agent answer: *"This is an AI-generated hypothesis based on the analyzed research
  corpus. It cannot detect work outside the analyzed corpus. Do not treat it as evidence
  that nobody has researched this topic."* The UI banner shows the notice verbatim.
* **In generated text.** Candidate hypotheses say *"relatively under-explored within the
  selected research corpus"*, never "no one has studied this".
* **In tests.** `tests/test_gaps.py` asserts that the word "nobody" never appears in a
  generated hypothesis and that the notice carries the cannot-detect-work-outside clause.

## 2. Label every inference as an inference

| Inference | How it is marked |
| --- | --- |
| Predicted paper links | `PREDICTED_LINK` edges, `is_hypothesis: true`, disclaimer on `/api/predictions` ("algorithmic hypotheses … must never be read as facts") |
| Claim tensions | `CONTRADICTS` edges with score, kind and reasons; copy says *"potential contradiction (AI-detected hypothesis)"* and *"not established"* |
| Communities | the payload reports the Louvain `engine` that produced them; names are descriptive, not canonical |
| Centrality | Every leaderboard says attention ≠ quality |
| Opportunity score | Labelled *"prototype heuristic — not a validated scientific metric"* in the API model, the UI, and the exported report |
| Embedding similarity | `embedding_engine` names the actual model or the local lexical baseline |

## 3. Distinguish real data from editorial annotation

`data/demo/PROVENANCE.md` is the audit; the pipeline enforces it:

* arXiv ids/titles/years are real; every record links to its arXiv page.
* `summary_source` is `editorial-summary` or `taxonomy` — the second means *no summary
  text at all*, so nothing can be mistaken for an abstract.
* `author_status` is `verified` or `not-collected`; uncollected authorship stays empty,
  and `tests/test_corpus.py` asserts a `verified` record actually lists authors.
* Claims carry `source: editorial-distillation` and a provenance block; tests assert it.
* No citation counts, h-index or impact metrics — the corpus cannot support them.
* `scripts/build_corpus.py` refuses to write a corpus with a broken reference or below the
  100-paper floor, and a test re-derives the shipped corpus from the curator tables so a
  record cannot silently disappear.

## 4. Explainability is part of the contract

Every AI conclusion ships with claim, reasoning steps, graph evidence, supporting papers,
confidence and confidence basis, and follow-up questions
(`backend/agents/agent.py::to_json`). A conclusion you cannot audit is not returned.

## 5. Security posture

| Area | Implementation |
| --- | --- |
| Secrets | Environment variables only (`.env`, git-ignored); the API exposes capability *booleans*, never keys |
| Database | Parameterised Cypher only (`cypher/queries/*.cypher`, bound params in `backend/graph/neo4j_store.py`); no string-built queries anywhere |
| Input | Pydantic validation at the boundary + hard server-side bounds (`max_nodes ≤ 400`, `max_edges ≤ 1200`, `depth ≤ 3`, `limit` caps) |
| Abuse | Per-IP rate limiting (default 240/min → `429` with `Retry-After`) and CORS allow-listing |
| Front end | No credentials, no tokens, no external CDN; three.js vendored locally |
| Errors | Server-side logging with generic JSON errors returned to clients (no internals leaked) |

## 6. What the system explicitly does **not** do

* It does not rank researchers or institutions.
* It does not claim novelty: a candidate is a *question to investigate*, and the corpus is
  a 204-paper slice of a much larger literature.
* It does not treat a high centrality score as a quality judgement.
* It does not present an LLM's sentence as evidence: papers, ids and metric values are the
  evidence, and the model is only allowed to narrate them.
* It does not fabricate citations — you can open every referenced paper at its arXiv URL.

## 7. Reporting a problem

If a generated hypothesis, a claim tension or a record's provenance looks wrong, open an
issue with the paper id, the endpoint, and the exact text. Provenance corrections are the
highest-priority fixes in this repository.
