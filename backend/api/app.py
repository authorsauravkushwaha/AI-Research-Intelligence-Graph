"""FastAPI application factory.

Layers, in order of what a request touches:

    browser ──▶ CORS ──▶ rate limit ──▶ router (/api/*) ──▶ engines
                                   └──▶ static frontend (SPA fallback)

Everything the front end needs is under `/api`; everything else is the static
single-page app in `frontend/`. When the front end has not been built, the API
still serves fully — with a small debugging page instead of a 404 — so a judge can
explore the OpenAPI docs at `/docs` from any state of the repository.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from backend.config import DEMO_DIR, REPO_ROOT, capability_flags, get_settings

log = logging.getLogger("nexus.api")


def _build_router():
    from backend.api import routes

    return routes.router


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="NEXUS — AI Research Intelligence Graph",
        description=(
            "Graph-native research intelligence: a knowledge graph of AI research, graph algorithms, "
            "GraphRAG retrieval, a transparent research-gap engine and an explainable research agent."
        ),
        version=settings.version,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    origins = list(settings.server.cors_origins) or ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,  # the demo uses no cookies and no credentials anywhere
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    limiter: dict[str, deque[float]] = defaultdict(deque)
    window = 60.0
    limit = settings.server.rate_limit_per_minute

    @app.middleware("http")
    async def rate_limit(request: Request, call_next: Callable):
        if request.url.path.startswith("/api") and limit > 0:
            client = request.client.host if request.client else "local"
            now = time.time()
            bucket = limiter[client]
            while bucket and now - bucket[0] > window:
                bucket.popleft()
            if len(bucket) >= limit:
                retry = max(1, int(window - (now - bucket[0])))
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "rate limit exceeded",
                        "detail": f"{limit} requests/minute per client; retry in {retry}s",
                    },
                    headers={"Retry-After": str(retry)},
                )
            bucket.append(now)
        started = time.time()
        response = await call_next(request)
        response.headers["X-NEXUS-Engine"] = settings.version
        response.headers["X-Response-Time-ms"] = str(int((time.time() - started) * 1000))
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal error",
                "detail": str(exc),
                "hint": "The engines degrade gracefully; check /api/health for the current backend status.",
            },
        )

    @app.get("/api/ping", include_in_schema=False, response_class=PlainTextResponse)
    def ping() -> str:
        """Liveness probe: plain, unauthenticated, no engine work. Always registered —
        `run.py`, the container healthcheck and CI poll it before the smoke sweep."""
        return "pong"

    app.include_router(_build_router())

    frontend = REPO_ROOT / "frontend"
    if (frontend / "index.html").exists():
        if (frontend / "assets").exists():
            app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(frontend / "index.html")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> Any:
            if path.startswith(("api", "docs", "redoc", "openapi.json")):
                return JSONResponse(status_code=404, content={"error": "not found", "path": path})
            candidate = frontend / path
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(frontend / "index.html")
    else:

        @app.get("/", include_in_schema=False, response_class=HTMLResponse)
        def placeholder() -> str:
            flags = capability_flags()
            return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>NEXUS API</title>
<style>body{{font-family:ui-monospace,Menlo,monospace;background:#0b0f14;color:#d7e3ef;padding:3rem;line-height:1.6}}
code{{background:#16202c;padding:.15rem .4rem;border-radius:4px}}a{{color:#5cc8ff}}</style></head>
<body><h1>NEXUS — AI Research Intelligence Graph</h1>
<p>The API is running (v{settings.version}). The 3D front end has not been built yet in this checkout.</p>
<ul>
<li><a href="/docs">Interactive API docs (OpenAPI)</a></li>
<li><a href="/api/health">/api/health</a> — engine status and capability flags</li>
<li><a href="/api/dashboard">/api/dashboard</a> — everything the Home dashboard renders</li>
</ul>
<p>Neo4j configured: <code>{flags.get('neo4j_configured')}</code> ·
LLM configured: <code>{flags.get('llm_configured')}</code> ·
demo mode: <code>{settings.server.demo_mode}</code></p>
<p>Run <code>python run.py</code> to serve the API, or <code>uvicorn backend.api.app:create_app --factory</code>.</p>
</body></html>"""

    @app.on_event("startup")
    async def _startup() -> None:
        from backend.graph.factory import get_state

        state = get_state()
        log.info(
            "NEXUS %s up — engine=%s (requested=%s, degraded=%s), corpus=%s",
            settings.version,
            state.backend,
            state.requested_backend,
            state.degraded,
            DEMO_DIR / "corpus.json",
        )

    return app


def __getattr__(name: str):
    """Lazily expose a module-level ``app``.

    This keeps both invocation styles working::

        python run.py                                  # the documented launcher
        uvicorn backend.api.app:create_app --factory   # explicit factory
        uvicorn backend.api.app:app                    # module-level attribute
    """
    if name == "app":
        return create_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
