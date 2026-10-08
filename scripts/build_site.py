#!/usr/bin/env python3
"""Build the static website + installable app from the running NEXUS engines.

    python scripts/build_site.py               # writes ./site
    python scripts/build_site.py --out docs-site --topics 12

What this does
--------------
The NEXUS UI is a static front end; the intelligence lives behind `/api/*`. This script
**records the API** by driving the real application through FastAPI's TestClient — the
same store, gap engine, GraphRAG and agent the server exposes — and writes the answers
into `site/data/`. The result is a self-contained website that runs on GitHub Pages (or
any static host) with no backend, no CDN and no build step at runtime.

Nothing here is hand-written sample data: every recorded payload is the engine's own
output, and `site/data/index.json` says so, including which scopes and questions were
precomputed. The static layer in `frontend/assets/site.js` answers the rest (graph
queries, paper lookups, shortest paths, the DSL planner, the agent's graph-template
answers) by computing over the exported graph in the browser — and
`scripts/check_site_data.mjs` checks those computations against these recordings.

Exit code is non-zero if a recording fails, so CI can gate the publish.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_TOPICS = (
    "AI Agents", "Tool Use", "Multi-Agent Systems", "AI Safety", "Evaluation",
    "Agent Memory", "Reasoning", "Retrieval-Augmented Generation", "Benchmarking",
    "Human-AI Interaction",
)

AGENT_QUESTIONS = (
    "What are the most important research gaps in AI agents?",
    "Which claims contradict each other about agent memory?",
    "What are the most important papers?",
    "Where are the research gaps in agent memory?",
    "Which papers bridge AI Agents and Tool Use?",
    "What connects Multi-Agent Systems and Evaluation?",
    "Which topics does ReAct connect?",
    "Who are the most central authors?",
    "What are the most active topics in the corpus?",
    "Which communities exist in this corpus?",
    "How do I tell a prediction from a fact?",
    "What should I read first?",
)

REFERENCE_PAIRS = (
    ("paper:2210.03629", "topic:agent-memory"),
    ("paper:2303.11366", "topic:multi-agent-systems"),
    ("topic:tool-use", "topic:reasoning"),
)

# --------------------------------------------------------------- helpers -----


def slug(text: str | None) -> str:
    """Stable file-name key for a scope or question ('' -> 'default')."""
    if not text:
        return "default"
    cleaned = re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")
    return cleaned[:64] or "default"


def write_json(path: Path, payload: Any) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
    path.write_text(text, encoding="utf-8")
    return len(text)


def write_text(path: Path, text: str) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return len(text)


# ------------------------------------------------------------- recording -----


class Recorder:
    """Drives the real app and records its answers, or fails loudly."""

    def __init__(self, out: Path) -> None:
        from fastapi.testclient import TestClient

        from backend.api.app import create_app

        self.client = TestClient(create_app())
        self.out = out
        self.recorded: list[dict[str, Any]] = []
        self.bytes = 0

    def get(self, path: str, key: str | None = None, **params: Any) -> Any:
        resp = self.client.get(path, params=params or None)
        return self._save(path, resp, key)

    def post(self, path: str, body: Any, key: str | None = None) -> Any:
        resp = self.client.post(path, json=body)
        return self._save(path, resp, key)

    def raw(self, path: str, body: Any | None = None) -> str:
        resp = self.client.post(path, json=body) if body is not None else self.client.get(path)
        if resp.status_code != 200:
            raise SystemExit(f"recording failed: {path} -> HTTP {resp.status_code} {resp.text[:200]}")
        self.bytes += len(resp.text)
        self.recorded.append({"path": path, "kind": "text", "bytes": len(resp.text)})
        return resp.text

    def _save(self, path: str, resp: Any, key: str | None) -> Any:
        if resp.status_code != 200:
            raise SystemExit(f"recording failed: {path} -> HTTP {resp.status_code} {resp.text[:200]}")
        payload = resp.json()
        if key:
            size = write_json(self.out / "data" / f"{key}.json", payload)
            self.bytes += size
            self.recorded.append({"path": path, "key": f"{key}.json", "bytes": size, "kind": "json"})
        return payload



def export_graph_dataset(legend: dict[str, Any], store: Any = None) -> dict[str, Any]:
    """The dataset `frontend/assets/site.js` computes over: one file, no server.

    Every node carries the metrics the C++ kernel computed (PageRank, betweenness,
    degree, Louvain community) and its full property set, so the browser can rebuild
    paper/node detail, filtering and ranking without a second round trip.
    """
    from backend.graph.factory import get_state
    from backend.models.graph import NODE_COLORS

    store = store or get_state().store
    analytics = store.analytics
    nodes = []
    for node in store.nodes.values():
        nodes.append({
            "id": node.id,
            "label": node.name,
            "type": node.label,
            "color": NODE_COLORS.get(node.label),
            "props": node.props,
            "pagerank": round(analytics.pagerank.get(node.id, 0.0), 6),
            "betweenness": round(analytics.betweenness.get(node.id, 0.0), 6),
            "degree": analytics.degree.get(node.id, 0),
            "community": analytics.communities.get(node.id, -1),
        })
    edges = [
        {
            "id": f"{edge.src}|{edge.type}|{edge.dst}",   # GEdge has no id of its own
            "source": edge.src,
            "target": edge.dst,
            "type": edge.type,
            "weight": edge.props.get("weight"),
            "provenance": edge.props.get("provenance"),
        }
        for edge in store.edges
    ]
    communities = [
        {
            "id": profile.get("id"),
            "name": profile.get("name"),
            "community_index": profile.get("community_index"),
            "paper_count": profile.get("paper_count"),
            "topic_count": profile.get("topic_count"),
            "method_count": profile.get("method_count"),
            "top_topics": profile.get("top_topics", []),
        }
        for profile in (store.community_profiles or [])
    ]
    return {
        "format": "nexus-static-graph",
        "generated_by": "scripts/build_site.py",
        "graph_source": "MemoryGraphStore at build time — the same store the API serves",
        "counts": {"nodes": len(nodes), "edges": len(edges)},
        "legend": legend,
        "nodes": nodes,
        "edges": edges,
        "communities": communities,
    }


def record_scope(rec: Recorder, store: Any, topic: str, key: str) -> None:
    """Store the gap engine's own resolution for a scope.

    Topic resolution is scored (aliases, head-noun bonus, a similarity floor), so the
    browser does not try to reproduce it: the resolved paper/topic/method ids are shipped
    instead, which keeps the static timeline and explorer *exact* for these scopes.
    """
    from backend.engine.gaps import GapEngine

    engine = GapEngine(store)
    paper_ids, topic_ids, method_ids = engine.scope(topic or None, min_papers=1)
    write_json(rec.out / "data" / f"scope--{key}.json", {
        "topic": topic or None,
        "papers": sorted(paper_ids),
        "topics": sorted(topic_ids),
        "methods": sorted(method_ids),
        "resolution": engine.last_resolution,
    })


def topic_names(store: Any, limit: int) -> list[str]:
    """The demo topics first, then the most-studied topics in the corpus.

    A scope does not have to be a Topic node — the gap engine resolves phrases such as
    "AI Agents" across several topics — so the documented demo scopes are always
    precomputed, and the rest of the list is the corpus' most-connected topics.
    """
    by_degree = sorted(
        (node for node in store.nodes.values() if node.label == "Topic"),
        key=lambda node: -len(store.adjacency.get(node.id, []) if hasattr(store, "adjacency") else []),
    )
    names = [node.name for node in by_degree] or []
    ordered: list[str] = []
    for name in list(DEFAULT_TOPICS) + names:
        if name and name not in ordered:
            ordered.append(name)
    return ordered[: max(limit, len(DEFAULT_TOPICS))]


# ------------------------------------------------------------------ icons ----


def build_icons(icons: Path) -> dict[str, int]:
    """Deterministic PNG app icons, drawn with Pillow (no font needed).

    Pillow is a build-only dependency (`scripts/requirements-site.txt`). Without it the
    site still builds — any icons already in the output folder are kept, and the build
    says so instead of shipping a manifest that points at missing files.
    """
    try:
        from PIL import Image, ImageDraw, ImageFilter
    except ImportError:  # pragma: no cover - exercised by hand without Pillow
        existing = sorted(icons.glob("*.png")) if icons.is_dir() else []
        if not existing:
            raise SystemExit(
                "building the PWA icons needs Pillow:\n"
                "    pip install -r scripts/requirements-site.txt\n"
                "(the site itself builds without it, but the installable app needs its icons)"
            )
        log(f"  ! Pillow is not installed — keeping the {len(existing)} icon(s) already in {icons}")
        return {path.name: path.stat().st_size for path in existing}

    BG = (7, 11, 22, 255)
    CYAN = (97, 228, 255, 255)
    VIOLET = (167, 139, 250, 255)
    GREEN = (52, 211, 153, 255)
    AMBER = (251, 191, 36, 255)

    def draw_icon(size: int, scale: float = 0.78) -> Image.Image:
        ss = 2  # supersample for smooth edges
        w = size * ss
        img = Image.new("RGBA", (w, w), BG)
        glow = Image.new("RGBA", (w, w), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow)
        d = ImageDraw.Draw(img)
        c = w / 2
        r = w * 0.5 * scale
        # a small knowledge graph: hub + ring, edges drawn as glowing lines
        nodes = [
            (c, c, r * 0.16, CYAN),           # hub
            (c + r * 0.95, c - r * 0.55, r * 0.11, VIOLET),
            (c + r * 0.15, c + r * 0.98, r * 0.11, GREEN),
            (c - r * 0.92, c + r * 0.42, r * 0.10, AMBER),
            (c - r * 0.55, c - r * 0.92, r * 0.10, VIOLET),
            (c + r * 0.72, c + r * 0.72, r * 0.08, CYAN),
        ]
        for (x1, y1, _, col) in nodes[1:]:
            gd.line([c, c, x1, y1], fill=(col[0], col[1], col[2], 190), width=max(2, int(r * 0.045)))
        gd.line([nodes[1][0], nodes[1][1], nodes[5][0], nodes[5][1]], fill=(*VIOLET[:3], 130), width=max(2, int(r * 0.035)))
        gd.line([nodes[2][0], nodes[2][1], nodes[5][0], nodes[5][1]], fill=(*GREEN[:3], 130), width=max(2, int(r * 0.035)))
        img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(w * 0.02)))
        for (x, y, rad, col) in nodes:
            d.ellipse([x - rad, y - rad, x + rad, y + rad], fill=col)
        # subtle ring to read as a graph, not a blob
        d.ellipse([c - r * 1.12, c - r * 1.12, c + r * 1.12, c + r * 1.12],
                  outline=(97, 228, 255, 90), width=max(2, int(r * 0.02)))
        return img.resize((size, size), Image.LANCZOS)

    icons.mkdir(parents=True, exist_ok=True)
    written: dict[str, int] = {}
    plan = {
        "icon-192.png": 192,
        "icon-512.png": 512,
        "icon-maskable-512.png": 512,
        "apple-touch-icon.png": 180,
        "favicon-64.png": 64,
    }
    for name, size in plan.items():
        scale = 0.60 if "maskable" in name else 0.78
        image = draw_icon(size, scale=scale)
        path = icons / name
        image.save(path, "PNG", optimize=True)
        written[name] = path.stat().st_size
    return written


# ------------------------------------------------------------- assembled -----

MANIFEST = {
    "name": "NEXUS — AI Research Intelligence Graph",
    "short_name": "NEXUS",
    "description": (
        "A knowledge-graph research intelligence prototype: graph algorithms, GraphRAG, a "
        "transparent research-gap finder, claim-conflict detection and an explainable agent."
    ),
    "start_url": "./index.html",
    "scope": "./",
    "display": "standalone",
    "orientation": "any",
    "background_color": "#070b16",
    "theme_color": "#070b16",
    "categories": ["education", "productivity", "science"],
    "icons": [
        {"src": "icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
        {"src": "icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
}

SERVICE_WORKER = """/* NEXUS service worker — generated by scripts/build_site.py (no hand edits).
   Strategy: the app shell is precached; recorded data is served cache-first with a
   background refresh, so a second visit works offline and never shows a spinner. */
