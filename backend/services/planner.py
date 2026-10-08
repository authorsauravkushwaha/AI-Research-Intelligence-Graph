"""Query planning for the NEXUS graph DSL (§4.3 / §30).

The DSL is deliberately tiny and *closed*:

    topic:"AI Agents" type in (Paper, Method) rel in (CITES) depth<=2 year>=2022 limit 60

Two implementations turn it into parameterised Cypher:

* the **Kotlin planner** (`polyglot/kotlin`, the reference implementation) — the API
  prefers it, because the JVM's sealed AST makes the planner total and the fallback
  traversal engine shares the same parse tree;
* this module's **Python port**, which answers when the sidecar is not running.

Both emit the identical JSON contract — `cypher`, `params`, `explanation`, `cost`,
`query` — and `scripts/compare_planners.py` checks that on a fixture of DSL queries,
so "the two planners agree" is a test result rather than a claim. The response always
says which one answered (`source: "sidecar" | "python-fallback"`), and nothing in the
demo depends on a JVM being present.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

from backend.config import get_settings

log = logging.getLogger("nexus.planner")

# Whitelists — closed enums in the Kotlin planner, mirrored here. Unknown values are
# rejected at parse time, so nothing outside these lists can reach a query string.
NODE_LABELS = (
    "Paper", "Author", "Topic", "Method", "Dataset", "Institution", "Claim", "Community",
)
REL_TYPES = (
    "AUTHORED", "CITES", "STUDIES", "USES_METHOD", "USES_DATASET", "AFFILIATED_WITH",
    "MAKES_CLAIM", "SUPPORTS", "CONTRADICTS", "BELONGS_TO", "RELATED_TO",
)

FULLTEXT_INDEX = "nexus-fulltext"
DEFAULT_DEPTH = 1
DEFAULT_LIMIT = 50
FANOUT = 8.0  # average degree of the NEXUS research graph
COST_BUDGET = 4000

_RE_TOPIC = re.compile(r'topic\s*:\s*"([^"]+)"', re.IGNORECASE)
_RE_AUTHOR = re.compile(r'author\s*:\s*"([^"]+)"', re.IGNORECASE)
_RE_YEAR_MIN = re.compile(r"year\s*>=\s*(\d{4})", re.IGNORECASE)
_RE_YEAR_MAX = re.compile(r"year\s*<=\s*(\d{4})", re.IGNORECASE)
_RE_DEPTH = re.compile(r"depth\s*<=?\s*(\d{1,2})", re.IGNORECASE)
_RE_LIMIT = re.compile(r"limit\s+(\d{1,4})", re.IGNORECASE)
_RE_TYPE = re.compile(r"type\s+in\s*\(([^)]*)\)", re.IGNORECASE)
_RE_REL = re.compile(r"rel\s+in\s*\(([^)]*)\)", re.IGNORECASE)
_WHITESPACE = re.compile(r"\s+")


class DslError(ValueError):
    """Raised for DSL that names something outside the whitelists."""


def _label(raw: str) -> str:
    for candidate in NODE_LABELS:
        if candidate.lower() == raw.strip().lower():
            return candidate
    raise DslError(f"Unknown node type '{raw.strip()}'. Allowed: {', '.join(NODE_LABELS)}")


def _relationship(raw: str) -> str:
    for candidate in REL_TYPES:
        if candidate.lower() == raw.strip().lower():
            return candidate
    raise DslError(f"Unknown relationship '{raw.strip()}'. Allowed: {', '.join(REL_TYPES)}")


def _items(raw: str) -> list[str]:
    return [piece.strip() for piece in raw.split(",") if piece.strip()]


def parse_dsl(dsl: str) -> dict[str, Any]:
    """Parse the DSL into a query record, mirroring the Kotlin parser clause for clause."""
    text = dsl or ""
    filters: list[dict[str, Any]] = []
    labels: list[str] = []
    relationships: list[str] = []

    match = _RE_TOPIC.search(text)
    topic_seed = match.group(1).strip() if match else None
    if match:
        text = text.replace(match.group(0), " ")

    match = _RE_AUTHOR.search(text)
    if match:
        filters.append({"kind": "TextContains", "field": "name", "value": match.group(1).strip()})
        text = text.replace(match.group(0), " ")

    match = _RE_YEAR_MIN.search(text)
    if match:
        filters.append({"kind": "IntAtLeast", "field": "year", "value": int(match.group(1))})
        text = text.replace(match.group(0), " ")

    match = _RE_YEAR_MAX.search(text)
    if match:
        filters.append({"kind": "IntAtMost", "field": "year", "value": int(match.group(1))})
        text = text.replace(match.group(0), " ")

    depth = DEFAULT_DEPTH
    match = _RE_DEPTH.search(text)
    if match:
        depth = min(max(int(match.group(1)), 1), 3)
        text = text.replace(match.group(0), " ")

    limit = DEFAULT_LIMIT
    match = _RE_LIMIT.search(text)
    if match:
        limit = min(max(int(match.group(1)), 1), 500)
        text = text.replace(match.group(0), " ")

    match = _RE_TYPE.search(text)
    if match:
        labels = [_label(piece) for piece in _items(match.group(1))]
        text = text.replace(match.group(0), " ")

    match = _RE_REL.search(text)
    if match:
        relationships = [_relationship(piece) for piece in _items(match.group(1))]
        text = text.replace(match.group(0), " ")

    free = _WHITESPACE.sub(" ", text).strip().strip('"')
    seed = " ".join(part for part in (topic_seed, free or None) if part)
    return {
        "text": seed or None,
        "labels": labels,
        "relationships": relationships,
        "filters": filters,
        "depth": depth,
        "limit": limit,
    }


def _filter_explanation(filt: dict[str, Any]) -> str:
    """Kotlin prints these with the data class' toString — same shape here."""
    return f"{filt['kind']}(field={filt['field']}, value={filt['value']})"


