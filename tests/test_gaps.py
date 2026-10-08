"""The gap engine: score transparency, safety language, non-degenerate pairs."""

from __future__ import annotations

from backend.engine.gaps import SAFETY_NOTICE, SCORE_WEIGHTS, score_model


def test_score_weights_match_the_published_model():
    assert sum(SCORE_WEIGHTS.values()) == 1.0
    assert [SCORE_WEIGHTS["community_separation"], SCORE_WEIGHTS["semantic_similarity"],
            SCORE_WEIGHTS["relationship_sparsity"], SCORE_WEIGHTS["research_activity"],
            SCORE_WEIGHTS["bridge_potential"]] == [0.30, 0.25, 0.20, 0.15, 0.10]
    model = score_model()
    assert len(model["components"]) == 5
    assert model["label"].startswith("prototype heuristic")


def test_find_gaps_returns_scored_candidates_with_evidence(gap_engine):
    payload = gap_engine.find_gaps("AI Agents", 2020, 2026, top_k=5)
    assert payload["scope"]["papers"] > 20
    assert payload["safety_notice"] == SAFETY_NOTICE
    assert payload["score_formula"]
    assert payload["score_distribution"]["candidates_considered"] > 0
    assert payload["opportunities"], "the demo corpus should yield candidates"
    top = payload["opportunities"][0]
    assert 0 < top["opportunity_score"] <= 100
    assert len(top["score_components"]) == 5
    assert all(0 <= c["value"] <= 100 for c in top["score_components"])
    assert top["confidence"] and top["confidence_reason"]
    assert top["hypothesis"] and top["experiment"]
    assert top["trajectory"]["status"] in {"no bridges in corpus", "persistent gap",
                                           "emerging bridge", "closing gap"}
    # Safety language: the hypothesis must stay scoped to the corpus and never
    # assert that nobody has researched the topic.
    notice = (top["hypothesis"] + " " + payload["safety_notice"]).lower()
    # The tool may *deny* the claim that nobody has researched a topic, but the
    # hypothesis itself must never make that claim.
    assert "nobody" not in top["hypothesis"].lower()
    assert "cannot detect work outside the analyzed corpus" in notice
    assert "within the selected research corpus" in top["hypothesis"].lower()


def test_opportunities_are_ranked_by_the_published_score(gap_engine):
    payload = gap_engine.find_gaps("AI Agents", 2020, 2026, top_k=6)
    scores = [o["opportunity_score"] for o in payload["opportunities"]]
    # ordering respects the score except inside the documented tie window
    for a, b in zip(scores, scores[1:]):
        assert a >= b - 2.5 - 1e-6, scores
    assert [o["rank"] for o in payload["opportunities"]] == list(range(1, len(scores) + 1))


def test_cluster_pairs_are_disjoint_and_never_self_paired(gap_engine):
    payload = gap_engine.find_gaps("AI Agents", 2020, 2026, top_k=8)
    for opportunity in payload["opportunities"]:
        a, b = opportunity["cluster_a"], opportunity["cluster_b"]
        assert a["name"] != b["name"]
        assert not (set(a["topics"]) & set(b["topics"])), opportunity["title"]


def test_scope_resolution_is_reported_and_reproducible(gap_engine):
    papers, topics, methods = gap_engine.scope("Agent Memory", None, None, None, 3)
    resolution = gap_engine.last_resolution
    assert resolution["strategy"] in {"scored-topic-resolution", "text-match", "all-papers"}
    assert papers, "topic resolution should find papers for a real topic"
    assert resolution["matched_topics"], "matched topics must be reported back to the user"


def test_unknown_topic_degrades_gracefully(gap_engine):
    payload = gap_engine.find_gaps("Quantum Basket Weaving", None, None, top_k=3)
    assert payload["opportunities"] == [] or payload["scope"]["papers"] > 0
    assert payload["safety_notice"] == SAFETY_NOTICE


def test_every_candidate_pair_is_disjoint_and_not_nested(gap_engine):
    payload = gap_engine.find_gaps(topic="AI Agents", top_k=6)
    for candidate in payload["opportunities"]:
        a = set(candidate["cluster_a"]["topics"])
        b = set(candidate["cluster_b"]["topics"])
        assert not (a & b), f"{candidate['title']} pairs overlapping clusters"
        name_a, name_b = candidate["cluster_a"]["name"].lower(), candidate["cluster_b"]["name"].lower()
        assert name_a not in name_b and name_b not in name_a, f"{candidate['title']} pairs nested concepts"


def test_narrow_scope_reports_why_it_has_no_candidates(gap_engine):
    """An empty result must still be explained — and the filters must be listed."""
    payload = gap_engine.find_gaps(topic="Agent Memory", top_k=5)
    if payload["opportunities"]:
        return  # a separated pair exists; nothing to explain
    assert payload["reason"], "an empty candidate list must carry a reason"
    assert payload["score_distribution"]["candidates_considered"] == 0
    assert payload["filtered_pairs"], "filtered pairs must be reported so the filter is auditable"
    for row in payload["filtered_pairs"]:
        assert row["a"] and row["b"] and row["reason"]


def test_topic_candidates_are_precise_concepts(gap_engine):
    payload = gap_engine.find_gaps(topic="Tool Use", top_k=5, min_papers=3)
    assert payload["opportunities"], "a 26-paper scope should yield candidates"
    for candidate in payload["opportunities"]:
        for side in ("cluster_a", "cluster_b"):
            assert candidate[side]["paper_count"] >= 3
