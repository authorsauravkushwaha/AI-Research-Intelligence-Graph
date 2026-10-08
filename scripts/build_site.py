#!/usr/bin/env python3
"""Build the static NEXUS site — the website and installable app published on GitHub Pages.

    python scripts/build_site.py                 # writes ./site
    python scripts/build_site.py --out site

Why this exists
---------------
GitHub Pages serves files; NEXUS is a FastAPI application with a knowledge graph behind it.
So the published site is not a rewrite of the demo and not a mock-up: this script drives the
**real application** in-process (`TestClient(create_app())` — the same store, engines,
GraphRAG pipeline and agent the API serves), records what the expensive engines answer, and
ships the graph itself so the cheap views can be recomputed live in the browser.

What lands in the output folder
-------------------------------
  index.html, assets/, vendor/    the front end, with absolute paths rewritten to relative ones
  assets/site.js                  the static data layer: it answers /api/* from ./data/
  data/graph.json                 the full corpus export (nodes, edges, metrics) — the app's graph
  data/papers.json                every paper row the Paper Explorer can page through
  data/<recorded>.json            engine output recorded at build time (see RECORDED below)
  data/index.json                 build manifest: version, corpus, counts, every file + size
  manifest.webmanifest, sw.js     makes it an installable, offline-capable app (PWA)
  icons/                          app icons drawn here (Pillow), including a maskable one
  .nojekyll                       tells GitHub Pages to serve the files as-is

RECORDED (engine output that cannot be recomputed in a browser):
  gaps--<topic>.json              the gap engine's scored opportunities (the killer feature)
  report--<topic>.json/.md        the opportunity report, both renderings
  agent--<question>.json          the research agent's answer *with its explainability block*
  agent-stream--<question>.txt    the recorded SSE stage trace the UI replays
  explorer--<scope>.json          analytics for the demo scopes
  timeline--<topic>.json          per-topic publishing timelines
  export-cypher.txt               the Cypher that recreates this graph in Neo4j

COMPUTED IN THE BROWSER (documented in docs/DEPLOY.md):
  /api/graph, /api/graph/expand, /api/graph/neighbours, /api/graph/path, /api/nodes,
  /api/papers, /api/search, /api/timeline, /api/explorer, /api/plan (a live JS planner).

Every recorded file is a verbatim API response; `scripts/check_site_data.mjs` replays every
recorded answer through the shipped browser layer and diffs the two field by field, so the
live engine, so the published site cannot drift from the application.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FRONTEND = ROOT / "frontend"

# The scopes the published demo precomputes. They are the ones the README, the demo script
# and the UI's own examples use; anything else falls back to an honest empty state that
# names the command to run for a live answer.
DEMO_TOPICS = [
    "AI Agents",
    "Tool Use",
    "Multi-Agent Systems",
    "AI Safety",
    "Evaluation",
    "Agent Memory",
]

# Questions the agent answers at build time (docs/DEMO_SCRIPT.md + the UI's follow-ups).
DEMO_QUESTIONS = [
    "Which claims contradict each other about agent memory?",
    "What are the most important research gaps in AI agents?",
    "What should I read first?",
    "Which papers bridge AI Agents and Tool Use?",
    "What are the most important papers?",
    "Who are the most central authors?",
    "Which communities exist in this corpus?",
    "How do I tell a prediction from a fact?",

    # GraphRAG follow-ups (multi-hop: the reason vector search alone is not enough)
    "What connects Multi-Agent Systems and Evaluation?",
    "Which topics does ReAct connect?",
]

# DSL queries whose real plan is recorded next to the page, so the browser planner
# (assets/site.js) can be compared against the server's Kotlin-backed planner.
PLAN_QUERIES = [
    "topic:\"AI Agents\" type in (Paper, Method) limit 40",
    "topic:\"Tool Use\" year >= 2023 sort by pagerank limit 25",
    "type in (Paper) text contains memory limit 10",
    "community 3 type in (Paper) limit 10",
    "rel in (CONTRADICTS) limit 10",
    "topic:\"AI Agents\" type in (Paper) depth 2 limit 30",
]

REFERENCE_PAPERS = [
    "paper:2210.03629",  # ReAct — the paper the demo clicks first
    "paper:2303.11366",  # Reflexion
    "paper:2005.14165",  # GPT-3
    "paper:2201.11903",  # chain-of-thought
    "paper:2501.04227",
]

REFERENCE_NODES = [
    "topic:agent-memory",
    "topic:tool-use",
    "method:chain-of-thought",
    "author:noah-shinn",
    "dataset:gaia",
]

REFERENCE_SEARCHES = ["agent memory", "tool use", "evaluation", "chain-of-thought"]


# --------------------------------------------------------------------------- util


def slug(text: str | None) -> str:
    """Filesystem-safe key for a topic or question (used in recorded file names)."""
    if not text:
        return "default"
    cleaned = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return cleaned[:60] or "default"


def log(message: str) -> None:
    print(message, flush=True)


class Recorder:
    """Writes recorded API responses and remembers what was written where."""

    def __init__(self, data_dir: Path) -> None:
        self.dir = data_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.files: dict[str, dict[str, Any]] = {}

    def json(self, name: str, payload: Any, kind: str, source: str) -> None:
        path = self.dir / name
        path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.files[name] = {
            "kind": kind,
            "source": source,
            "bytes": path.stat().st_size,
        }

    def text(self, name: str, payload: str, kind: str, source: str) -> None:
        path = self.dir / name
        path.write_text(payload if payload.endswith("\n") else payload + "\n")
        self.files[name] = {"kind": kind, "source": source, "bytes": path.stat().st_size}

    def blob(self, name: str, payload: bytes, kind: str, source: str) -> None:
        path = self.dir / name
        path.write_bytes(payload)
        self.files[name] = {"kind": kind, "source": source, "bytes": path.stat().st_size}


# ------------------------------------------------------------------------- icons


def build_icons(icons: Path) -> list[str]:
    """Draw the app icons with Pillow (no font file needed, no binary assets in git)."""
    from PIL import Image, ImageDraw

    icons.mkdir(parents=True, exist_ok=True)
    sizes = {"icon-192.png": (192, 0.86), "icon-512.png": (512, 0.86), "icon-maskable-512.png": (512, 0.66),
             "apple-touch-icon.png": (180, 0.86), "favicon-64.png": (64, 0.86)}
    written = []
    for name, (size, extent) in sizes.items():
        scale = 4  # supersample, then downscale: cheap antialiasing
        w = size * scale
        img = Image.new("RGBA", (w, w), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # navy tile with a soft gradient ring (the header mark, at app-icon scale)
        draw.rounded_rectangle([0, 0, w - 1, w - 1], radius=int(w * 0.22), fill=(10, 15, 30, 255))
        cx = cy = w / 2
        ring = w * 0.5 * extent
        for i in range(w):
            t = i / max(1, w - 1)
            colour = (int(97 + t * 70), int(228 - t * 89), 255, 255)
            draw.arc([cx - ring, cy - ring, cx + ring, cy + ring], 200 + i % 1, 340, fill=colour,
                     width=max(2, int(w * 0.022)))
        nodes = [
            (cx, cy, w * 0.055, (97, 228, 255, 255)),                       # centre
            (cx + ring * 0.92, cy - ring * 0.72, w * 0.030, (167, 139, 250, 255)),
            (cx - ring * 0.95, cy - ring * 0.55, w * 0.028, (52, 211, 153, 255)),
            (cx - ring * 0.80, cy + ring * 0.78, w * 0.030, (251, 191, 36, 255)),
            (cx + ring * 0.88, cy + ring * 0.70, w * 0.026, (244, 114, 182, 255)),
        ]
        for nx, ny, r, colour in nodes:
            draw.line([cx, cy, nx, ny], fill=(120, 170, 210, 210), width=max(2, int(w * 0.012)))
        for nx, ny, r, colour in nodes:
            draw.ellipse([nx - r, ny - r, nx + r, ny + r], fill=colour)
        img = img.resize((size, size), Image.LANCZOS)
        img.save(icons / name, "PNG", optimize=True)
        written.append(name)
    return written


# ------------------------------------------------------------------- PWA shell


MANIFEST = {
    "name": "NEXUS — AI Research Intelligence Graph",
    "short_name": "NEXUS",
    "description": (
        "A graph-native research intelligence prototype: knowledge graph, graph algorithms, "
        "GraphRAG, a transparent gap finder and an explainable research agent."
    ),
    "start_url": "./index.html",
    "scope": "./",
    "display": "standalone",
    "display_override": ["standalone", "minimal-ui"],
    "orientation": "any",
    "background_color": "#070b16",
    "theme_color": "#070b16",
    "categories": ["education", "productivity", "science"],
    "icons": [
        {"src": "icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
        {"src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        {"src": "icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
    ],
}


SERVICE_WORKER = """/* NEXUS service worker — generated by scripts/build_site.py, do not edit by hand.
   Strategy: the app shell is precached at install time; recorded data is served from the
   cache with a background refresh; navigations are network-first with an offline fallback,
   so the installed app opens with no connection instead of showing the browser error page. */