const VERSION = "%(version)s";
const SHELL = %(shell)s;
const CACHE = `nexus-shell-${VERSION}`;
const DATA = `nexus-data-${VERSION}`;

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => !k.endsWith(VERSION)).map((k) => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== location.origin) return;
  const isData = url.pathname.includes("/data/");

  event.respondWith((async () => {
    const cache = await caches.open(isData ? DATA : CACHE);
    const cached = await cache.match(request);
    if (cached) {
      if (isData) {
        event.waitUntil(fetch(request).then((fresh) => fresh.ok && cache.put(request, fresh.clone())).catch(() => {}));
      }
      return cached;
    }
    try {
      const fresh = await fetch(request);
      if (fresh.ok) cache.put(request, fresh.clone());
      return fresh;
    } catch (err) {
      if (request.mode === "navigate") return (await caches.match("./index.html")) || Response.error();
      throw err;
    }
  })());
});
"""

BANNER = """
<div class="static-strip" id="static-strip" role="note">
  <span><strong>Static snapshot.</strong> The engines ran at build time; recorded answers are served from
  <code>data/</code> and the graph queries are computed in your browser. The live server adds Neo4j,
  the LLM and the polyglot sidecars.</span>
  <span class="static-actions">
    <button class="btn small ghost" id="install-app" hidden>Install app</button>
    <a class="btn small ghost" href="https://github.com/authorsauravkushwaha/AI-Research-Intelligence-Graph" target="_blank" rel="noreferrer">GitHub ↗</a>
  </span>