def _predicates(
    query: dict[str, Any], params: dict[str, Any], why: list[str]
) -> list[str]:
    predicates: list[str] = []
    for filt in query["filters"]:
        field, kind, value = filt["field"], filt["kind"], filt["value"]
        if kind == "TextEquals":
            predicates.append(f"toLower(n.{field}) = toLower(${field}Exact)")
            params[f"{field}Exact"] = value
        elif kind == "TextContains":
            predicates.append(f"toLower(n.{field}) CONTAINS toLower(${field})")
            params[field] = value
        elif kind == "IntAtLeast":
            predicates.append(f"n.{field} >= ${field}Min")
            params[f"{field}Min"] = value
        elif kind == "IntAtMost":
            predicates.append(f"n.{field} <= ${field}Max")
            params[f"{field}Max"] = value
        elif kind == "InList":
            predicates.append(f"n.{field} IN ${field}List")
            params[f"{field}List"] = value
    if query["filters"]:
        why.append("Applied structured filters: " + ", ".join(_filter_explanation(f) for f in query["filters"]))

    if query["relationships"]:
        predicates.append("(n)-[" + "|".join(query["relationships"]) + "]-()")
        why.append(
            "Relationship filter: edge type must be one of "
            + ", ".join(query["relationships"])
            + "."
        )
    return predicates


def estimate_cost(query: dict[str, Any], corpus_entities: int = COST_BUDGET) -> dict[str, Any]:
    """The Kotlin cost model, mirrored: seeds are page-bound, hops are budget-bound."""
    seeds = min(query["limit"], 200)
    hops = max(query["depth"] - 1, 0)
    raw = seeds * (1 + hops * FANOUT)
    return {
        "depth": query["depth"],
        "fanout": FANOUT,
        "seeds": seeds,
        "estimated_nodes_visited": min(int(raw), corpus_entities),
        "budget": corpus_entities,
        "strategy": "fulltext -> expand" if query["text"] else "label scan -> rank",
        "safe": raw <= corpus_entities,
    }


