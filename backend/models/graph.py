"""Core graph value objects shared by every backend and engine.

These are intentionally backend-agnostic: the in-process store and the Neo4j
store both produce `GNode` / `GEdge` / `Subgraph`, so the analytics, GraphRAG and
gap-detection layers never need to know which database answered.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

NodeLabel = Literal[
    "Paper", "Author", "Topic", "Method", "Dataset", "Institution", "Claim", "Community", "Metric"
]

REL_TYPES: tuple[str, ...] = (
    "AUTHORED",
    "CITES",
    "STUDIES",
    "USES_METHOD",
    "USES_DATASET",
    "AFFILIATED_WITH",
    "MAKES_CLAIM",
    "SUPPORTS",
    "CONTRADICTS",
    "BELONGS_TO",
    "RELATED_TO",
    "MEASURED_BY",
    "SIMILAR_TO",
    "PREDICTED_LINK",
)

#: Relationships that carry *semantic* meaning for gap analysis (co-occurrence
#: through a paper) as opposed to purely structural links.
ASSOCIATIVE_RELS: frozenset[str] = frozenset(
    {"STUDIES", "USES_METHOD", "USES_DATASET", "MEASURED_BY", "BELONGS_TO", "RELATED_TO"}
)

NODE_COLORS: dict[str, str] = {
    "Paper": "#4cc9f0",
    "Author": "#b892ff",
    "Topic": "#f4a261",
    "Method": "#2ec4b6",
    "Dataset": "#8ecae6",
    "Institution": "#94a3b8",
    "Claim": "#ff6b6b",
    "Community": "#f9c74f",
    "Metric": "#a3e635",
}


@dataclass(slots=True)
class GNode:
    id: str
    label: str
    name: str
    props: dict[str, Any] = field(default_factory=dict)

    def with_metrics(self, metrics: dict[str, float] | None, degree: int | None = None) -> "GNode":
        """Copy of this node carrying its computed metrics (used by graph payloads)."""
        merged = dict(self.props)
        for key, value in (metrics or {}).items():
            if value is None:
                continue
            merged[key] = round(value, 6) if isinstance(value, float) else value
        if degree is not None:
            merged["degree"] = degree
        return GNode(id=self.id, label=self.label, name=self.name, props=merged)

    def to_json(self, *, metrics: dict[str, float] | None = None, degree: int | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "label": self.name,
            "type": self.label,
            "color": NODE_COLORS.get(self.label, "#8899aa"),
        }
        merged = dict(self.props)
        if metrics:
            merged.update(metrics)
        if degree is not None:
            merged["degree"] = degree
        out.update({k: v for k, v in merged.items() if v is not None})
        return out


@dataclass(slots=True)
class GEdge:
    src: str
    dst: str
    type: str
    props: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": f"{self.src}|{self.type}|{self.dst}",
            "source": self.src,
            "target": self.dst,
            "type": self.type,
            **{k: v for k, v in self.props.items() if v is not None},
        }


@dataclass(slots=True)
class Subgraph:
    nodes: list[GNode] = field(default_factory=list)
    edges: list[GEdge] = field(default_factory=list)
    truncated: bool = False
    notes: list[str] = field(default_factory=list)

    def node_ids(self) -> set[str]:
        return {n.id for n in self.nodes}

    def add_node(self, node: GNode) -> bool:
        if any(n.id == node.id for n in self.nodes):
            return False
        self.nodes.append(node)
        return True

    def add_edge(self, edge: GEdge) -> bool:
        for e in self.edges:
            if e.src == edge.src and e.dst == edge.dst and e.type == edge.type:
                return False
        self.edges.append(edge)
        return True

    def merge(self, other: "Subgraph") -> None:
        for n in other.nodes:
            self.add_node(n)
        for e in other.edges:
            self.add_edge(e)
        self.truncated = self.truncated or other.truncated
        self.notes.extend(other.notes)

    def to_json(self) -> dict[str, Any]:
        return {
            "nodes": [n.to_json() for n in self.nodes],
            "edges": [e.to_json() for e in self.edges],
            "truncated": self.truncated,
            "notes": self.notes,
            "counts": self.label_counts(),
        }

    def label_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for n in self.nodes:
            counts[n.label] = counts.get(n.label, 0) + 1
        return counts


def build_adjacency(nodes: Iterable[GNode], edges: Iterable[GEdge]) -> dict[str, list[tuple[str, str, float]]]:
    """Undirected adjacency used by the in-process gap engine."""
    adj: dict[str, list[tuple[str, str, float]]] = {n.id: [] for n in nodes}
    for e in edges:
        if e.src in adj and e.dst in adj:
            w = float(e.props.get("weight", 1.0) or 1.0)
            adj[e.src].append((e.dst, e.type, w))
            adj[e.dst].append((e.src, e.type, w))
    return adj
