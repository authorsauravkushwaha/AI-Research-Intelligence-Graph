"""Shared fixtures. Everything runs offline: no Neo4j, no LLM, no network."""

from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agents.agent import ResearchAgent  # noqa: E402
from backend.agents.tools import build_registry  # noqa: E402
from backend.engine.gaps import GapEngine  # noqa: E402
from backend.rag.graphrag import GraphRAG  # noqa: E402
from backend.store.memory_store import MemoryGraphStore  # noqa: E402


@pytest.fixture(scope="session")
def store() -> MemoryGraphStore:
    graph = MemoryGraphStore()
    graph.load()
    return graph


@pytest.fixture(scope="session")
def gap_engine(store):
    return GapEngine(store)


@pytest.fixture(scope="session")
def graphrag(store):
    return GraphRAG(store)


@pytest.fixture(scope="session")
def registry(store, gap_engine, graphrag):
    return build_registry(store, gap_engine, graphrag)


@pytest.fixture(scope="session")
def agent(store, gap_engine, registry, graphrag):
    return ResearchAgent(store, gap_engine, registry, graphrag)


@pytest.fixture(scope="session")
def corpus() -> dict:
    return json.loads((ROOT / "data" / "demo" / "corpus.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def api():
    from fastapi.testclient import TestClient

    from backend.api.app import create_app

    with TestClient(create_app()) as client:
        yield client
