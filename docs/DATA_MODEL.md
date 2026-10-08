# Data model

One heterogeneous property graph, expressed once as typed Python dataclasses
(`backend/models/graph.py`) and once as Cypher constraints/indexes
(`cypher/queries/06_maintenance.cypher`). The two engines hydrate the same shape, which is
what makes them interchangeable.

---

## Node labels

| Label | Id pattern | Core properties | Provenance |
| --- | --- | --- | --- |
| `Paper` | `paper:<arxiv_id>` | `title`, `year`, `field`, `url`, `summary`, `summary_source`, `author_status`, `corpus_tier` | arXiv id/title real; summary editorial or title-derived |
| `Topic` | `topic:<slug>` | `name`, `paper_count` | derived from corpus labels |
| `Method` | `method:<slug>` | `name`, `paper_count` | derived from corpus labels |
| `Dataset` | `dataset:<slug>` | `name`, `paper_count` | derived from corpus labels |
| `Author` | `author:<slug>` | `name`, `author_status` | only when verified (`not-collected` otherwise) |
| `Claim` | `claim:<paper_id>:<n>` | `text`, `polarity`, `direction`, `subject`, `assertive`, `tokens` | extracted from the paper record, labelled `source` |
| `Community` | `community:c<idx>` | `name`, `size`, `top_topics`, `top_methods`, `top_papers`, `avg_pagerank` | Louvain (algorithmic) |

Computed metrics live on the node when applicable: `pagerank`, `betweenness`, `degree`,
`community_index`, `community_name`.

Node ids are **readable slugs with hyphens** (`topic:agent-memory`). Unlike Neo4j's
internal ids they are stable across reloads and can be typed by a human.

---

## Relationship types

The vocabulary is closed and validated (`REL_TYPES`, 14 entries) so that a typo in a
loader becomes a startup error instead of an invisible edge.

| Relationship | Direction | Meaning | Kind |
| --- | --- | --- | --- |
| `AUTHORED` | Author → Paper | authorship | fact (when collected) |
| `CITES` | Paper → Paper | curated citation lineage | fact (curated subset) |
| `STUDIES` | Paper → Topic | the paper works on this topic | corpus label |
| `USES_METHOD` | Paper → Method | the paper applies this method | corpus label |
| `USES_DATASET` | Paper → Dataset | the paper evaluates on this dataset | corpus label |
| `AFFILIATED_WITH` | Author → Institution | affiliation | fact (when collected) |
| `MAKES_CLAIM` | Paper → Claim | the paper asserts this claim | extraction |
| `SUPPORTS` | Claim → Claim | compatible claims | inference |
| `CONTRADICTS` | Claim → Claim | explicit opposition, scored | inference |
| `BELONGS_TO` | Paper/Claim → Community | community membership | algorithm (Louvain) |
| `RELATED_TO` | any ↔ any | curated thematic relation | curation |
| `MEASURED_BY` | Topic/Method → Metric | analytic measurement | algorithm |
| `SIMILAR_TO` | Paper ↔ Paper | embedding similarity above threshold | algorithm (similarity) |
| `PREDICTED_LINK` | Paper ↔ Paper | link-prediction hypothesis | algorithm (never a fact) |

`ASSOCIATIVE_RELS` (`STUDIES`, `USES_METHOD`, `USES_DATASET`, `MEASURED_BY`,
`BELONGS_TO`, `RELATED_TO`) mark the edges that carry semantic meaning for gap analysis,
as opposed to purely structural links.

Every edge carries `weight`, `engine` (which implementation produced it) and, where
relevant, `provenance`/`source` describing where it came from.

---

## TypeScript/JS shape on the wire

The API emits **flat** node objects so the 3D view needs no client-side transformation:

```json
{
  "id": "paper:2310.08560",
  "label": "MemGPT: Towards LLMs as Operating Systems",
  "type": "Paper",
  "color": "#4cc9f0",
  "pagerank": 0.0031, "betweenness": 0.0412, "degree": 19,
  "year": 2023, "url": "https://arxiv.org/abs/2310.08560"
}
```

Edges: `{ "id": "paper:a|CITES|paper:b", "source": "paper:a", "target": "paper:b",
"type": "CITES", "weight": 1.0, "predicted": false }`.

Graph responses additionally carry `legend`, `focus`, `truncated` and `notes` so the UI
can render trust information next to the picture.

---

## Constraints and indexes (Neo4j)

`cypher/queries/06_maintenance.cypher` creates:

```cypher
CREATE CONSTRAINT paper_id IF NOT EXISTS FOR (p:Paper)  REQUIRE p.id IS UNIQUE;
CREATE CONSTRAINT topic_id IF NOT EXISTS FOR (t:Topic)  REQUIRE t.id IS UNIQUE;
-- … one uniqueness constraint per label …
CREATE FULLTEXT INDEX nexus_search IF NOT EXISTS
FOR (n:Paper|Topic|Method|Dataset|Author) ON EACH [n.label, n.name, n.summary];
```

The full-text index backs `/api/search` on the Neo4j engine and the local lexical scan
backs it on the embedded engine, so search behaves the same either way.

---

## Corpus record format

```jsonc
{
  "arxiv_id": "2310.08560",
  "title": "MemGPT: Towards LLMs as Operating Systems",
  "year": 2023,                       // derived from the arXiv id prefix
  "url": "https://arxiv.org/abs/2310.08560",
  "field": "Agent Memory",
  "authors": ["Charles Packer", "…"],
  "author_status": "verified",        // or "not-collected"
  "summary": "Editorial scope note …",
  "summary_source": "editorial",      // or "title-derived"
  "topics": ["agent-memory"], "methods": ["…"], "datasets": ["…"],
  "corpus_tier": "classic"            // or "pool"
}
```

Validated on every build (`scripts/build_corpus.py --check`): arXiv id syntax, duplicate
detection, citation targets and claim targets must exist, minimum corpus size enforced.
Provenance policy and per-record audit: [../data/demo/PROVENANCE.md](../data/demo/PROVENANCE.md).
