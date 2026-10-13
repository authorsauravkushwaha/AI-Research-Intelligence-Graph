"""Question-to-evidence investigation workflow; all evidence must be traceable."""

from __future__ import annotations


def test_investigator_reranks_papers_and_explains_a_scoped_candidate(store, gap_engine, graphrag):
    from backend.engine.investigator import ResearchInvestigator

    investigator = ResearchInvestigator(store, gap_engine, graphrag)
    result = investigator.investigate(
        "How can agent memory improve coordination in multi-agent systems?",
        scope_topic="AI Agents",
        top_k=5,
    )

    assert result["scope"]["selected_topic"] == "AI Agents"
    assert result["scope"]["papers"] == 101
    assert result["coverage"]["papers_with_question_term_match"] < result["scope"]["papers"]
    assert set(result["coverage"]["question_terms"]) == {"coordination", "memory"}
    assert result["retrieval"]["engine"] == "local:lexical-baseline"
    assert result["backend"]["graph_store"] == "in-process"
    assert "in-process graph projection" in result["backend"]["graph_computation_scope"].lower()
    assert "independently reranked" in result["retrieval"]["note"]
    assert result["relevant_papers"]
    assert result["relevant_papers"][0]["matched_terms"]
    assert not {"agent", "agents", "system", "multi-agent"} & {
        term.casefold() for paper in result["relevant_papers"] for term in paper["matched_terms"]
    }
    assert result["relevant_papers"][0]["url"].startswith("https://arxiv.org/abs/")

    candidate = result["opportunities"][0]
    assert candidate["title"] == "Agent Memory × Coordination"
    assert candidate["labels"]["status"] == "candidate research opportunity"
    assert candidate["opportunity_score"] <= 100  # heuristic score, not a probability
    assert candidate["question_relevance_note"]
    assert candidate["evidence"]
    assert all(paper["url"].startswith("https://arxiv.org/abs/") for paper in candidate["evidence"])
    assert candidate["graph_paths"]
    path = candidate["graph_paths"][0]
    assert path["source_engine"] == "in-process"
    assert path["hops"]
    assert all(hop["provenance"] for hop in path["hops"])
    assert all(hop["type"] not in {"RELATED_TO", "SIMILAR_TO", "PREDICTED_LINK", "CONTRADICTS"}
               for hop in path["hops"])
    assert candidate["experiment_proposal"]["falsification_criterion"]
    assert candidate["experiment_proposal"]["unknowns"]
    assert "hypothes" in candidate["counterevidence_status"].lower()
    assert result["limitations"]


def test_investigator_infers_scope_only_from_a_clear_question_topic_match(store, gap_engine, graphrag):
    from backend.engine.investigator import ResearchInvestigator

    result = ResearchInvestigator(store, gap_engine, graphrag).investigate(
        "How can agent memory improve coordination in multi-agent systems?"
    )
    assert result["scope"]["selected_topic"] == "Multi-Agent Systems"
    assert result["scope"]["selected_by"] == "question-topic match"


def test_candidate_filter_does_not_surface_a_distant_pair(store, gap_engine, graphrag):
    from backend.engine.investigator import ResearchInvestigator

    result = ResearchInvestigator(store, gap_engine, graphrag).investigate(
        "Which planning strategies could improve API tool use in AI agents?",
        scope_topic="Tool Use",
    )
    titles = [candidate["title"] for candidate in result["opportunities"]]
    assert titles == ["Planning × API Calling"]
    assert result["opportunities"][0]["question_matched_terms"]
    assert {"api", "planning"} <= set(result["opportunities"][0]["question_matched_terms"])


def test_sparse_rag_scope_reports_relevant_sources_without_unrelated_gaps(store, gap_engine, graphrag):
    from backend.engine.investigator import ResearchInvestigator

    result = ResearchInvestigator(store, gap_engine, graphrag).investigate(
        "How might graph retrieval support factual reliability in retrieval-augmented generation?",
        scope_topic="Retrieval-Augmented Generation",
    )
    assert result["scope"]["papers"] == 5
    assert not result["opportunities"]
    assert result["empty_reason"]
    assert result["relevant_papers"]
    assert any("Retrieval-Augmented" in paper["title"] for paper in result["relevant_papers"])
    assert result["coverage"]["corpus_papers"] == 204
    assert any("does not establish" in limitation.lower() for limitation in result["limitations"])


def test_investigate_api_validates_question_and_returns_the_workflow(api):
    response = api.post("/api/investigate", json={
        "question": "How can agent memory improve coordination in multi-agent systems?",
        "scope_topic": "AI Agents",
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["generated_at"]
    assert payload["status"]["engine"]
    assert payload["backend"]["graph_store"]
    assert payload["coverage"]["scope_papers"] > 0
    assert payload["opportunities"] and payload["relevant_papers"]
    assert payload["evidence_paths"]

    invalid = api.post("/api/investigate", json={"question": "short"})
    assert invalid.status_code == 422
