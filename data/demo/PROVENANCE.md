# Corpus provenance audit

`data/demo/corpus.json` is the dataset every number in NEXUS is computed from. This file
records exactly what is real, what is editorial, and how to verify each part.

**Summary: the arXiv identifiers, titles and years are real public records. The summaries,
topic/method/dataset tags, citation lineage and claims are editorial annotations written
for this prototype. Nothing here is a quotation, and no authorship is claimed without a
verified source.** Verify every record at `https://arxiv.org/abs/<arxiv_id>`.

---

## 1. Record counts

| Property | Value |
| --- | --- |
| Papers | 204 |
| Year range | 2013 – 2025 |
| Topics / methods / datasets | 75 topics · 61 methods · 68 datasets (editorial labels) |
| Curated citation edges | 60 |
| Claim records | 51 (including 7 curated tension pairs) |
| Authorship verified | 37 records |
| Authorship not collected | 167 records (`author_status: "not-collected"`) |
| Records with an editorial scope note | 70 |
| Records with taxonomy tags only | 134 (`summary_source: "taxonomy"`) |

## 2. Where each field comes from

| Field | Source | Status |
| --- | --- | --- |
| `arxiv_id` | Public arXiv identifier | **Real** — syntax-validated (`YYMM.NNNNN`), presence in the arXiv listing index is the collection criterion |
| `title` | arXiv listing title | **Real** |
| `year` | Derived from the id prefix (`YYMM`) | **Derived from a real id** |
| `url` | `https://arxiv.org/abs/<id>` | **Real** |
| `authors` | arXiv abstract page or paper PDF, read at collection time | **Real but partial** — present on 37 records only |
| `author_status` | `verified` when authors were read from the source, `not-collected` otherwise | **Editorial flag** |
| `field` | Editorial taxonomy (one per record) | **Editorial** |
| `topics` / `methods` / `datasets` | Editorial tagging rules, applied to titles (`scripts/corpus_data.py`) | **Editorial** |
| `summary` | Editorial scope note written for this prototype | **Editorial, not a quotation** |
| `summary_source` | `editorial-summary` or `taxonomy` | **Editorial flag** |
| `citations` | Curated lineage subset, checked against the papers' own related-work sections | **Curated subset** of real lineage — not a citation graph |
| `claims` | Editorial distillation of what each paper reports, with polarity/direction/assertiveness fields | **Editorial interpretation** |

## 3. Verification method per tier

* **`classic` (34 records)** — long-standing, widely cited works. Authorship was read from
  the arXiv abstract page or the paper's author list. These records are the backbone of
  the citation lineage.
* **`verified` (6 records)** — 2025 papers (memory systems) whose titles were verified
  against the arXiv abstract page during this build; authorship recorded where the page
  listed it.
* **`pool` (164 records)** — titles harvested from the community index archived in
  `data/demo/sources/awesome-agent-papers.md` (170 unique arXiv ids with titles), which
  itself links to arXiv. These records carry tags and a year, and explicitly **no
  summary text and no authors**.

Two ids were deliberately dropped from the harvested index: `2503.21460` (the index entry
is a placeholder sentence rather than a paper title) and `2505.00753` (kept out of the
curated tables to avoid presenting an unverified title).

## 4. Claim records: how the tension pairs were built

`claimed` records are editorial sentences that state what a paper reports, with four
machine-readable annotations: `polarity`, `direction`, `assertive` and a list of curated
`tension_with` targets. The seven curated tension pairs each state one finding and the
opposing finding **with an explicit negation**, sharing vocabulary, so the resolver's
conservative opposition gate can evaluate them honestly:

| Finding | Opposing finding |
| --- | --- |
| More sampled agents improve performance (2402.05120) | More agents do not improve performance when coordination fails (2503.13657) |
| Self-organised agentic memory improves long-horizon performance (2502.12110) | Uncurated memory accumulation degrades long-horizon performance (2505.16067) |
| Self-supervised tool training improves tool use (2302.04761) | Query rewriting reaches unseen APIs without such training (2408.01875) |
| Adversarial propagation amplifies exponentially (2402.08567) | Topology-guided treatment reduces propagation (2502.11127) |
| Agent memory leaks private content through retrieval (2502.13172) | Pre-execution memory checking prevents those leaks (2402.01586) |
| Interleaved reasoning and acting improves long-horizon success (2210.03629) | Frameworks do not transfer chat competence to long tasks (2308.03688) |
| Learned memory operations improve later answers (2508.19828) | Uncurated accumulation makes stored experience unreliable (2505.16067) |

What the pipeline produces from these is a **claim-level tension with the evidence
attached** — a candidate for a human to check. It is never presented as "these authors
disagree", and the demo copy says so on every surface.

## 5. What was deliberately *not* included

* No scraped abstracts or quotations — copyright and accuracy risk, and summaries here are
  explicitly editorial.
* No citation counts, h-index or impact factors — they would invite rankings the corpus
  cannot support at this size.
* No invented author names: uncollected authorship stays empty and is labelled.
* No synthetic papers: every id resolves to a real arXiv record.

## 6. Reproducing and validating

```bash
python scripts/build_corpus.py --check     # validates ids, references, floors; writes nothing
python scripts/build_corpus.py             # rebuilds data/demo/corpus.json
python -m pytest tests/test_corpus.py      # asserts the shipped file matches the curator tables
```

`scripts/build_corpus.py` fails (non-zero exit) if a record is malformed, duplicated, or if
any citation/claim points outside the corpus, and it refuses to write a corpus smaller
than the 100-paper floor. `tests/test_corpus.py::test_committed_corpus_is_reproducible_from_the_curator_tables`
re-derives the corpus from `scripts/corpus_data.py` and asserts it is byte-for-byte the
same content — so no record can quietly vanish between the curator table and the shipped
file.

Harvest artefacts kept in `data/demo/sources/`:

* `verified_index.json` — the 170 harvested id/title pairs (input to the tagger).
* `awesome-agent-papers.md` — the archived community index the harvest came from.
