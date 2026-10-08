"""The corpus itself is a contract: real ids, honest provenance, valid claims."""

from __future__ import annotations

import re

import pytest

ARXIV = re.compile(r"^\d{4}\.\d{4,5}$")


def test_corpus_size_within_specification(corpus):
    papers = corpus["papers"]
    assert 100 <= len(papers) <= 600, "spec asks for a curated 100–500 paper demo corpus"
    assert len(corpus["claims"]) >= 20
    assert len(corpus["citations"]) >= 10


def test_every_id_and_year_is_plausible(corpus):
    for paper in corpus["papers"]:
        assert ARXIV.match(paper["arxiv_id"]), paper["arxiv_id"]
        assert 1991 <= paper["year"] <= 2026
        assert paper["url"] == f"https://arxiv.org/abs/{paper['arxiv_id']}"
        assert paper["summary_source"] in {"editorial-summary", "taxonomy"}
        if paper["summary_source"] == "taxonomy":
            assert paper["summary"] == "", "a record without an editorial note must not carry text"
        assert paper["author_status"] in {"verified", "not-collected"}
        if paper["author_status"] == "verified":
            assert paper["authors"], "verified authorship must actually list authors"
        else:
            assert paper["authors"] == []


def test_no_duplicate_ids(corpus):
    ids = [p["arxiv_id"] for p in corpus["papers"]]
    assert len(ids) == len(set(ids))


def test_claims_point_at_real_papers_with_provenance(corpus):
    ids = {p["arxiv_id"] for p in corpus["papers"]}
    for claim in corpus["claims"]:
        assert claim["paper"] in ids
        assert claim["stance"] in {"reports", "supports", "contradicts"}
        assert claim["source"] == "editorial-distillation"
        assert claim["provenance"]["annotation"]
        assert claim["text"].strip()


def test_lineage_edges_are_inside_the_corpus(corpus):
    ids = {p["arxiv_id"] for p in corpus["papers"]}
    for citation in corpus["citations"]:
        assert citation["source"] in ids and citation["target"] in ids
        assert citation["source"] != citation["target"]


def test_provenance_notice_exists(corpus):
    provenance = corpus["provenance"]
    assert "notice" in provenance
    assert "editorial" in provenance["summaries"]


def test_committed_corpus_is_reproducible_from_the_curator_tables(corpus):
    """`data/demo/corpus.json` must be exactly what `scripts/build_corpus.py` builds.

    This is the guard against the worst failure mode a curated dataset has:
    a record silently disappearing between the curator table and the shipped file.
    """
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("nexus_build_corpus", root / "scripts" / "build_corpus.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    rebuilt = module.build()
    for key in ("papers", "topics", "methods", "datasets", "fields", "citations", "claims"):
        assert rebuilt[key] == corpus[key], f"{key} drifted from scripts/corpus_data.py — rebuild the corpus"
