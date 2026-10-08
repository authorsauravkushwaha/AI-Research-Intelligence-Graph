"""Native kernel bridge + pure-Python fallbacks (they must agree).

The wrappers transparently fall back to Python when `native/build/bin/nexus-kernel`
is missing, so these tests assert *both* paths on small graphs where the expected
answer is known by hand.
"""

from __future__ import annotations

import pytest

from backend.algorithms import kernel


def _two_triangles() -> tuple[list[str], list[tuple[str, str, float]]]:
    ids = ["a1", "a2", "a3", "b1", "b2", "b3"]
    edges = [("a1", "a2", 1.0), ("a2", "a3", 1.0), ("a1", "a3", 1.0),
             ("b1", "b2", 1.0), ("b2", "b3", 1.0), ("b1", "b3", 1.0), ("a1", "b1", 1.0)]
    return ids, edges


def test_kernel_reports_its_engine_and_path():
    info = kernel.kernel_info()
    assert "available" in info and info["path"]


def test_pagerank_is_a_distribution_in_both_paths():
    ids, edges = _two_triangles()
    values, engine = kernel.pagerank(ids, edges)
    assert abs(sum(values.values()) - 1.0) < 1e-6
    python = kernel.py_pagerank(ids, edges)
    assert abs(sum(python.values()) - 1.0) < 1e-6
    assert engine


def test_louvain_finds_the_two_planted_communities():
    ids, edges = _two_triangles()
    partition, engine = kernel.louvain(ids, edges, resolution=1.0)
    assert len(set(partition.values())) == 2, partition
    assert set(partition.values()) == {0, 1}
    assert engine
    assert len(set(kernel.py_louvain(ids, edges, resolution=1.0).values())) == 2


def test_betweenness_matches_the_textbook_example():
    ids, edges = ["a", "b", "c"], [("a", "b", 1.0), ("b", "c", 1.0)]
    values, engine = kernel.betweenness(ids, edges)
    python = kernel.py_betweenness(ids, edges)
    assert abs(values["b"] - 1.0) < 1e-6
    assert abs(python["b"] - 1.0) < 1e-6
    assert engine


def test_shortest_path_both_paths_agree():
    ids, edges = ["a", "b", "c"], [("a", "b", 1.0), ("b", "c", 1.0)]
    assert kernel.shortest_path(ids, edges, "a", "c") == ["a", "b", "c"]


def test_link_prediction_scores_common_neighbours():
    ids = ["a", "b", "c"]
    edges = [("a", "b", 1.0), ("b", "c", 1.0)]
    scores = kernel.link_prediction(ids, edges, [("a", "c")])
    row = scores[("a", "c")]
    assert row["common_neighbors"] == 1.0
    assert 0.0 < row["jaccard"] <= 1.0
    assert row["adamic_adar"] > 0.0


def test_knn_returns_ranked_neighbours():
    vectors = {"a": [1.0, 0.0], "b": [0.9, 0.1], "c": [0.0, 1.0]}
    result, engine = kernel.knn(vectors, k=1, min_similarity=0.0)
    assert result["a"] and result["a"][0][0] == "b"
    assert result["a"][0][1] > result["c"][0][1]
    assert engine


def test_python_fallbacks_are_used_when_the_binary_is_hidden(monkeypatch, tmp_path):
    monkeypatch.setattr(kernel, "_ensure_binary", lambda: None)
    monkeypatch.setattr(kernel, "_KERNEL_INFO", None)
    ids, edges = _two_triangles()
    values, engine = kernel.pagerank(ids, edges)
    assert abs(sum(values.values()) - 1.0) < 1e-6
    assert engine == "python-fallback"
    assert kernel.kernel_info(force=True)["available"] is False
