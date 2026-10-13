"""Neo4j driver, import, and path contracts tested offline with a recording fake."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from backend.graph.neo4j_store import Neo4jError, Neo4jGraphStore
from backend.models.graph import GNode


class FakeResult(list):
    def single(self):
        return self[0] if self else None


class FakeSession:
    def __init__(self, driver, database):
        self.driver = driver
        self.database = database

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def run(self, query, params=None):
        self.driver.queries.append((query, params or {}, self.database))
        return FakeResult()


class FakeDriver:
    def __init__(self, *, verify_error=None):
        self.verify_error = verify_error
        self.verified = 0
        self.closed = False
        self.queries = []
        self.databases = []

    def verify_connectivity(self):
        self.verified += 1
        if self.verify_error:
            raise self.verify_error

    def session(self, *, database):
        self.databases.append(database)
        return FakeSession(self, database)

    def close(self):
        self.closed = True


def neo4j_config(**overrides):
    defaults = {
        "uri": "bolt://neo4j.test:7687",
        "username": "nexus-user",
        "password": "never-echo-this-secret",
        "database": "nexus",
        "timeout": 2.0,
        "query_timeout": 7.0,
        "use_gds": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_connect_verifies_connectivity_and_runs_queries_with_official_query_timeout(monkeypatch):
    import neo4j
    import backend.graph.neo4j_store as neo4j_module

    driver = FakeDriver()
    monkeypatch.setattr(neo4j.GraphDatabase, "driver", lambda *_args, **_kwargs: driver)
    monkeypatch.setattr(neo4j_module, "get_settings", lambda: SimpleNamespace(neo4j=neo4j_config()))

    store = Neo4jGraphStore()
    assert store.connect() is store
    assert store.connection_verified is True
    assert driver.verified == 1

    rows = store.run_cypher("MATCH (n:Paper {id: $id}) RETURN n.id AS id", {"id": "paper:1"})
    assert rows == []
    query, params, database = driver.queries[-1]
    assert isinstance(query, neo4j.Query)
    assert query.text == "MATCH (n:Paper {id: $id}) RETURN n.id AS id"
    assert query.timeout == 7.0
    assert params == {"id": "paper:1"}
    assert database == "nexus"
    assert "never-echo-this-secret" not in str(params)
    store.close()
    assert driver.closed


def test_failed_connect_closes_driver_and_returns_safe_actionable_error(monkeypatch):
    import neo4j
    import backend.graph.neo4j_store as neo4j_module

    driver = FakeDriver(verify_error=RuntimeError("simulated network failure"))
    monkeypatch.setattr(neo4j.GraphDatabase, "driver", lambda *_args, **_kwargs: driver)
    monkeypatch.setattr(neo4j_module, "get_settings", lambda: SimpleNamespace(neo4j=neo4j_config()))
    store = Neo4jGraphStore()

    with pytest.raises(Neo4jError, match="connectivity verification failed") as caught:
        store.connect()
    assert driver.closed
    assert store._driver is None
    assert "NEO4J_URI" in str(caught.value)
    assert "never-echo-this-secret" not in str(caught.value)


def test_neo4j_import_uses_stable_merge_ids_bound_rows_and_field_edges(corpus, monkeypatch):
    store = Neo4jGraphStore(enable_similarity=False)
    store._corpus = corpus
    calls = []
    store.run_cypher = lambda query, params=None: (calls.append((query, params or {})) or [])

    store._load_corpus_into_neo4j()
    first_pass = list(calls)
    store._load_corpus_into_neo4j()
    assert len(calls) == 2 * len(first_pass)

    write_calls = [call for call in first_pass if "MERGE" in call[0]]
    assert write_calls
    assert all("MERGE" in query for query, _params in write_calls)
    assert all("MATCH (p:Paper {id: row.paper})" in query for query, _params in write_calls
               if "row.paper" in query)
    paper_query, paper_params = first_pass[0]
    assert "MERGE (p:Paper {id: row.id})" in paper_query
    assert paper_params["rows"][0]["id"].startswith("paper:")
    assert corpus["papers"][0]["title"] not in paper_query  # content is data, never Cypher text

    studies_query, studies_params = next(
        (query, params) for query, params in first_pass if "MERGE (p)-[r:STUDIES]->(n)" in query
    )
    assert "topic_kind" in studies_query
    field_rows = [row for row in studies_params["rows"] if row["topic_kind"] == "field"]
    assert field_rows and all(row["weight"] == 0.6 for row in field_rows)
    assert all(row["provenance"] == "paper.field taxonomy" for row in field_rows)

    claim_query, claim_params = next(
        (query, params) for query, params in first_pass if "MERGE (c:Claim {id: row.id})" in query
    )
    assert claim_query
    claim_row = claim_params["rows"][0]
    assert claim_row["id"].startswith("claim:")
    claim_provenance = json.loads(claim_row["provenance"])
    assert claim_provenance["paper_id"].startswith("paper:")
    assert claim_provenance["url"].startswith("https://arxiv.org/abs/")
    assert first_pass == calls[len(first_pass):]  # repeated import uses the same stable rows


def test_hydration_decodes_claim_provenance_json_for_mapping_consumers():
    store = Neo4jGraphStore(enable_similarity=False)
    store._driver = object()
    provenance = {"paper_id": "paper:2310.08560", "url": "https://arxiv.org/abs/2310.08560"}

    def fake_run(query, _params=None):
        if "RETURN n.id AS id" in query:
            return [{
                "id": "claim:2310.08560:0", "labels": ["Claim"], "name": "A claim",
                "props": {"provenance": json.dumps(provenance), "source": "corpus"},
            }]
        if "RETURN a.id AS src" in query:
            return []
        raise AssertionError("unexpected hydration query")

    store.run_cypher = fake_run
    store._hydrate()
    claim = store.nodes["claim:2310.08560:0"]
    assert claim.props["provenance"] == provenance
    assert claim.props["provenance"].get("paper_id") == "paper:2310.08560"


def test_neo4j_trace_path_uses_bounded_parameterized_cypher(monkeypatch):
    store = Neo4jGraphStore(enable_similarity=False)
    store._driver = object()
    store.nodes = {
        "topic:source": GNode("topic:source", "Topic", "Source topic"),
        "topic:target": GNode("topic:target", "Topic", "Target topic"),
    }
    calls = []

    def fake_run(query, params=None):
        calls.append((query, params))
        return [{
            "path_nodes": [
                {"id": "topic:source", "type": "Topic", "label": "Source topic"},
                {"id": "topic:target", "type": "Topic", "label": "Target topic"},
            ],
            "path_edges": [{
                "source": "topic:source", "target": "topic:target", "type": "STUDIES",
                "properties": {"provenance": "curated taxonomy", "weight": 1.0},
            }],
        }]

    store.run_cypher = fake_run
    path = store.trace_path("topic:source", "topic:target", max_hops=99, allowed_types=["STUDIES"])
    assert path and path["source_engine"] == "neo4j"
    assert path["hops"][0]["provenance"] == "curated taxonomy"
    query, params = calls[0]
    assert "shortestPath" in query and "*1..4" in query
    assert "$source_id" in query and "$target_id" in query and "$max_hops" in query and "$allowed_types" in query
    assert "topic:source" not in query and "topic:target" not in query
    assert params == {
        "source_id": "topic:source", "target_id": "topic:target", "max_hops": 4,
        "allowed_types": ["STUDIES"],
    }


def test_graphrag_exposes_the_database_path_engine_and_evidence_provenance():
    from backend.rag.graphrag import GraphRAG

    calls = []

    class PathStore:
        engine_name = "neo4j"
        nodes = {}

        def trace_path(self, source, target, *, max_hops, allowed_types):
            calls.append((source, target, max_hops, allowed_types))
            return {
                "nodes": [
                    {"id": "topic:a", "type": "Topic", "label": "Area A"},
                    {"id": "paper:1", "type": "Paper", "label": "Evidence paper"},
                    {"id": "topic:b", "type": "Topic", "label": "Area B"},
                ],
                "hops": [
                    {"from": "topic:a", "to": "paper:1", "type": "STUDIES", "provenance": "corpus taxonomy", "direction": "incoming"},
                    {"from": "paper:1", "to": "topic:b", "type": "STUDIES", "provenance": "corpus taxonomy", "direction": "outgoing"},
                ],
                "hops_count": 2,
                "source_engine": "neo4j",
                "method": "parameterized Neo4j Cypher shortest path (maximum 4 hops)",
            }

    path = GraphRAG(PathStore()).paths_between("topic:a", "topic:b", max_hops=20)
    assert calls and calls[0][2] == 4
    assert set(calls[0][3]) == {"STUDIES", "USES_METHOD", "USES_DATASET", "CITES", "AUTHORED", "MAKES_CLAIM", "AFFILIATED_WITH"}
    assert path["nodes"] == ["topic:a", "paper:1", "topic:b"]
    assert path["source_engine"] == "neo4j"
    assert path["node_labels"][1]["label"] == "Evidence paper"
    assert path["hops"][0]["provenance"] == "corpus taxonomy"
    assert "Area A" in path["hops"][0]["label"]


def test_schema_failures_are_reported_instead_of_silently_hidden():
    store = Neo4jGraphStore(enable_similarity=False)

    def fail(_query, _params=None):
        raise RuntimeError("permission denied")

    store.run_cypher = fail
    store.ensure_schema()
    assert store.schema_warnings
    assert any("paper_id" in warning for warning in store.schema_warnings)
    assert all("permission denied" not in warning for warning in store.schema_warnings)


def test_factory_closes_neo4j_store_after_startup_failure(monkeypatch):
    import backend.graph.factory as factory
    import backend.graph.neo4j_store as neo4j_module

    closed = []

    class FailingStore:
        engine_name = "neo4j"

        def __init__(self):
            pass

        def load(self):
            raise RuntimeError("simulated import failure")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(neo4j_module, "Neo4jGraphStore", FailingStore)
    monkeypatch.setattr(factory, "get_settings", lambda: SimpleNamespace(
        neo4j=SimpleNamespace(configured=True, enabled=True),
    ))
    state = factory.build_store()

    assert closed == [True]
    assert state.backend == "in-process"
    assert state.degraded is True
    assert "Neo4j unavailable" in state.reason
