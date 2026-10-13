# Investigator methodology and response contract

The Investigator (`POST /api/investigate`) composes existing NEXUS components. It does not
replace GraphRAG, the opportunity-score model, or the graph schema. Its job is to turn a
question into a scoped, auditable review bundle without pretending that a curated corpus is
a complete literature search.

## 1. Scope and coverage

The caller may provide `scope_topic`; if omitted, the Investigator chooses a corpus topic
only when normalized question terms provide a clear label match. Otherwise, it reports a
whole-corpus scope. Optional year bounds and field filters are passed through to the
existing `GapEngine.scope()` routine. The response preserves the scope resolver's strategy,
matched topics, requested/selected topic, counts, and year/field coverage.

A scope that is empty or too small for the GapEngine returns an explicit reason. It does not
silently widen to unrelated papers or use global candidates to fill the result.

## 2. GraphRAG context and independent paper ranking

GraphRAG is called for its normal retrieval and graph-expansion context. The response
preserves the real `retrieval_engine` and `expansion_engine` labels (for example,
`local:lexical-baseline`) and reports the number of raw context papers. Its order is not
assumed to be question relevance.

Every in-scope Paper record is independently reranked from normalized question-specific
terms after removing generic domain words and the selected scope's own label. Coverage is
measured in these fields:

| Signal | Weight | How it is used |
| --- | ---: | --- |
| Title | 52% | Weighted query-term coverage with IDF over the scoped documents |
| Collected summary | 30% | Same coverage; `summary_source` is returned because many entries are editorial scope notes, not abstracts |
| Topic/method labels | 18% | Same coverage against graph taxonomy labels |
| GraphRAG paper-seed score | 5% before normalization | Small tie signal, never a replacement for question-term support |

The first three weights sum to one; the seed term is added and the total is normalized by
1.05. A paper must have at least one query-term match to be returned. The API includes the
matched terms and which text/taxonomy fields matched. This is a deterministic lexical
heuristic, not semantic relevance calibrated against human judgments.

## 3. Candidate opportunities

The existing `GapEngine.find_gaps()` runs in the selected scope. Candidate relevance is
computed separately from the published Opportunity Score:

- compare question-specific terms to candidate concept labels (cluster names, topics,
  methods), not to accidental words in evidence-paper titles;
- do not count generic domain/scope terms as sufficient support when more specific terms
  remain;
- filter pairs that lack question-term support and sort supported pairs by a separate
  relevance signal;
- preserve `opportunity_score`, its component values, and its documented formula unchanged.

If the gap engine produced no pair, or if no pair meets the question-support rule, the
response returns no opportunity candidates and a reason. A candidate is a lead for
literature review—not a novelty claim, probability, or measure of scientific value.

## 4. Evidence, paths, and counterevidence

Each candidate carries source Paper IDs/URLs, years, summary provenance, evidence roles,
and any bridge papers already present in the selected graph. The API includes observed
paths over the existing graph relationships (`STUDIES`, `USES_METHOD`, `USES_DATASET`,
`CITES`, `AUTHORED`, `MAKES_CLAIM`, `AFFILIATED_WITH`) and reports each edge's direction,
type, and provenance. Similarity, association, predicted-link, and automated contradiction
edges are not accepted as evidence-path hops.

With the in-process backend, the store uses bounded breadth-first traversal. When Neo4j is
selected, scope selection, GraphRAG, candidate ranking, and most analytics use the
Neo4j-hydrated local projection; each Investigator path is separately verified by a
parameterized Neo4j shortest-path query, capped at four hops and subject to the configured
query timeout. A missing result means only that this graph returned no path under these
bounds. It does not establish that no work or relationship exists elsewhere.

Claim-tension records are included only when their source-paper IDs touch a candidate's
clusters/bridges. They are explicitly labelled automated hypotheses that require reading
both original claims. No detected tension is not evidence of consensus.

## 5. Falsifiable experiment proposal

For each candidate, the deterministic proposal supplies:

- a directional hypothesis;
- an intervention to specify from the selected source literature;
- a reproducible comparator to choose after full-text review, not from the NEXUS score;
- one task-appropriate primary outcome, to predeclare before runs;
- controls, repeat/uncertainty reporting suggestions, and secondary outcomes;
- a failure criterion (a non-positive held-out difference under the predeclared analysis
  does not support the directional hypothesis);
- unknown protocol, baseline, benchmark, and effect-size details to resolve from source
  papers before running a study.

The metric descriptions are study-design suggestions, not corpus measurements. NEXUS
reports no synthetic outcome, effect size, benchmark score, or scientific result.

## 6. Static-site behavior

The static builder (`scripts/build_site.py`) records exactly three question/scope runs from
this Investigator form. `frontend/assets/site.js` serves the matching unfiltered pair,
adds a static-snapshot note, and returns HTTP 501 for any other pair or non-default filters.
The browser does not rerun retrieval or graph traversal, and no nearest-question response
is substituted. The `data/index.json` manifest identifies the supported pairs.

## 7. Verification boundary

Offline tests cover query validation, question-specific ranking, evidence/path shape,
claim provenance hydration, stable Neo4j import rows, query parameterization, and explicit
static unsupported behavior. Neo4j driver tests use an offline fake; no live Neo4j result is
claimed unless an operator configures and tests a real service. See
[`AUDIT_AND_IMPLEMENTATION_PLAN.md`](AUDIT_AND_IMPLEMENTATION_PLAN.md) for the session's
verification record.
