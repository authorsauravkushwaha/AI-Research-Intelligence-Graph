# Methodology

What NEXUS computes, how, and what the numbers do and do not mean. If you only read one
section, read [§5 Limitations](#5-limitations).

---

## 1. From corpus to concept graph

The corpus (`data/demo/corpus.json`) is a set of records: real arXiv ids and titles,
editorial summaries, labelled topics/methods/datasets, a curated citation subset and
claim records. `backend/ingestion/graph_builder.py` materialises it as a heterogeneous
property graph:

```
Paper ──STUDIES──▶ Topic
Paper ──USES_METHOD──▶ Method
Paper ──USES_DATASET──▶ Dataset
Paper ──AUTHORED──◀ Author
Paper ──CITES──▶ Paper
Paper ──MAKES_CLAIM──▶ Claim
```

Everything derived from analytics is added afterwards and is always labelled:
`BELONGS_TO` (Louvain), `SIMILAR_TO` (vector similarity above threshold),
`CONTRADICTS`/`SUPPORTS` (claim opposition scoring) and `PREDICTED_LINK` (link
prediction). The last two are **inference**, not fact, and are presented that way
everywhere (see [SAFETY.md](SAFETY.md)).

Identifiers are human-readable slugs (`topic:agent-memory`), so a judge can read a graph
payload without a lookup table.

---

## 2. Graph analytics

| Metric | Implementation | Notes |
| --- | --- | --- |
| PageRank | native kernel → GDS → Python | Damping 0.85, weighted, converges < 60 iterations; verified to sum to 1.0 |
| Louvain communities | native kernel → GDS → Python | Seeded (42) for determinism; resolution 1.2 (concepts), 1.35 (sub-cluster) |
| Betweenness centrality | native kernel (sampled) → GDS → Python | Exact for ≤ 400 nodes, 220-source approximation above, stated in the response |
| Adamic–Adar / Jaccard / common neighbours | native kernel → Python | Link prediction only; emits `PREDICTED_LINK` hypotheses |
| kNN over embeddings | native kernel → Python | Cosine similarity, threshold 0.05, top-k bounded |
| Shortest path / neighbourhoods | native kernel → Python | Depth-bounded; used for evidence paths |
| Degree, connected components | native kernel → Python | Coverage and isolation checks |

**Centrality is attention, not quality.** A paper with high PageRank is structurally
central *in this corpus*; that says nothing about its scientific correctness, and every
API response and UI surface repeats that sentence.

---

## 3. Claim-level contradiction detection

`backend/ingestion/claims.py` extracts claim records (subject, predicate, polarity,
direction, assertiveness, tokens) and compares every cross-paper pair. A pair is a
tension only when there is **explicit opposition** *and* enough shared substance:

```
candidate = explicit opposition (different polarity ∨ opposing direction)
            ∧ (identical subject+predicate ∨ ≥2 shared normalised tokens, ≥20 % overlap)
            ∧ different papers

score    =  0.45 · polarity differs
          + 0.35 · opposing direction
          + 0.20 · same subject/predicate
          + 0.10 · both assertive
          + 0.15 · lexical overlap
          − 0.20 · same polarity and direction        (penalty, not veto)
threshold = 0.45
```

The gate is intentionally **conservative**: it prefers to miss a contradiction rather
than invent one. Results are surfaced as *"claim-level tensions to check"*, with the two
claim texts, the score and the reason attached — never as "these authors disagree".

---

## 4. The NEXUS Opportunity Score

Computed by `backend/engine/gaps.py` for every candidate pair of communities in scope.

### 4.1 Candidate construction

1. Scope the corpus (topic/year/field).
2. Build the concept graph: papers plus the topics, methods and datasets they study.
3. Louvain at resolution **1.2** → concept communities (candidate clusters).
4. Louvain at resolution **1.35** within large communities → sub-clusters
   (`granularity: "sub-cluster"`).
5. Add **topic-level candidates** (negative ids, topics with ≥ 4 papers) so a single
   strong topic pair is not hidden by a big community.
6. Reject degenerate pairs: overlapping membership, shared topic, duplicate names;
   cap at 12 clusters.

### 4.2 Five absolute features → weighted sum

| Weight | Feature | 0 means | 1 means |
| ---: | --- | --- | --- |
| **30 %** | `community_separation` | same community | maximally separated communities in the scoped structure |
| **25 %** | `semantic_similarity` | unrelated topics (no reason to combine) | related but distinct (a real combination is plausible) |
| **20 %** | `relationship_sparsity` | densely cross-linked | no recorded cross-cluster edges |
| **15 %** | `research_activity` | dormant area | active area (fertile ground for a new line) |
| **10 %** | `bridge_potential` | nothing could carry a connection | strong structural bridge potential |

Each feature is computed on an absolute 0–1 scale and multiplied by its weight; the
result is published on a 0–100 scale. `POST /api/gaps` returns the raw values
(`score_components[].raw`), the weighted values (`value`) and the weights, and
`GET /api/opportunity-score` returns the same model with plain-language explanations.

### 4.3 Ranking, confidence and trajectory

- **Ranking**: `0.75 × score + 0.25 × query relevance`, with ties inside a 2.5-point
  window broken by relevance. The published `opportunity_score` is never rewritten —
  `rank` is a separate field.
- **Confidence**: derived from the amount and recency of evidence supporting the
  candidate, with a `confidence_reason` string, not a bare number.
- **Trajectory**: `no bridges in corpus` · `persistent gap` · `emerging bridge`
  (bridges appeared ≥ 2024) · `closing gap` (bridge ratio ≥ 0.5) — computed from the
  years of existing bridge papers.
- **Experiment sketch**: every candidate carries a small, concrete first experiment
  (question, method, success signal), generated from the two clusters' methods.

---

## 5. Limitations

1. **Prototype heuristic.** The weights are a judgement call, not the output of a
   validation study. The score ranks candidates within one scope; it is not comparable
   across scopes and is not a probability.
2. **Corpus-bounded.** A "gap" means *relatively under-explored in the analyzed corpus* —
   never "nobody has researched this". The demo corpus is 198 papers; real literature is
   far larger, and absence of evidence here is not evidence of absence.
3. **Score compression.** Demo scopes produce scores in a narrow band (≈ 73–89). Read
   components and ranks, not the digits.
4. **Lexical-baseline embeddings** by default: semantic similarity is a bag-of-words
   proxy unless `NEXUS_EMBEDDINGS_URL` is configured. It is labelled
   `embedding_engine: local:lexical-baseline` in every response.
5. **Rule-based claims.** Extraction targets precision; recall is modest, so the conflict
   list is a starting point, not an audit.
6. **Community names** are generated from member topics/methods and can carry
   field-taxonomy artefacts.
7. **Not validated against expert judgement.** The honest next step is an evaluation
   harness comparing ranked candidates against expert-labelled gap lists — until then,
   treat output as hypotheses to investigate.

---

## 6. Reproducing a result

```bash
python scripts/build_corpus.py --check          # corpus integrity
python -m pytest -q                             # 48 tests, algorithms included
python run.py & ./scripts/smoke.sh              # live API sweep
```

Every response records the engine that produced each number
(`engine`, `embedding_engine`, `claim_engine`, `retrieval_engine`, `expansion_engine`),
so any figure in this document can be traced back to the implementation that computed it.
