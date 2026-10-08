"""Corpus -> knowledge graph.

This is the NEXUS data pipeline's final stage: it turns curated paper records
into the node/relationship structure the rest of the system reasons over.

Pipeline position:

    papers.json (metadata)
        -> entity extraction (topics / methods / datasets)
        -> claim extraction (with provenance)
        -> relationship extraction (authored, cites, studies, uses_*)
        -> similarity + community analysis (engine.analytics)
        -> Neo4j (or the in-process store)

Every synthesised edge carries `provenance` in its properties so the UI can
always answer "why is this edge here?".
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable

from backend.models.graph import GEdge, GNode, Subgraph


def _slug(text: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in text.lower())
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-")[:64] or hashlib.md5(text.encode()).hexdigest()[:8]


def topic_id(name: str) -> str:
    return f"topic:{_slug(name)}"


def method_id(name: str) -> str:
    return f"method:{_slug(name)}"


def dataset_id(name: str) -> str:
    return f"dataset:{_slug(name)}"


def author_id(name: str) -> str:
    return f"author:{_slug(name)}"


def paper_id(arxiv_id: str) -> str:
    return f"paper:{arxiv_id}"


def community_id(index: int) -> str:
    return f"community:c{index}"


def claim_id(raw: str) -> str:
    return raw if raw.startswith("claim:") else f"claim:{raw}"


@dataclass(slots=True)
class BuiltGraph:
    nodes: list[GNode]
    edges: list[GEdge]
    paper_ids: list[str]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for n in self.nodes:
            out[n.label] = out.get(n.label, 0) + 1
        for e in self.edges:
            key = f"rel:{e.type}"
            out[key] = out.get(key, 0) + 1
        return out


class GraphBuilder:
    """Builds the research graph from a validated corpus dict."""

    def __init__(self, corpus: dict[str, Any]) -> None:
        self.corpus = corpus
        self._nodes: dict[str, GNode] = {}
        self._edges: list[GEdge] = []
        self._edge_keys: set[tuple[str, str, str]] = set()

    # ------------------------------------------------------------------ util
    def _node(self, node: GNode) -> None:
        existing = self._nodes.get(node.id)
        if existing is None:
            self._nodes[node.id] = node
        else:  # merge properties (e.g. a topic seen through many papers)
            for k, v in node.props.items():
                if v is not None and k not in existing.props:
                    existing.props[k] = v

    def _edge(self, src: str, dst: str, rel: str, **props: Any) -> None:
        key = (src, dst, rel)
        if key in self._edge_keys or src == dst:
            return
        if src not in self._nodes or dst not in self._nodes:
            return
        self._edge_keys.add(key)
        self._edges.append(GEdge(src=src, dst=dst, type=rel, props={k: v for k, v in props.items() if v is not None}))

    # --------------------------------------------------------------- build
    def build(self) -> BuiltGraph:
        papers = self.corpus.get("papers", [])
        paper_ids: list[str] = []

        for record in papers:
            pid = paper_id(record["arxiv_id"])
            paper_ids.append(pid)
            self._node(
                GNode(
                    id=pid,
                    label="Paper",
                    name=record["title"],
                    props={
                        "title": record["title"],
                        "abstract": record["summary"],
                        "summary_source": record.get("summary_source", "unknown"),
                        "year": record.get("year"),
                        "url": record.get("url"),
                        "arxiv_id": record.get("arxiv_id"),
                        "field": record.get("field"),
                        "author_status": record.get("author_status", "not-collected"),
                        "provenance": {
                            "metadata": "arXiv public listing",
                            "summary": record.get("summary_source", "unknown"),
                        },
                    },
                )
            )

            # field is also a Topic of kind "field" so cross-field bridges show up
            field = record.get("field")
            if field:
                tid = topic_id(field)
                self._node(GNode(id=tid, label="Topic", name=field, props={"topic_kind": "field"}))
                self._edge(pid, tid, "STUDIES", weight=0.6, provenance="paper.field taxonomy")

            for topic in record.get("topics", []):
                tid = topic_id(topic)
                self._node(GNode(id=tid, label="Topic", name=topic, props={"topic_kind": "concept"}))
                self._edge(pid, tid, "STUDIES", weight=1.0, provenance="curated taxonomy")

            for method in record.get("methods", []):
                mid = method_id(method)
                self._node(GNode(id=mid, label="Method", name=method, props={}))
                self._edge(pid, mid, "USES_METHOD", weight=1.0, provenance="curated taxonomy")

            for dataset in record.get("datasets", []):
                did = dataset_id(dataset)
                self._node(GNode(id=did, label="Dataset", name=dataset, props={}))
                self._edge(pid, did, "USES_DATASET", weight=1.0, provenance="curated taxonomy")

            for author in record.get("authors", []):
                aid = author_id(author)
                self._node(GNode(id=aid, label="Author", name=author, props={"provenance": "arXiv listing"}))
                self._edge(aid, pid, "AUTHORED", weight=1.0, provenance="arXiv listing")

        # citations
        for citation in self.corpus.get("citations", []):
            self._edge(
                paper_id(citation["source"]),
                paper_id(citation["target"]),
                "CITES",
                weight=1.0,
                provenance=citation.get("citation_source", "curated-lineage"),
                directed=True,
            )

        # claims + provenance
        for claim in self.corpus.get("claims", []):
            cid = claim_id(claim["id"])
            pid = paper_id(claim["paper"])
            self._node(
                GNode(
                    id=cid,
                    label="Claim",
                    name=claim["text"][:120],
                    props={
                        "text": claim["text"],
                        "stance": claim.get("stance", "reports"),
                        "confidence": claim.get("confidence", "unscored"),
                        "source": claim.get("source", "unspecified"),
                        "provenance": claim.get("provenance", {}),
                    },
                )
            )
            self._edge(pid, cid, "MAKES_CLAIM", weight=1.0, provenance=claim.get("source", "unspecified"))

        return BuiltGraph(nodes=list(self._nodes.values()), edges=self._edges, paper_ids=paper_ids)


def build_graph(corpus: dict[str, Any]) -> BuiltGraph:
    return GraphBuilder(corpus).build()


def subgraph_from(nodes: Iterable[GNode], edges: Iterable[GEdge]) -> Subgraph:
    sg = Subgraph()
    for n in nodes:
        sg.add_node(n)
    for e in edges:
        sg.add_edge(e)
    return sg
