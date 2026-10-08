#!/usr/bin/env python3
"""Build and validate the NEXUS demonstration corpus.

    python scripts/build_corpus.py          # validate, then write data/demo/corpus.json
    python scripts/build_corpus.py --check  # validate only (CI)

The curator's tables live in ``scripts/corpus_data.py`` next to this file; this script
normalises them into the record shape the ingestion pipeline consumes and refuses to
write a corpus that contains a broken reference.

Provenance rules enforced here:

* ``arxiv_id`` must be syntactically a real arXiv identifier (``YYMM.NNNNN``);
* ids and titles are editorial selections of real public records;
* ``authors`` are carried only when authorship was verified (``author_status``);
* summaries are labelled ``editorial-summary`` or ``taxonomy`` — never quoted text;
* every curated citation and claim must point at a record in the corpus.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import corpus_data as data  # noqa: E402  (imported after sys.path)

ARXIV_ID = re.compile(r"^\d{4}\.\d{4,5}$")
OUT = ROOT / "data" / "demo" / "corpus.json"
MIN_PAPERS = 100  # the demo corpus must never quietly shrink


def slug(text: str) -> str:
    """Stable, human-readable identifier fragment: 'Agent Memory' -> 'agent-memory'."""
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.strip().lower())
    return cleaned.strip("-")[:64] or "unknown"


def year_of(arxiv_id: str) -> int:
    prefix = int(arxiv_id.split(".")[0])
    return 2000 + prefix // 100


def collect() -> tuple[list[dict], list[str]]:
    problems: list[str] = []
    papers: dict[str, dict] = {}

    def add(arxiv_id, title, field, topics, methods, datasets, authors, tier, summary_source):
        if not ARXIV_ID.match(arxiv_id):
            problems.append(f"malformed arXiv id {arxiv_id!r} ({title[:40]!r})")
            return
        if arxiv_id in papers:
            problems.append(f"duplicate record for {arxiv_id}")
            return
        authors = [a for a in authors if a]
        papers[arxiv_id] = {
            "arxiv_id": arxiv_id,
            "title": title.strip(),
            "year": year_of(arxiv_id),
            "url": f"https://arxiv.org/abs/{arxiv_id}",
            "field": field,
            "topics": [t for t in dict.fromkeys(topics)],
            "methods": [m for m in dict.fromkeys(methods)],
            "datasets": [d for d in dict.fromkeys(datasets)],
            "authors": authors,
            "author_status": "verified" if authors else "not-collected",
            "summary": data.SUMMARIES.get(arxiv_id, ""),
            "summary_source": "editorial-summary" if data.SUMMARIES.get(arxiv_id) else "taxonomy",
            "corpus_tier": tier,
        }

    for (aid, title, field, topics, methods, datasets, authors) in data.CLASSICS:
        add(aid, title, field, topics, methods, datasets, authors, "classic", "editorial-summary")
    for (aid, title, field, topics, methods, datasets, authors) in data.VERIFIED:
        add(aid, title, field, topics, methods, datasets, authors, "verified", "editorial-summary")
    for (aid, title, field, topics, methods, datasets) in data.POOL:
        add(aid, title, field, topics, methods, datasets, [], "pool", "taxonomy")

    # summaries that belong to no record are curator drift, not a crash
    for key in data.SUMMARIES:
        if key not in papers:
            problems.append(f"summary for unknown record {key}")

    citations = []
    for source, target in data.LINEAGE:
        if source not in papers or target not in papers:
            problems.append(f"citation {source} -> {target} points outside the corpus")
            continue
        if source == target:
            problems.append(f"self-citation on {source}")
            continue
        citations.append(
            {
                "source": source,
                "target": target,
                "relation": "CITES",
                "citation_source": "curated-lineage",
                "provenance": "curated-lineage",
                "note": "high-confidence lineage subset; verify at source",
            }
        )

    claims = []
    per_paper: dict[str, int] = {}
    for record in data.CLAIMS:
        aid, text, polarity, direction, assertive, opposes = record
        if aid not in papers:
            problems.append(f"claim for unknown record {aid}")
            continue
        for target in opposes:
            if target not in papers:
                problems.append(f"claim tension {aid} -> {target} points outside the corpus")
        if not text.strip():
            problems.append(f"empty claim text for {aid}")
            continue
        per_paper[aid] = per_paper.get(aid, 0) + 1
        claims.append(
            {
                # the id shape the claim resolver and the graph builder expect
                "id": f"{aid}:{per_paper[aid]}",
                "text": text.strip(),
                "paper": aid,
                "stance": "reports",
                "source": "editorial-distillation",
                "confidence": "editorial",
                "polarity": polarity,
                "direction": direction,
                "assertive": bool(assertive),
                "tension_with": list(opposes),
                "provenance": {
                    "annotation": "editorial distillation of the paper's reported finding",
                    "polarity": polarity,
                    "direction": direction,
                    "assertive": bool(assertive),
                    "curated_tension_with": list(opposes),
                    "note": "Not a quotation. Read the paper at its arXiv URL before relying on it.",
                },
            }
        )

    if len(papers) < MIN_PAPERS:
        problems.append(f"corpus has {len(papers)} records, below the {MIN_PAPERS} floor")

    ordered = sorted(papers.values(), key=lambda p: (-p["year"], p["arxiv_id"]))
    return ordered + [{"_citations": citations, "_claims": claims}], problems  # type: ignore[list-item]


def build() -> dict:
    records, problems = collect()
    if problems:
        for problem in problems:
            print(f"  ! {problem}", file=sys.stderr)
        raise SystemExit(f"corpus validation failed with {len(problems)} problem(s)")

    bundle = records.pop()
    citations = bundle["_citations"]
    claims = bundle["_claims"]
    papers = records

    def dictionary(key: str) -> list[dict]:
        counts: dict[str, int] = {}
        display: dict[str, str] = {}
        for paper in papers:
            for value in paper[key]:
                s = slug(value)
                counts[s] = counts.get(s, 0) + 1
                display.setdefault(s, value)
        return [
            {"id": s, "name": display[s], "paper_count": counts[s]}
            for s in sorted(counts, key=lambda k: (-counts[k], k))
        ]

    fields: dict[str, int] = {}
    for paper in papers:
        fields[paper["field"]] = fields.get(paper["field"], 0) + 1

    corpus = {
        "title": "NEXUS demonstration corpus",
        "corpus_version": "1.0.0",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provenance": {
            "ids_and_titles": "real arXiv records, collected from public arXiv listings and community indexes",
            "summaries": "editorial scope notes written for this prototype; never quotations",
            "authors": "carried only when verified, otherwise author_status='not-collected'",
            "citations": "curated high-confidence lineage subset, not a full citation graph",
            "claims": "editorial distillations of reported findings, labelled per record",
            "notice": "Demonstration corpus: real paper metadata with editorial annotations. "
                      "Verify every record at its arXiv URL before citing it.",
        },
        "papers": papers,
        "topics": dictionary("topics"),
        "methods": dictionary("methods"),
        "datasets": dictionary("datasets"),
        "fields": [{"name": k, "paper_count": v} for k, v in sorted(fields.items(), key=lambda kv: -kv[1])],
        "citations": citations,
        "claims": claims,
    }
    return corpus


def write(corpus: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(corpus, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def report(corpus: dict) -> None:
    papers = corpus["papers"]
    years = [p["year"] for p in papers]
    tiers: dict[str, int] = {}
    for paper in papers:
        tiers[paper["corpus_tier"]] = tiers.get(paper["corpus_tier"], 0) + 1
    with_authors = sum(1 for p in papers if p["author_status"] == "verified")
    print(f"  · {len(papers)} papers, {min(years)}–{max(years)}")
    print(f"  · tiers: " + ", ".join(f"{k}={v}" for k, v in sorted(tiers.items())))
    print(f"  · authorship verified on {with_authors} records, {len(papers) - with_authors} marked not-collected")
    print(f"  · {len(corpus['topics'])} topics · {len(corpus['methods'])} methods · {len(corpus['datasets'])} datasets")
    print(f"  · {len(corpus['citations'])} curated citations · {len(corpus['claims'])} claims")
    print(f"  · written to {OUT.relative_to(ROOT)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and validate the demo corpus.")
    parser.add_argument("--check", action="store_true", help="validate only, do not write")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    corpus = build()
    if not args.check:
        write(corpus)
    if not args.quiet:
        print("NEXUS corpus " + ("valid" if args.check else "built"))
        report(corpus)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
