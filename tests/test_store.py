"""The graph store: construction, provenance, bounded payloads, labelled predictions."""

from __future__ import annotations

import pytest

REQUIRED_EDGE_PROPS = ("provenance",)


def test_store_builds_a_graph_in_the_expected_shape(store):
    stats = store.stats()
    assert stats["nodes"] > 400 and stats["edges"] > 2000
    for label in ("Paper", "Topic", "Method", "Dataset", "Author", "Claim", "Community"):
        assert stats["labels"].get(label, 0) > 0, f"missing {label} nodes"


def test_every_edge_records_its_provenance(store):
    missing = [e for e in store.edges if e.type not in {"SIMILAR_TO", "RELATED_TO", "PREDICTED_LINK"}
               and not e.props.get("provenance")]
    assert not missing, f"{len(missing)} edges without provenance, e.g. {missing[:1]}"


def test_papers_keep_their_source_url(store):
    papers = [n for n in store.nodes.values() if n.label == "Paper"]
    assert papers
    assert all(n.props.get("url", "").startswith("https://arxiv.org/abs/") for n in papers)


def test_search_and_node_detail(store):
    hits = store.search("memory", limit=5)
    assert hits and hits[0]["id"].startswith(("paper:", "topic:", "claim:"))
    detail = store.node_detail(hits[0]["id"])
    assert detail["metrics_explained"], "metrics must come with an explanation"
    assert "neighbours" in detail


def test_subgraph_is_bounded_and_marks_truncation(store):
    sub = store.subgraph(depth=2, max_nodes=40, max_edges=80)
    assert len(sub.nodes) <= 40
    assert len(sub.edges) <= 80


def test_predicted_links_are_labelled_as_predictions(store):
    assert store.predicted_links, "link prediction should produce candidates"
    edges = [e for e in store.edges if e.type == "PREDICTED_LINK"]
    assert edges
    assert all(e.props.get("predicted") for e in edges)


def test_conflicts_carry_reasons_and_never_claim_certainty(store):
    payload = store.conflicts_payload(limit=5)
    for conflict in payload:
        assert conflict["score"] > 0
        assert conflict["reasons"], "a detected tension must explain itself"
        assert "hypothesis" in conflict["label"].lower()


def test_communities_have_profiles(store):
    profiles = store.communities()
    assert profiles and profiles[0]["paper_count"] > 0
    assert profiles[0]["top_topics"]