</div>
"""


def patch_index(html: str, *, version: str, topics: list[str], questions: list[str]) -> str:
    """Make the shipped index.html work from a sub-path, as a PWA, with static data."""
    head_before = '<link rel="stylesheet" href="/assets/style.css" />'
    assert head_before in html
    head_after = (
        '<link rel="stylesheet" href="assets/style.css" />\n'
        '<link rel="manifest" href="manifest.webmanifest" />\n'
        '<meta name="theme-color" content="#070b16" />\n'
        '<meta name="color-scheme" content="dark" />\n'
        '<meta name="apple-mobile-web-app-capable" content="yes" />\n'
        '<meta name="apple-mobile-web-app-title" content="NEXUS" />\n'
        '<link rel="apple-touch-icon" href="icons/apple-touch-icon.png" />\n'
        '<link rel="icon" type="image/png" sizes="64x64" href="icons/favicon-64.png" />'
    )
    html = html.replace(head_before, head_after)

    # the 3D view imports three.js by absolute path: make it relative for sub-path hosting
    html = html.replace('from "/vendor/', 'from "./vendor/').replace('"/assets/', '"assets/')
    html = html.replace("<body>", "<body>\n" + BANNER, 1)

    bootstrap = (
        "<script>window.NEXUS_MODE = \"static\"; "
        f"window.NEXUS_BUILD = {{version: \"{version}\", topics: {json.dumps(topics)}, "
        f"questions: {json.dumps(questions)}}};</script>\n"
        '<script defer src="assets/site.js"></script>'
    )
    # app.js is an ES module; site.js installs the static data layer before it runs
    html = html.replace("</body>", bootstrap + "\n</body>")
    return html


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=ROOT / "site", help="output directory (default: ./site)")
    parser.add_argument("--topics", type=int, default=10, help="how many topics to precompute scopes for")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    out: Path = args.out.resolve()
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    def log(message: str) -> None:
        if not args.quiet:
            print(message)

    started = time.time()
    log(f"NEXUS static build → {out}")

    # 1 · the front end, copied verbatim (assets + vendored three.js)
    for name in ("assets", "vendor"):
        shutil.copytree(ROOT / "frontend" / name, out / name)
    (out / "assets" / "app.js").write_text(
        (ROOT / "frontend" / "assets" / "app.js").read_text(encoding="utf-8"), encoding="utf-8"
    )
    log(f"  · front end copied ({sum(1 for _ in out.rglob('*'))} files)")

    # 2 · record every API payload the UI can consume
    rec = Recorder(out)
    rec.get("/api/health", "health")
    rec.get("/api/dashboard", "dashboard")
    rec.get("/api/services", "services")
    for path, key in (
        ("/api/algorithms", "algorithms"),
        ("/api/safety", "safety"),
        ("/api/tools", "tools"),
        ("/api/mcp", "mcp"),
        ("/api/opportunity-score", "opportunity-score"),
        ("/api/communities", "communities"),
        ("/api/conflicts", "conflicts"),
        ("/api/predictions", "predictions"),
        ("/api/centrality", "centrality"),
    ):
        rec.get(path, key)
    log("  · meta + dashboard recorded")

    # The exported graph the browser computes over. It comes from the same store the API
    # serves, through the same accessors, so the metrics are the engine's own numbers.
    from backend.graph.factory import get_state

    store = get_state().store
    legend = rec.get("/api/graph", None)["legend"]
    write_text(out / "data" / "export-cypher.txt", rec.raw("/api/export/cypher?limit=300"))
    dataset = export_graph_dataset(legend, store=store)
    write_json(out / "data" / "graph.json", dataset)
    papers = sorted(
        (node for node in dataset["nodes"] if node["type"] == "Paper"),
        key=lambda node: -node["pagerank"],
    )
    log("  · graph exported for the browser (nodes with metrics + full properties, edges, communities)")

    # curated scopes: timeline + explorer + gaps + report per topic, plus the default
    topics = topic_names(store, args.topics)
    for topic in ["", *topics]:
        key = slug(topic)
        rec.get("/api/timeline", f"timeline--{key}", **({"topic": topic} if topic else {}))
        rec.get("/api/explorer", f"explorer--{key}", **({"topic": topic} if topic else {}))
        gaps = rec.post("/api/gaps", {"topic": topic or None, "top_k": 5}, f"gaps--{key}")
        for index in range(len(gaps.get("opportunities", []))):
            rec.post(f"/api/gaps/{index}/evidence", {"topic": topic or None, "top_k": 5},
                     f"gaps-evidence--{key}--{index}")
        record_scope(rec, store, topic, key)
        rec.recorded.append({"path": f"gap-engine.scope({topic!r})", "key": f"scope--{key}.json",
                             "bytes": 0, "kind": "computed"})
        markdown = rec.raw("/api/report/markdown", {"topic": topic or None, "top_k": 5})
        write_text(out / "data" / f"report--{key}.md", markdown)
        rec.post("/api/report", {"topic": topic or None, "top_k": 5}, f"report--{key}")
    log(f"  · {len(topics) + 1} scopes precomputed (timeline, explorer, gaps, evidence, report)")

    # agent answers, plus the recorded SSE stage trace for each
    for question in list(AGENT_QUESTIONS) + [""]:
        if not question:
            continue
        key = slug(question)
        rec.post("/api/agent", {"question": question, "depth": 2, "top_k": 12}, f"agent--{key}")
        stream = rec.raw("/api/agent/stream", {"question": question, "depth": 2, "top_k": 12})
        write_text(out / "data" / f"agent-stream--{key}.txt", stream)
    log(f"  · {len(AGENT_QUESTIONS)} agent questions recorded (answer + stage trace)")

    # The comparisons the checker re-runs against a live engine (kept out of the site:
    # the browser must not ship payloads it never reads).
    references = {
        "graph": [
            {"payload": {"focus": None, "seeds": None, "depth": 1}},
            {"payload": {"seeds": ["paper:2210.03629"], "depth": 1}},
            {"payload": {"focus": "community:0"}},
        ],
        "paths": [
            {"source": src, "target": dst} for src, dst in REFERENCE_PAIRS
        ],
        "papers": [row["id"] for row in papers[:8]],
        "nodes": ["paper:2210.03629", "topic:agent-memory", "method:chain-of-thought", "community:c14"],
        "neighbours": ["paper:2210.03629", "topic:agent-memory", "topic:tool-use"],
        "node_lists": [{"label": label, "limit": 25, "sort": "degree"} for label in ("Paper", "Topic", "Method")],
        "search": [{"q": q} for q in ("agent memory", "tool use", "evaluation")],
        "paper_lists": [
            {"q": q, "sort": sort, "limit": 25}
            for q, sort in (("", "pagerank"), ("memory", "betweenness"), ("agent", "degree"), ("", "year"))
        ],
        "explorer": [{"topic": t} for t in [None, "AI Agents", "Agent Memory", "Tool Use"]],
        "timeline": [{"topic": t} for t in [None, "AI Agents", "Tool Use"]],
    }

    html_source = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    shutil.copy2(ROOT / "frontend" / "assets" / "site.js", out / "assets" / "site.js")
    version = hashlib.sha256(
        (json.dumps(rec.recorded, sort_keys=True) + html_source).encode("utf-8")
    ).hexdigest()[:12]

    # 3 · PWA: icons, manifest, service worker
    if not (ROOT / "frontend" / "assets" / "site.js").exists():
        raise SystemExit("frontend/assets/site.js is missing — the static layer is required")
    icons = build_icons(out / "icons")
    shell = [
        "./", "index.html", "assets/style.css", "assets/app.js", "assets/site.js",
        "vendor/three.module.js", "vendor/OrbitControls.js", "manifest.webmanifest",
        "icons/icon-192.png", "icons/icon-512.png", "icons/favicon-64.png",
    ]
    write_json(out / "manifest.webmanifest", MANIFEST)
    write_text(out / "sw.js", SERVICE_WORKER % {"version": version, "shell": json.dumps(shell, indent=2)})
    write_text(out / ".nojekyll", "")
    log(f"  · PWA shell: {len(shell)} precached files · icons {sum(icons.values()) // 1024} KB · version {version}")

    # 4 · the page itself, patched for sub-path hosting and static mode
    write_text(out / "index.html", patch_index(
        html_source, version=version, topics=topics, questions=list(AGENT_QUESTIONS),
    ))

    # 5 · a build manifest so the site (and the tests) can state exactly what is inside
    health = rec.client.get("/api/health").json()
    counts = health["counts"]
    corpus_meta = rec.client.get("/api/dashboard").json().get("provenance", {}) or {}
    index = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "version": version,
        "build_seconds": round(time.time() - started, 1),
        "corpus_version": corpus_meta.get("corpus_version"),
        "counts": counts,
        "provenance": corpus_meta,
        "engines": {
            "analytics": "native/nexus-kernel (C++17)",
            "store": "in-process MemoryGraphStore at build time",
            "recorded_by": "scripts/build_site.py via FastAPI TestClient",
        },
        "precomputed": {
            "topics": topics,
            "questions": list(AGENT_QUESTIONS),
            "records": sorted({r["key"] for r in rec.recorded if r.get("key")}),
        },
        "computed_in_browser": [
            "graph queries (seeds, focus, depth, node/rel/type filters, expand, neighbours)",
            "shortest evidence path (BFS over the exported edges)",
            "paper list filters, sorting and paging; paper and node detail",
            "search over papers, topics, methods, datasets, authors and claims",
            "timeline counts per topic",
            "the NEXUS query DSL → parameterised Cypher (same rules as the Kotlin planner)",
            "agent answers for intents the recorded set does not cover (graph templates)",
        ],
        "static_note": (
            "This is a build-time snapshot of the NEXUS engines, published as a static site. "
            "Recorded payloads are the engines' own output; anything marked computed is derived "
            "from the exported graph in the browser. Gap scoring, community detection and "
            "prediction are not re-run here — run the app locally (or with docker compose) for "
            "live queries, and check /api/health there."
        ),
        "references": references,
        "records": rec.recorded,
        "bytes_written": rec.bytes,
    }
    write_json(out / "data" / "index.json", index)

    total = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    log(
        f"  · done in {index['build_seconds']}s · {total / 1024 / 1024:.1f} MB on disk "
        f"({len(rec.recorded)} recorded payloads)"
    )
    if not args.quiet:
        print("\n  serve it locally with:")
        print(f"    python -m http.server 8080 --directory {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
