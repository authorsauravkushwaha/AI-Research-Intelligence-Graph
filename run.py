#!/usr/bin/env python3
"""NEXUS launcher.

    python run.py                # serve API + front end on 0.0.0.0:8000
    python run.py --port 9000    # custom port
    python run.py --reload       # development
    python run.py --check        # verify the installation and exit

The launcher refuses to start with a broken installation and tells you exactly
which piece is missing (corpus, native kernel, dependencies) instead of failing at
request time.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def preflight() -> tuple[bool, list[str]]:
    problems: list[str] = []
    notes: list[str] = []

    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError as exc:
        problems.append(f"missing dependency: {exc} — run `pip install -r backend/requirements.txt`")

    corpus = ROOT / "data" / "demo" / "corpus.json"
    if not corpus.exists():
        problems.append(f"corpus not found at {corpus} — run `python scripts/build_corpus.py`")

    native = ROOT / "native" / "build" / "bin" / "nexus-kernel"
    if not native.exists():
        notes.append("native kernel not built — run `cd native && make -j2` (analytics will use the Python fallbacks)")

    try:
        from backend.config import capability_flags

        flags = capability_flags()
        notes.append(
            "neo4j: {neo4j} · llm: {llm} · embeddings: {emb}".format(
                neo4j="configured" if flags.get("neo4j_configured") else "not configured (in-process engine)",
                llm="configured" if flags.get("llm_configured") else "not configured (graph-template answers)",
                emb="configured" if flags.get("embeddings_provider") else "local lexical baseline",
            )
        )
    except Exception as exc:  # noqa: BLE001 - preflight must never crash
        problems.append(f"backend import failed: {exc}")

    for note in notes:
        print(f"  · {note}")
    return not problems, problems


def main() -> int:
    parser = argparse.ArgumentParser(description="NEXUS — AI Research Intelligence Graph")
    parser.add_argument("--host", default=None, help="bind address (default from NEXUS_HOST)")
    parser.add_argument("--port", type=int, default=None, help="port (default from NEXUS_PORT)")
    parser.add_argument("--reload", action="store_true", help="auto-reload on source changes")
    parser.add_argument("--check", action="store_true", help="verify the installation and exit")
    args = parser.parse_args()

    print("NEXUS — AI Research Intelligence Graph")
    ok, problems = preflight()
    if problems:
        for problem in problems:
            print(f"  ! {problem}")
    if not ok:
        return 1
    if args.check:
        print("  ✓ installation looks good")
        return 0

    from backend.config import get_settings

    settings = get_settings()
    host = args.host or settings.server.host
    port = args.port or settings.server.port

    import uvicorn

    print(f"  → http://{host if host != '0.0.0.0' else 'localhost'}:{port}  (docs at /docs)")
    uvicorn.run(
        "backend.api.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=args.reload,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
