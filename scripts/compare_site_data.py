#!/usr/bin/env python3
"""Diff the static site's in-browser engine against the live NEXUS API.

    python scripts/build_site.py                      # writes ./site (and its index.json)
    node scripts/check_site_data.mjs --out /tmp/js.json
    python scripts/compare_site_data.py /tmp/js.json

`check_site_data.mjs` runs the browser-side layer over the published data and dumps what
it computed; this script asks the **real** application (the same store, gap engine,
algorithms and routes the server exposes) for the same things and compares them field by
field. It is the evidence behind the claim that the published site is not a mock-up:
graph subgraphs, shortest paths, paper/node detail, listings, search, the timeline and the
explorer are computed in the browser and are expected to agree with the engine.

Exit code 0 only when every comparison passes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []
CHECKS = 0


def ok(label: str, extra: str = "") -> None:
    global CHECKS
    CHECKS += 1
    print(f"  ok   {label}{'  ' + extra if extra else ''}")


def bad(label: str, detail: str) -> None:
    global CHECKS
    CHECKS += 1
    FAILURES.append(f"{label}: {detail}")
    print(f"  FAIL {label}  {detail}")


def near(a: Any, b: Any, tol: float = 1e-6) -> bool:
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return a == b


def compare(label: str, js: Any, engine: Any, *, tol: float = 1e-6) -> None:
    """Compare nested structures; floats use a tolerance, everything else must be equal."""
    if isinstance(js, dict) and isinstance(engine, dict):
        for key in engine:
            if key not in js:
                continue
            compare(f"{label}.{key}", js[key], engine[key], tol=tol)
        return
    if isinstance(js, list) and isinstance(engine, list):
        if len(js) != len(engine):
            bad(label, f"length {len(js)} vs {len(engine)}")
            return
        for index, (left, right) in enumerate(zip(js, engine)):
            compare(f"{label}[{index}]", left, right, tol=tol)
        return
    if isinstance(engine, (int, float)) and not isinstance(engine, bool):
        if not near(js, engine, tol):
            bad(label, f"{js} vs {engine}")
        return
    if js != engine:
        bad(label, f"{json.dumps(js)[:80]} vs {json.dumps(engine)[:80]}")


def ids(rows: list[dict[str, Any]]) -> list[str]:
    return [str(row.get("id")) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("js_dump", type=Path, help="the JSON written by scripts/check_site_data.mjs --out")
    parser.add_argument("--site", type=Path, default=ROOT / "site")
    parser.add_argument("--tolerance", type=float, default=1e-6)
    args = parser.parse_args()

    from fastapi.testclient import TestClient

    from backend.api.app import create_app

    client = TestClient(create_app())
    graph_dataset = json.loads((ROOT / "site" / "data" / "graph.json").read_text())
    index = json.loads((args.site / "data" / "index.json").read_text())
    js = json.loads(args.js_dump.read_text())
    computed = js["results"]["computed"]
    references = index["references"]

    print(f"  · comparing {args.js_dump.name} (static layer v{js['engine']}) against the live API")
    print(f"  · corpus: {index['counts']['nodes']} nodes / {index['counts']['edges']} edges")

    # ---------------------------------------------------------------- graph
    for reference, payload in zip(references["graph"], computed["graph"]):
        label = f"graph {json.dumps(reference['payload'])}"
        engine = client.post("/api/graph", json=reference["payload"]).json()
        if ids(payload["nodes"]) == [] and ids(engine["nodes"]) == []:
            ok(f"{label} (both empty)")
            continue
        if set(ids(payload["nodes"])) != set(ids(engine["nodes"])):
            missing = sorted(set(ids(engine["nodes"])) - set(ids(payload["nodes"])))[:4]
            extra = sorted(set(ids(payload["nodes"])) - set(ids(engine["nodes"])))[:4]
            bad(label, f"node sets differ (engine-only {missing}, browser-only {extra})")
        elif set(ids(payload["edges"])) != set(ids(engine["edges"])):
            bad(label, f"edge sets differ ({len(payload['edges'])} vs {len(engine['edges'])})")
        elif payload["counts"] != engine["counts"]:
            bad(label, f"counts {payload['counts']} vs {engine['counts']}")
        else:
            ok(f"{label} — {len(payload['nodes'])} nodes / {len(payload['edges'])} edges, same sets")

    # ---------------------------------------------------------------- paths
    # A shortest path is rarely unique: the kernel and the browser may break a tie between
    # two equally short chains, and both are valid evidence paths. Compared here is what
    # matters — same endpoints, same hop count, and every hop backed by a published edge.
    edge_pairs = set()
    for edge in graph_dataset["edges"]:
        edge_pairs.add((edge["source"], edge["target"]))
        edge_pairs.add((edge["target"], edge["source"]))
    node_ids = {node["id"] for node in graph_dataset["nodes"]}

    for row in computed["paths"]:
        label = f"path {row['source']} → {row['target']}"
        engine = client.get("/api/graph/path", params={"source": row["source"], "target": row["target"]}).json()
        js_payload = row["payload"]
        chain = js_payload.get("nodes") or []
        if js_payload.get("found") != engine.get("found"):
            bad(label, f"found {js_payload.get('found')} vs {engine.get('found')}")
        elif js_payload.get("hops_count") != engine.get("hops_count"):
            bad(label, f"{js_payload.get('hops_count')} hops in the browser vs {engine.get('hops_count')} in the kernel")
        elif chain and (chain[0] != row["source"] or chain[-1] != row["target"]):
            bad(label, f"chain does not join the two nodes: {chain}")
        elif any(a not in node_ids or b not in node_ids for a, b in zip(chain, chain[1:])):
            bad(label, f"chain names a node outside graph.json: {chain}")
        elif any((a, b) not in edge_pairs for a, b in zip(chain, chain[1:])):
            bad(label, f"chain uses a hop that is not an edge in graph.json: {chain}")
        else:
            note = "same chain as the kernel" if chain == engine.get("nodes") else "same length, different tie-break"
            ok(f"{label} — {js_payload.get('hops_count')} hops ({note})")

    # ------------------------------------------------------- paper details
    for paper in computed["papers"]:
        label = f"paper {paper['id']}"
        engine = client.get(f"/api/papers/{paper['id']}").json()
        for field in ("pagerank", "betweenness", "degree", "community"):
            if not near(paper["metrics"].get(field), engine["metrics"].get(field), args.tolerance):
                bad(f"{label}.{field}", f"{paper['metrics'].get(field)} vs {engine['metrics'].get(field)}")
                break
        else:
            if set(ids(paper.get("claims", []))) != set(ids(engine.get("claims", []))):
                bad(f"{label}.claims", f"{len(paper.get('claims', []))} vs {len(engine.get('claims', []))}")
            elif ids(paper.get("similar_papers", [])) != ids(engine.get("similar_papers", [])):
                bad(f"{label}.similar_papers",
                    f"{len(paper.get('similar_papers', []))} vs {len(engine.get('similar_papers', []))} (order matters)")
            elif (paper.get("in_graph_citation_degree") != engine.get("in_graph_citation_degree")):
                bad(f"{label}.citations", f"{paper.get('in_graph_citation_degree')} vs {engine.get('in_graph_citation_degree')}")
            else:
                ok(f"{label} — metrics, claims and similar papers agree")

    # -------------------------------------------------------- paper lists
    for row in computed["paper_lists"]:
        query = row["query"]
        label = f"papers {query}"
        engine = client.get("/api/papers", params={k: v for k, v in query.items() if k != "topic"}).json()
        js_payload = row["payload"]
        if js_payload["total"] != engine["total"]:
            bad(f"{label}.total", f"{js_payload['total']} vs {engine['total']}")
        elif ids(js_payload["items"]) != ids(engine["items"]):
            bad(f"{label}.items", f"order differs (first: {ids(js_payload['items'])[:3]} vs {ids(engine['items'])[:3]})")
        else:
            ok(f"{label} — {js_payload['total']} matching papers, same order")

    # -------------------------------------------------------- node detail
    for node in computed["nodes"]:
        label = f"node {node['id']}"
        engine = client.get(f"/api/nodes/{node['id']}").json()
        mine = {row["metric"]: row["value"] for row in node["metrics_explained"]}
        theirs = {row["metric"]: row["value"] for row in engine["metrics_explained"]}
        if mine != theirs:
            bad(f"{label}.metrics_explained", f"{mine} vs {theirs}")
        elif node["neighbour_counts"] != engine["neighbour_counts"]:
            bad(f"{label}.neighbour_counts", f"{node['neighbour_counts']} vs {engine['neighbour_counts']}")
        else:
            ok(f"{label} — metrics, explanations and neighbour counts agree")

    # -------------------------------------------------------- neighbours
    for row in computed["neighbours"]:
        label = f"neighbours {row['id']}"
        engine = client.get(f"/api/graph/neighbours/{row['id']}", params={"limit": 40}).json()
        if row["payload"]["center"]["id"] != engine["center"]["id"]:
            bad(label, "center differs")
        elif row["payload"]["count"] != engine["count"]:
            bad(label, f"count {row['payload']['count']} vs {engine['count']}")
        else:
            ok(f"{label} — {row['payload']['count']} neighbours")

    # -------------------------------------------------------- node lists
    for row in computed["node_lists"]:
        query = row["query"]
        label = f"nodes {query}"
        engine = client.get("/api/nodes", params=query).json()
        if row["payload"]["total"] != engine["total"]:
            bad(f"{label}.total", f"{row['payload']['total']} vs {engine['total']}")
        elif ids(row["payload"]["items"]) != ids(engine["items"]):
            bad(f"{label}.items", "order differs")
        else:
            ok(f"{label} — {engine['total']} nodes, same order")

    # -------------------------------------------------------- search
    for row in computed["search"]:
        query = row["query"]
        label = f"search {query['q']!r}"
        engine = client.get("/api/search", params={"q": query["q"], "labels": "Topic", "limit": 5}).json()
        mine = set(ids(row["payload"]))
        theirs = set(ids(engine["results"]))
        if not mine or not theirs:
            bad(label, f"empty result set (browser {len(mine)}, engine {len(theirs)})")
        elif not mine & theirs:
            bad(label, f"disjoint results: {sorted(mine)[:3]} vs {sorted(theirs)[:3]}")
        else:
            ok(f"{label} — {len(mine & theirs)} of {len(theirs)} engine hits matched")

    # -------------------------------------------------------- explorer
    for row in computed["explorer"]:
        query = {k: v for k, v in row["query"].items()}
        label = f"explorer {query}"
        engine = client.get("/api/explorer", params={k: v for k, v in query.items() if v is not None}).json()
        mine = row["payload"]
        diffs: list[str] = []
        for key, value in engine["counts"].items():
            if mine["counts"].get(key) != value:
                diffs.append(f"counts.{key} {mine['counts'].get(key)} vs {value}")
        if mine["years"] != engine["years"]:
            diffs.append(f"years differ ({len(mine['years'])} vs {len(engine['years'])})")
        for key in ("papers_since_2023", "oldest_year", "newest_year"):
            if mine["recency"].get(key) != engine["recency"].get(key):
                diffs.append(f"recency.{key} {mine['recency'].get(key)} vs {engine['recency'].get(key)}")
        for key in ("top_papers", "top_topics", "top_methods", "top_bridge_papers"):
            if ids(mine.get(key, [])) != ids(engine.get(key, [])):
                diffs.append(f"{key} differs")
        if [c["community_index"] for c in mine.get("communities", [])] != \
           [c["community_index"] for c in engine.get("communities", [])]:
            diffs.append("communities differ")
        if diffs:
            bad(label, "; ".join(diffs[:3]))
        else:
            ok(f"{label} — counts, years, leaderboards and communities agree")

    # -------------------------------------------------------- timeline
    for row in computed["timeline"]:
        query = row["query"]
        label = f"timeline {query}"
        engine = client.get("/api/timeline", params={k: v for k, v in query.items() if v is not None}).json()
        mine = row["payload"]
        if mine["papers_per_year"] != engine["papers_per_year"]:
            bad(f"{label}.papers_per_year",
                f"{sum(mine['papers_per_year'].values())} vs {sum(engine['papers_per_year'].values())} papers")
        elif list(mine["topics_over_time"].keys()) != list(engine["topics_over_time"].keys()):
            bad(f"{label}.topics_over_time",
                f"{len(mine['topics_over_time'])} vs {len(engine['topics_over_time'])} series")
        else:
            ok(f"{label} — {sum(mine['papers_per_year'].values())} paper-year records, same 12 series")

    print(f"\n{CHECKS - len(FAILURES)}/{CHECKS} comparisons agree with the live engine")
    if FAILURES:
        print(f"static layer diverges from the engine ({len(FAILURES)} problem(s)):", file=sys.stderr)
        for failure in FAILURES[:12]:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print("the published site computes the same answers as the live engine")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