const VERSION = "{version}";
const SHELL = {shell};
const SHELL_CACHE = `nexus-shell-${{VERSION}}`;
const DATA_CACHE = `nexus-data-${{VERSION}}`;

self.addEventListener("install", (event) => {{
  event.waitUntil((async () => {{
    const cache = await caches.open(SHELL_CACHE);
    await cache.addAll(SHELL.map((path) => new Request(path, {{ cache: "reload" }})));
    await self.skipWaiting();
  }})());
}});

self.addEventListener("activate", (event) => {{
  event.waitUntil((async () => {{
    const keep = new Set([SHELL_CACHE, DATA_CACHE]);
    for (const key of await caches.keys()) {{
      if (!keep.has(key)) await caches.delete(key);
    }}
    await self.clients.claim();
  }})());
}});

self.addEventListener("message", (event) => {{
  if (event.data === "skip-waiting") self.skipWaiting();
}});

self.addEventListener("fetch", (event) => {{
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // The UI's API calls are answered inside the page by assets/site.js, so the only data
  // requests that reach the network are the snapshot files under ./data/.
  const isData = url.pathname.includes("/data/");
  if (isData) {{
    event.respondWith((async () => {{
      const cache = await caches.open(DATA_CACHE);
      const cached = await cache.match(request, {{ ignoreSearch: true }});
      const network = fetch(request)
        .then((response) => {{
          if (response && response.ok) cache.put(request, response.clone());
          return response;
        }})
        .catch(() => null);
      return cached || (await network) || Response.error();
    }})());
    return;
  }}

  if (request.mode === "navigate") {{
    event.respondWith((async () => {{
      try {{
        const response = await fetch(request);
        const cache = await caches.open(SHELL_CACHE);
        cache.put("./index.html", response.clone());
        return response;
      }} catch (err) {{
        const cache = await caches.open(SHELL_CACHE);
        return (await cache.match("./index.html")) || (await cache.match("./")) || Response.error();
      }}
    }})());
    return;
  }}

  event.respondWith((async () => {{
    const cache = await caches.open(SHELL_CACHE);
    const cached = await cache.match(request, {{ ignoreSearch: true }});
    if (cached) return cached;
    try {{
      const response = await fetch(request);
      if (response && response.ok) cache.put(request, response.clone());
      return response;
    }} catch (err) {{
      return Response.error();
    }}
  }})());
}});
"""


BANNER = """
<div id="static-banner" class="banner static-banner" role="status">
  <strong>Static snapshot.</strong>
  This is the published build of NEXUS: the graph, papers, search and the DSL planner run live
  in your browser, while gap scoring, opportunity reports and agent answers were computed by
  the real engines at build time ({built}). It answers for the curated corpus only —
  <a href="https://github.com/authorsauravkushwaha/AI-Research-Intelligence-Graph#readme"
     target="_blank" rel="noreferrer">run it locally</a> for live queries.
  <button type="button" id="static-banner-close" aria-label="Dismiss">×</button>
