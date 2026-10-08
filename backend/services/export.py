"""Graph export (§4.6 nice-to-have).

Four formats, all produced from the same in-memory graph the API serves:

* **GraphML** — opens in Gephi / yEd / Cytoscape for offline inspection,
* **CSV** — a nodes table plus an edges table inside one response,
* **JSON** — the raw node/edge payload (for notebooks),
* **Cypher** — a runnable script that recreates the exported slice in Neo4j, so
  anyone can move the in-process demo into a real database in one paste.

The C# sidecar in `polyglot/dotnet` offers the same GraphML/CSV/report endpoints
as a standalone service; this module is the zero-dependency path the API uses.
"""

from __future__ import annotations

import io
import json
import xml.sax.saxutils as saxutils
from typing import Any, Sequence

from backend.models.graph import GEdge, GNode


def _slug(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value.lower())


def export_graph(
    store: Any,
    *,
    format: str = "graphml",
    node_types: Sequence[str] | None = None,
    rel_types: Sequence[str] | None = None,
    limit: int = 800,
) -> Any:
    allowed_types = set(node_types) if node_types else None
    allowed_rels = set(rel_types) if rel_types else None

    nodes = [
        node
        for node in store.nodes.values()
        if (allowed_types is None or node.label in allowed_types)
    ]
    nodes.sort(key=lambda n: -(store.analytics.pagerank.get(n.id, 0.0) if store.analytics else 0.0))
    nodes = nodes[:limit]
    selected = {node.id for node in nodes}
    edges = [
        edge
        for edge in store.edges
        if edge.src in selected and edge.dst in selected and (allowed_rels is None or edge.type in allowed_rels)
    ]

    if format == "json":
        return {
            "format": "nexus-graph-json",
            "engine": store.engine_name,
            "nodes": [node.to_json() for node in nodes],
            "edges": [edge.to_json() for edge in edges],
            "counts": {"nodes": len(nodes), "edges": len(edges)},
            "provenance": store.health().get("provenance", {}),
        }
    if format == "graphml":
        return _graphml(store, nodes, edges)
    if format == "csv":
        return _csv(nodes, edges)
    if format == "cypher":
        return _cypher(store, nodes, edges)
    raise ValueError(f"unsupported export format: {format!r}")


def _graphml(store: Any, nodes: list[GNode], edges: list[GEdge]) -> str:
    buffer = io.StringIO()
    buffer.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    buffer.write('<graphml xmlns="http://graphml.graphdrawing.org/xmlns">\n')
    keys = {
        "label": "string",
        "name": "string",
        "year": "int",
        "pagerank": "double",
        "betweenness": "double",
        "degree": "int",
        "community": "int",
        "url": "string",
        "provenance": "string",
        "weight": "double",
        "predicted": "boolean",
    }
    for key, kind in keys.items():
        for target in ("node", "edge"):
            default = ' default="0"' if kind in {"int", "double", "boolean"} else ""
            buffer.write(f'  <key id="{target}_{key}" for="{target}" attr.name="{key}" attr.type="{kind}"{default}/>\n')
    buffer.write("  <graph id=\"nexus\" edgedefault=\"undirected\">\n")
    for node in nodes:
        metrics = store.analytics.metrics_for(node.id) if store.analytics else {}
        buffer.write(f'    <node id="{saxutils.escape(node.id)}">\n')
        values: dict[str, Any] = {
            "label": node.label,
            "name": node.name,
            "year": node.props.get("year") or 0,
            "pagerank": round(metrics.get("pagerank", 0.0), 8),
            "betweenness": round(metrics.get("betweenness", 0.0), 8),
            "degree": metrics.get("degree", 0),
            "community": metrics.get("community", -1) if metrics.get("community") is not None else -1,
            "url": node.props.get("url") or "",
        }
        for key, value in values.items():
            if value == "":
                continue
            buffer.write(f'      <data key="node_{key}">{saxutils.escape(str(value))}</data>\n')
        buffer.write("    </node>\n")
    for index, edge in enumerate(edges):
        buffer.write(
            f'    <edge id="e{index}" source="{saxutils.escape(edge.src)}" target="{saxutils.escape(edge.dst)}">\n'
        )
        buffer.write(f'      <data key="edge_label">{saxutils.escape(edge.type)}</data>\n')
        buffer.write(f'      <data key="edge_weight">{float(edge.props.get("weight", edge.props.get("similarity", 1.0)) or 1.0)}</data>\n')
        buffer.write(f'      <data key="edge_provenance">{saxutils.escape(str(edge.props.get("provenance", "graph-construction")))}</data>\n')
        buffer.write(f'      <data key="edge_predicted">{"true" if edge.props.get("predicted") else "false"}</data>\n')
        buffer.write("    </edge>\n")
    buffer.write("  </graph>\n</graphml>\n")
    return buffer.getvalue()


def _csv(nodes: list[GNode], edges: list[GEdge]) -> str:
    buffer = io.StringIO()
    buffer.write("# NEXUS nodes\n")
    buffer.write("id,label,name,year,url\n")
    for node in nodes:
        values = [
            node.id,
            node.label,
            node.name.replace('"', "'"),
            str(node.props.get("year") or ""),
            str(node.props.get("url") or ""),
        ]
        buffer.write(",".join(f'"{value}"' if "," in value else value for value in values) + "\n")
    buffer.write("\n# NEXUS edges (predicted relationships are marked)\n")
    buffer.write("source,target,type,weight,predicted,provenance\n")
    for edge in edges:
        buffer.write(
            f"{edge.src},{edge.dst},{edge.type},"
            f"{float(edge.props.get('weight', edge.props.get('similarity', 1.0)) or 1.0)},"
            f"{'true' if edge.props.get('predicted') else 'false'},"
            f"{str(edge.props.get('provenance', 'graph-construction')).replace(',', ';')}\n"
        )
    return buffer.getvalue()


def _cypher(store: Any, nodes: list[GNode], edges: list[GEdge]) -> str:
    lines = [
        "// NEXUS graph export — recreates this slice in Neo4j.",
        "// Every value below is a bound literal because this file is data, not a query:",
        "// the API itself always uses parameters for user input.",
        "CREATE CONSTRAINT nexus_id IF NOT EXISTS FOR (n:NexusNode) REQUIRE n.id IS UNIQUE;",
        "",
    ]
    by_label: dict[str, list[GNode]] = {}
    for node in nodes:
        by_label.setdefault(node.label, []).append(node)
    for label, group in sorted(by_label.items()):
        lines.append(f"// {label}: {len(group)} nodes")
        for node in group:
            props = {"id": node.id, "name": node.name, **{
                k: v for k, v in node.props.items() if isinstance(v, (str, int, float, bool)) and k not in {"name"}
            }}
            lines.append(f"MERGE (n:{label} {{id: {json.dumps(node.id)}}}) SET n += {json.dumps(props)};")
        lines.append("")
    lines.append("// relationships")
    for edge in edges:
        props = {k: v for k, v in edge.props.items() if isinstance(v, (str, int, float, bool))}
        lines.append(
            f"MATCH (a {{id: {json.dumps(edge.src)}}}), (b {{id: {json.dumps(edge.dst)}}}) "
            f"MERGE (a)-[r:{edge.type}]->(b) SET r += {json.dumps(props)};"
        )
    lines += [
        "",
        "// communities and metrics",
        "// MATCH (n) SET n.pagerank = n.pagerank, n.betweenness = n.betweenness;  // written by the store",
        "",
        f"// exported from the {store.engine_name} engine, {len(nodes)} nodes / {len(edges)} edges",
    ]
    return "\n".join(lines) + "\n"