def python_plan(dsl: str, fulltext_index: str = FULLTEXT_INDEX) -> dict[str, Any]:
    """Plan a DSL string without a JVM. Raises :class:`DslError` on unknown names."""
    try:
        query = parse_dsl(dsl)
    except DslError as exc:
        return {"ok": False, "engine": "nexus-python-planner", "error": str(exc)}

    if not query["text"] and not query["filters"] and not query["labels"] and not query["relationships"]:
        return {
            "ok": False,
            "engine": "nexus-python-planner",
            "error": 'empty query: give free text, a topic:"\u2026" phrase, or a structured clause',
        }

    params: dict[str, Any] = {}
    why: list[str] = []

    labels = query["labels"]
    label_clause = (
        "n" if not labels
        else f"n:{labels[0]}" if len(labels) == 1
        else "n:" + "|".join(labels)
    )
    if labels:
        why.append("Restricted scan to labels: " + ", ".join(labels) + ".")

    predicates = _predicates(query, params, why)
    where = " AND ".join(predicates)

    if query["text"]:
        text = query["text"]
        params["q"] = text
        params["limit"] = query["limit"]
        why.append(
            f"Free text '{text}' handled by the full-text index (tokenised, relevance-ranked)."
        )
        header = [
            f"CALL db.index.fulltext.queryNodes('{fulltext_index}', $q) YIELD node AS n, score",
            "WHERE score > 0.0",
        ]
        if labels:
            header[1] += " AND (" + " OR ".join(f"'{name}' IN labels(n)" for name in labels) + ")"
        lines = list(header)
        if where:
            # `score` is only in scope after the full-text yield, hence WITH.
            lines.append(f"WITH n, score WHERE {where}")
            why.append("Post-filter applied on indexed properties (bound parameters only).")
        lines.append("RETURN n, score ORDER BY score DESC LIMIT $limit")
        cypher = "\n".join(lines)
    else:
        params["limit"] = query["limit"]
        why.append(
            f"Limit {query['limit']} keeps the first paint under a second (progressive expansion)."
        )
        parts = [f"MATCH ({label_clause})"]
        if where:
            parts.append(f"WHERE {where}")
        parts += [
            "RETURN n",
            "ORDER BY coalesce(n.pagerank, 0.0) DESC",
            "LIMIT $limit",
        ]
        cypher = "\n".join(parts)

    return {
        "ok": True,
        "engine": "nexus-python-planner",
        "query": {
            "text": query["text"],
            "labels": labels,
            "relationships": query["relationships"],
            "depth": query["depth"],
            "limit": query["limit"],
        },
        "cypher": cypher,
        "params": params,
        "explanation": why,
        "cost": estimate_cost(query),
    }


def sidecar_plan(dsl: str, timeout: float = 2.5) -> dict[str, Any] | None:
    """Ask the Kotlin planner for a plan; ``None`` on any failure (never raises)."""
    url = get_settings().embeddings.planner_service
    if not url:
        return None
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url.rstrip("/") + "/api/plan", params={"q": dsl})
        payload = resp.json()
        if not isinstance(payload, dict) or "cypher" not in payload and "error" not in payload:
            log.info("kotlin planner returned an unexpected payload (%s)", resp.status_code)
            return None
        payload.setdefault("engine", "nexus-kotlin-planner")
        return payload
    except Exception as exc:  # noqa: BLE001 - any failure must degrade gracefully
        log.info("kotlin planner unavailable (%s) — planning locally", exc)
        return None


def plan(dsl: str) -> dict[str, Any]:
    """Plan a DSL string, preferring the JVM planner and naming the engine that answered."""
    payload = sidecar_plan(dsl)
    if payload is not None:
        payload["source"] = "sidecar"
        return payload
    local = python_plan(dsl)
    local["source"] = "python-fallback"
    return local


def planner_status(timeout: float = 0.8) -> dict[str, Any]:
    """Report whether the JVM planner is reachable — used by ``/api/services``."""
    url = get_settings().embeddings.planner_service
    reachable, engine = False, "python-fallback"
    if url:
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.get(url.rstrip("/") + "/health")
            if resp.status_code == 200:
                reachable = True
                engine = str(resp.json().get("engine", "nexus-kotlin-planner"))
        except Exception:  # noqa: BLE001
            reachable = False
    return {
        "name": "planner",
        "language": "kotlin",
        "url": url,
        "reachable": reachable,
        "engine": engine if reachable else "nexus-python-planner",
        "fallback": "backend/services/planner.py::python_plan",
    }