</div>
"""


def patch_index(html: str, *, version: str, built: str, topics: list[str], questions: list[str]) -> str:
    """Rewrite the served index.html for a static, sub-path-safe deployment."""
    # GitHub Pages serves the project under /<repo>/ — every path must be relative.
    html = html.replace('href="/assets/', 'href="assets/')
    html = html.replace('src="/assets/', 'src="assets/')
    html = html.replace('href="/vendor/', 'href="vendor/')
    html = html.replace('src="/vendor/', 'src="vendor/')
    html = html.replace('href="/icons/', 'href="icons/')
    html = html.replace('src="/icons/', 'src="icons/')
    html = html.replace('href="/manifest.webmanifest"', 'href="manifest.webmanifest"')
    html = html.replace('href="/api/health"', 'href="data/health.json"')
    html = html.replace('href="/docs"', 'href="https://github.com/authorsauravkushwaha/AI-Research-Intelligence-Graph#api"')

    # app.js imports "/vendor/three.module.js" (absolute) — make the module graph relative too
    html = html.replace('<script type="module" src="assets/app.js"></script>',
                        '<script type="module" src="assets/app.js"></script>')

    head_extra = (
        '  <link rel="manifest" href="manifest.webmanifest" />\n'
        '  <meta name="theme-color" content="#070b16" />\n'
        '  <meta name="color-scheme" content="dark" />\n'
        '  <link rel="apple-touch-icon" href="icons/apple-touch-icon.png" />\n'
        '  <link rel="icon" type="image/png" sizes="64x64" href="icons/favicon-64.png" />\n'
        '  <meta property="og:title" content="NEXUS — AI Research Intelligence Graph" />\n'
        '  <meta property="og:description" content="A graph-native research intelligence prototype: '
        'knowledge graph, graph algorithms, GraphRAG, a transparent research gap finder and an '
        'explainable research agent." />\n'
        '  <meta property="og:type" content="website" />\n'
    )
    html = html.replace("</title>\n", "</title>\n" + head_extra, 1)

    # the snapshot descriptor the header/status and the JS data layer read
    boot = (
        '  <script id="nexus-snapshot" type="application/json">'
        + json.dumps({"mode": "static", "version": version, "built": built,
                      "topics": topics, "questions": questions,
                      "note": "Recorded from the live application at build time; the graph, "
                              "papers, search and planner are recomputed in the browser."})
        + "</script>\n"
    )
    html = html.replace('<script type="module" src="assets/app.js"></script>',
                        boot + '<script src="assets/site.js"></script>\n'
                               '<script type="module" src="assets/app.js"></script>', 1)

    # an install affordance + the honest banner
    html = html.replace('  <div class="status" id="status" title="Engine status">',
                        '  <button type="button" id="install-app" class="install" hidden>Install app</button>\n'
                        '  <div class="status" id="status" title="Engine status">', 1)
    html = html.replace('<main>', BANNER + '\n<main>', 1)
    html = html.replace("${VERSION}", version).replace("{built}", built)
    return html


# -------------------------------------------------------------------------- build


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the static NEXUS website + app")
    parser.add_argument("--out", default=str(ROOT / "site"), help="output directory (default: ./site)")
    parser.add_argument("--keep", action="store_true", help="do not delete the output directory first")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    out = Path(args.out).resolve()
    started = datetime.now(timezone.utc)

    # The builder issues ~800 calls into the app in a couple of minutes (one per node, plus
    # the recordings). That is one client, not a load test, so the per-IP limiter is off for
    # the build only — the served site never runs behind FastAPI at all.
    os.environ.setdefault("NEXUS_RATE_LIMIT_PER_MINUTE", "0")

    from fastapi.testclient import TestClient  # imported late: --help must not need deps

    from backend.api.app import create_app

    if out.exists() and not args.keep:
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    client = TestClient(create_app())

    def api_get(path: str, **params: Any) -> Any:
        response = client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    def api_post(path: str, body: dict[str, Any]) -> Any:
        response = client.post(path, json=body)
        response.raise_for_status()
        return response.json()

    def api_text(path: str, body: dict[str, Any] | None = None, **params: Any) -> str:
        response = client.post(path, json=body) if body is not None else client.get(path, params=params)
        response.raise_for_status()
        return response.text

    # ---------------------------------------------------------------- the front end
    shutil.copytree(FRONTEND / "assets", out / "assets")
    shutil.copytree(FRONTEND / "vendor", out / "vendor")
    (out / "data").mkdir(parents=True, exist_ok=True)
    rec = Recorder(out / "data")

    # --------------------------------------------------------------- graph snapshot
    # The browser needs properties (title, abstract, claim text, year, url) *and* metrics for
    # every node, so each node's own detail response is folded into one dataset. Every field
    # comes from the API: identities and edges from the export, properties and metrics from
    # /api/nodes/<id>, the community profiles from /api/communities.
    export = api_post("/api/export", {"format": "json", "limit": 5000})
    communities_payload = api_get("/api/communities")
    # The default graph view is recorded here rather than later: its `legend` is the API's own
    # (node colours, the full relationship list, the associative/predicted relationships), and
    # the browser needs exactly those values to colour and filter the graph.
    graph_default = api_post("/api/graph", {})
    graph = {
        "format": "nexus-static-graph",
        "engine": "memory-store@build",
        "counts": export.get("counts", {}),
        "provenance": export.get("provenance", {}),
        "communities": communities_payload.get("communities", []),
        "legend": graph_default["legend"],
        "nodes": [],
        "edges": export.get("edges", []),
    }
    empty_props = 0
    for index, node in enumerate(export.get("nodes", []), start=1):
        detail = api_get("/api/nodes/" + node["id"])
        metrics = detail.get("metrics", {})
        props = detail.get("properties", {})
        if not props:
            empty_props += 1
        graph["nodes"].append({
            "id": detail["id"],
            "label": detail["label"],
            "type": detail["type"],
            "color": node.get("color") or graph_default["legend"]["node_colors"].get(detail["type"]),
            "props": props,
            "pagerank": metrics.get("pagerank", 0.0),
            "betweenness": metrics.get("betweenness", 0.0),
            "degree": metrics.get("degree", 0),
            "community": metrics.get("community", -1),
        })
        if index % 150 == 0:
            log(f"    … {index} nodes folded in")
    rec.json("graph.json", graph, "graph",
             "POST /api/export + GET /api/nodes/<id> for every node (properties + metrics)")

    papers: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = api_get("/api/papers", limit=200, offset=offset, sort="pagerank")
        papers.extend(page.get("items", []))
        offset += 200
        if offset >= page.get("total", 0) or not page.get("items"):
            break
    rec.json("papers.json", {"count": len(papers), "items": papers}, "corpus",
             "GET /api/papers (all pages, sorted by pagerank)")

    # ------------------------------------------------------------ recorded payloads
    for name, path in [
        ("health.json", "/api/health"),
        ("dashboard.json", "/api/dashboard"),
        ("services.json", "/api/services"),
        ("algorithms.json", "/api/algorithms"),
        ("safety.json", "/api/safety"),
        ("tools.json", "/api/tools"),
        ("mcp.json", "/api/mcp"),
        ("opportunity-score.json", "/api/opportunity-score"),
        ("conflicts.json", "/api/conflicts"),
        ("predictions.json", "/api/predictions"),
        ("centrality.json", "/api/centrality"),
    ]:
        rec.json(name, api_get(path), "recorded", f"GET {path}")

    rec.json("communities.json", communities_payload, "recorded", "GET /api/communities")
    rec.json("timeline.json", api_get("/api/timeline"), "recorded", "GET /api/timeline")
    rec.json("explorer--default.json", api_get("/api/explorer"), "recorded", "GET /api/explorer")
    rec.json("graph--default.json", graph_default, "reference", "POST /api/graph {}")

    for topic in DEMO_TOPICS:
        key = slug(topic)
        rec.json(f"timeline--{key}.json", api_get("/api/timeline", topic=topic), "recorded",
                 f"GET /api/timeline?topic={topic}")
        rec.json(f"explorer--{key}.json", api_get("/api/explorer", topic=topic), "recorded",
                 f"GET /api/explorer?topic={topic}")
        gaps = api_post("/api/gaps", {"topic": topic, "top_k": 5})
        rec.json(f"gaps--{key}.json", gaps, "recorded", f"POST /api/gaps {{topic:{topic}, top_k:5}}")
        report = api_post("/api/report", {"topic": topic})
        rec.json(f"report--{key}.json", report, "recorded", f"POST /api/report {{topic:{topic}}}")
        rec.text(f"report--{key}.md", api_text("/api/report/markdown", {"topic": topic}),
                 "recorded", f"POST /api/report/markdown {{topic:{topic}}}")

    rec.json("gaps--default.json", api_post("/api/gaps", {"topic": None, "top_k": 5}),
             "recorded", "POST /api/gaps {topic:null, top_k:5}")
    rec.json("report--default.json", api_post("/api/report", {"topic": None}),
             "recorded", "POST /api/report {topic:null}")
    rec.text("report--default.md", api_text("/api/report/markdown", {"topic": None}),
             "recorded", "POST /api/report/markdown {topic:null}")

    for question in DEMO_QUESTIONS:
        key = slug(question)
        rec.json(f"agent--{key}.json", api_post("/api/agent", {"question": question}),
                 "recorded", f"POST /api/agent {{question:{question!r}}}")
        rec.text(f"agent-stream--{key}.txt",
                 api_text("/api/agent/stream", {"question": question}),
                 "recorded", "POST /api/agent/stream (SSE trace)")

    for query in PLAN_QUERIES:
        rec.json(f"plan--{slug(query)}.json", api_post("/api/plan", {"query": query}),
                 "reference", f"POST /api/plan {{query:{query!r}}}")

    rec.text("export-cypher.txt", api_text("/api/export/cypher", limit=300),
             "recorded", "GET /api/export/cypher?limit=300")

    # A few individual references (also what the node checker replays).
    for paper_id in REFERENCE_PAPERS:
        if paper_id in {n["id"] for n in graph["nodes"]}:
            rec.json(f"paper--{slug(paper_id)}.json", api_get(f"/api/papers/{paper_id}"),
                     "reference", f"GET /api/papers/{paper_id}")
    for node_id in REFERENCE_NODES:
        if node_id in {n["id"] for n in graph["nodes"]}:
            rec.json(f"node--{slug(node_id)}.json", api_get(f"/api/nodes/{node_id}"),
                     "reference", f"GET /api/nodes/{node_id}")
    for query in REFERENCE_SEARCHES:
        rec.json(f"search--{slug(query)}.json", api_get("/api/search", q=query),
                 "reference", f"GET /api/search?q={query}")

    # ------------------------------------------------------------------ PWA shell
    icons = build_icons(out / "icons")
    (out / "manifest.webmanifest").write_text(json.dumps(MANIFEST, indent=2) + "\n")
    (out / ".nojekyll").write_text("")

    # --------------------------------------------------------------------- index
    built = started.strftime("%Y-%m-%d %H:%M UTC")
    version = started.strftime("%Y%m%d%H%M%S")
    html = patch_index((FRONTEND / "index.html").read_text(), version=version, built=built,
                       topics=DEMO_TOPICS, questions=DEMO_QUESTIONS)
    (out / "index.html").write_text(html)

    shell = [
        "./", "./index.html", "./manifest.webmanifest", "./assets/style.css", "./assets/app.js",
        "./assets/site.js", "./vendor/three.module.js", "./vendor/OrbitControls.js",
        "./icons/icon-192.png", "./icons/icon-512.png", "./icons/icon-maskable-512.png",
    ]
    (out / "sw.js").write_text(SERVICE_WORKER.format(version=version, shell=json.dumps(shell, indent=2)))

    # ------------------------------------------------------------------ manifest
    counts = graph.get("counts", {})
    index = {
        "site": "NEXUS — static snapshot",
        "mode": "static",
        "version": version,
        "built": built,
        "build_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 1),
        "corpus_version": graph.get("corpus_version") or graph.get("version"),
        "counts": counts,
        "engines": api_get("/api/health").get("capabilities", {}),
        "precomputed": {
            "topics": DEMO_TOPICS,
            "questions": DEMO_QUESTIONS,
            "plan_queries": PLAN_QUERIES,
            "note": "Scopes outside this list return a documented empty state naming the "
                    "command that answers them live — the snapshot never invents a score.",
        },
        "computed_in_browser": [
            "/api/graph", "/api/graph/expand", "/api/graph/neighbours", "/api/graph/path",
            "/api/nodes", "/api/papers", "/api/search", "/api/timeline", "/api/explorer",
            "/api/plan",
        ],
        "icons": icons,
        "graph": {"nodes": len(graph["nodes"]), "edges": len(graph["edges"]),
                  "nodes_without_properties": empty_props},
        "files": rec.files,
    }
    (out / "data" / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n")

    total = sum(path.stat().st_size for path in out.rglob("*") if path.is_file())
    if not args.quiet:
        log(f"  · wrote {out}")
        log(f"  · {total / 1e6:.1f} MB · {len(list(out.rglob('*')))} entries")
        log(f"  · graph: {counts.get('nodes')} nodes / {counts.get('edges')} edges · "
            f"{len(papers)} papers · {len(rec.files)} recorded payloads")
        log(f"  · build version {version} · {index['build_seconds']}s")
        log("")
        log("  serve it locally with:  python -m http.server 8080 --directory site")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
