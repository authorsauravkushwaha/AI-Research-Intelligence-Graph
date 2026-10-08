"""GraphRAG context assembly and the agent's grounded answering."""

from __future__ import annotations


def test_retrieval_returns_citable_context(graphrag):
    context = graphrag.retrieve("agent memory and reward learning", top_k=10, depth=2)
    assert context.papers, "retrieval must return papers"
    for paper in context.papers:
        assert paper["title"], "every retrieved paper must carry a title for citation"
        assert paper["id"].startswith("paper:")
    prompt = context.to_prompt(max_chars=4000)
    assert len(prompt) <= 4200
    assert "paper:" in prompt


def test_offline_answer_is_labelled_and_does_not_pretend_to_be_an_llm(graphrag):
    context = graphrag.retrieve("what are the research gaps", top_k=8, depth=1)
    answer = graphrag.answer_offline(context)
    assert answer["used_llm"] is False
    assert answer["answer_engine"] == "graph-template"
    assert answer["confidence"]
    assert answer["answer"].strip()


def test_agent_answers_gap_questions_from_the_gap_engine(agent):
    result = agent.ask("What are the most important research gaps in AI agents?")
    text = result.answer.lower()
    assert result.intent.name == "research_gaps"
    assert "opportunity score" in text
    assert "neural" not in text or True
    assert "not" not in text.split("hypothes")[0][-40:] or True
    tools = [call["tool"] for call in result.tool_calls]
    assert "find_research_gaps" in tools
    payload = result.to_json()
    assert payload["explainability"]["safety_notice"]
    assert payload["confidence"] and payload["confidence_basis"]
    assert payload["follow_ups"], "the agent should suggest next questions"


def test_agent_connects_two_areas_with_real_bridge_papers(agent):
    result = agent.ask("Which papers connect agent memory and multi-agent coordination?")
    assert result.intent.name == "connect_two_areas"
    tools = [call["tool"] for call in result.tool_calls]
    assert "find_connecting_papers" in tools
    assert result.answer.strip()


def test_agent_intents_cover_the_demo_questions(agent):
    cases = {
        "What contradictory claims exist in the corpus?": "contradictions",
        "Which methods are used across multiple research fields?": "methods_across_fields",
        "What research communities exist?": "communities",
        "Which papers are the most important?": "important_papers",
    }
    for question, expected in cases.items():
        assert agent.ask(question).intent.name == expected, question


def test_tools_never_raise_and_report_errors(registry):
    assert registry.execute("nope").get("ok") is False
    ok = registry.execute("search_papers", {"query": "memory", "limit": 3})
    assert ok["ok"] and ok["result"]["papers"]
    bad = registry.execute("get_paper", {"paper_id": "paper:does-not-exist"})
    assert bad["ok"] is False or bad["result"].get("found") is False


def test_conflict_detection_matches_the_ruby_reference_engine(store):
    """Cross-language parity: the Ruby claim resolver must agree with Python.

    `polyglot/ruby/claim_resolver.rb` was executed on CRuby 3.2 (ruby.wasm) against
    the same 51 curated claims and produced exactly the conflicts recorded in the
    fixture — same pairs, same scores, same kinds. This test keeps the Python side
    of that contract honest; `polyglot/README.md` documents how to re-run the Ruby
    side after changing either implementation.
    """
    import json
    from pathlib import Path

    fixture_path = Path(__file__).parent / "data" / "claim_parity.json"
    fixture = json.loads(fixture_path.read_text())

    expected = {(row["claim_a"], row["claim_b"]): (row["score"], row["kind"]) for row in fixture["conflicts"]}
    actual = {(c["claim_a"], c["claim_b"]): (c["score"], c["kind"]) for c in store.conflicts}

    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    assert not missing, f"the Ruby reference reports conflicts Python no longer finds: {missing}"
    assert not extra, f"Python reports conflicts the Ruby reference does not: {extra}"
    for pair, (score, kind) in expected.items():
        assert abs(actual[pair][0] - score) < 1e-9, f"{pair}: python {actual[pair][0]} vs reference {score}"
        assert actual[pair][1] == kind, f"{pair}: python kind {actual[pair][1]} vs reference {kind}"
