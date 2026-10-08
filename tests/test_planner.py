"""Query planner: the Kotlin reference implementation and the Python port (§4.3, §30).

Two planners exist on purpose — the JVM one is the reference, the Python one keeps the
API alive without a JVM — which is only safe if they agree. These tests pin both to the
fixture captured from the Kotlin planner (`tests/data/planner_parity.json`) and check
the properties that matter: whitelisted labels only, every user value bound, cost
bounded, and an honest `source` field.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.config import get_settings
from backend.services.planner import NODE_LABELS, REL_TYPES, plan, python_plan

FIXTURE = Path(__file__).resolve().parent / "data" / "planner_parity.json"
FIELDS = ("ok", "error", "query", "cypher", "params", "explanation", "cost")


@pytest.fixture(scope="module")
def parity() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_is_documented_and_non_trivial(parity):
    assert parity["engine"] == "nexus-kotlin-planner"
    assert len(parity["queries"]) >= 8
    assert "OpenJDK" in parity["verified_with"]
    assert all(row["dsl"] is not None and row["plan"] for row in parity["queries"])


@pytest.mark.parametrize("row", json.loads(FIXTURE.read_text(encoding="utf-8"))["queries"],
                         ids=lambda row: (row["dsl"][:40] or "<empty>"))
def test_python_planner_matches_the_kotlin_reference(row):
    """Every field the Kotlin planner emits must be reproduced by the Python port."""
    theirs = row["plan"]
    ours = python_plan(row["dsl"])
    for field in FIELDS:
        if field not in theirs and field not in ours:
            continue
        assert json.loads(json.dumps(ours.get(field))) == json.loads(json.dumps(theirs.get(field))), (
            f"planner drift on {row['dsl']!r} field {field!r}"
        )


def test_plan_prefers_the_kotlin_sidecar_and_says_so(monkeypatch):
    """With the sidecar reachable the answer comes from the JVM; without it, from Python."""
    monkeypatch.setenv("NEXUS_PLANNER_URL", "http://127.0.0.1:8099")  # nothing listening there
    # get_settings() is lru_cached for the process, so an env change only becomes
    # visible after clearing it — and the cache must be cleared again afterwards so
    # the rest of the suite sees its own environment.
    get_settings.cache_clear()
    try:
        payload = plan('topic:"AI Agents" limit 20')
    finally:
        get_settings.cache_clear()
    assert payload["source"] == "python-fallback"
    assert payload["engine"] == "nexus-python-planner"
    assert payload["ok"] is True
    assert payload["params"] == {"q": "AI Agents", "limit": 20}
    assert payload["cost"]["estimated_nodes_visited"] <= payload["cost"]["budget"]


def test_unknown_labels_and_relationships_are_rejected():
    for dsl in ("type in (Paper, SecretVault)", "rel in (CITES, DELETES)"):
        payload = python_plan(dsl)
        assert payload["ok"] is False
        assert "Allowed:" in payload["error"]


def test_user_text_is_never_interpolated_into_cypher():
    """The DSL is a query language, not a string builder: hostile text stays in params."""
    hostile = 'topic:"AI Agents\\" DETACH DELETE n //" limit 5'
    payload = python_plan(hostile)
    assert payload["ok"] is True
    assert "DELETE" not in payload["cypher"]
    assert "DELETE" in json.dumps(payload["params"])

    # A clause smuggled in after the label list is just free text: it is ranked by the
    # full-text index as a bind parameter and never becomes part of the statement.
    smuggled = python_plan("type in (Paper); MATCH (n) DETACH DELETE n")
    assert "DETACH" not in smuggled["cypher"]
    assert "DETACH" in smuggled["params"]["q"]


def test_cypher_shape_is_valid_for_every_path():
    """No query may reference an unbound variable or emit a pattern without labels."""
    samples = [
        'topic:"AI Agents" type in (Paper, Method) depth<=2 limit 40',
        'author:"Yao" type in (Paper) rel in (CITES, STUDIES) limit 25',
        "year>=2022 year<=2025 type in (Paper) limit 30",
        "rel in (CONTRADICTS) limit 10",
        "memory systems community",
    ]
    for dsl in samples:
        payload = python_plan(dsl)
        cypher = payload["cypher"]
        assert payload["ok"], dsl
        assert "MATCH (nn" not in cypher, "unbound variable regression"
        # filters must appear before RETURN, never after it
        for keyword in ("WHERE", "WITH"):
            if keyword in cypher:
                assert cypher.index("RETURN") > cypher.index(keyword), dsl
        # Free text and field values never appear in the statement — only labels and
        # relationship types do, and those come from the whitelists.
        for value in ("AI Agents", "Yao", "memory systems"):
            if value in dsl:
                assert value not in cypher, f"{value!r} interpolated into Cypher for {dsl!r}"
        for name in payload["query"]["labels"]:
            assert name in NODE_LABELS
        for rel in payload["query"]["relationships"]:
            assert rel in REL_TYPES
        assert cypher.count("(") == cypher.count(")")


def test_plan_route_answers_with_the_engine_that_ran(api):
    payload = api.post("/api/plan", json={"query": 'topic:"AI Agents" type in (Paper) limit 20'}).json()
    assert payload["dsl"] == 'topic:"AI Agents" type in (Paper) limit 20'
    assert payload["engine"] in {"nexus-kotlin-planner", "nexus-python-planner"}
    assert payload["source"] in {"sidecar", "python-fallback"}
    assert "$q" in payload["cypher"] and "AI Agents" not in payload["cypher"]
    assert payload["params"]["q"] == "AI Agents"
    cost = payload["cost"]
    assert cost["strategy"] in {"fulltext -> expand", "label scan -> rank"}
    assert cost["estimated_nodes_visited"] <= cost["budget"]
    assert cost["seeds"] <= 200
    assert "bind parameter" in payload["note"]

    via_get = api.get("/api/plan", params={"q": "rel in (CONTRADICTS) limit 10"}).json()
    assert via_get["cypher"].startswith("MATCH (n)")


def test_plan_route_rejects_unknown_labels(api):
    response = api.post("/api/plan", json={"query": "type in (Paper, SecretVault)"})
    assert response.status_code == 422
    assert "SecretVault" in response.json()["detail"]


def test_services_route_reports_every_sidecar_and_its_fallback(api):
    payload = api.get("/api/services").json()
    names = {entry["name"] for entry in payload["sidecars"]}
    assert names == {"planner", "ingest", "export", "claims"}
    assert payload["total"] == 4
    for entry in payload["sidecars"]:
        assert entry["language"] and entry["fallback"]
        assert isinstance(entry["reachable"], bool)
        assert entry["url"].startswith("http") or entry["configured"] is False
    assert "optional by design" in payload["note"]
    planner = next(entry for entry in payload["sidecars"] if entry["name"] == "planner")
    assert planner["engine"].startswith("nexus-")
    assert "Paper" in NODE_LABELS and "Claim" in NODE_LABELS and "CONTRADICTS" in REL_TYPES
