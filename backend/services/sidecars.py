"""Polyglot sidecar status (§37 / §40).

NEXUS is built from several languages, each given a job it is good at, and every one
of them is **optional**: when a sidecar is not running the Python implementation
behind the same interface answers instead, and the response says which engine ran.

This module reports that honestly — for each sidecar: its URL, whether it is
configured, whether it is *reachable right now*, the engine that would answer, and
the Python fallback that takes over if it is not. Nothing here is required for the
demo; it exists so "the JVM planner is live" is an observable fact in the UI rather
than a claim in a README.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from backend.config import get_settings


@dataclass(frozen=True)
class SidecarSpec:
    name: str
    language: str
    env: str
    default_url: str
    engine: str
    fallback: str
    role: str


SPECS: tuple[SidecarSpec, ...] = (
    SidecarSpec(
        "planner", "Kotlin (JVM)", "NEXUS_PLANNER_URL", "http://127.0.0.1:8092",
        "nexus-kotlin-planner", "backend/services/planner.py::python_plan",
        "turns the NEXUS query DSL into parameterised Cypher, with explanation and cost estimate",
    ),
    SidecarSpec(
        "ingest", "Go", "NEXUS_INGEST_URL", "http://127.0.0.1:8090",
        "nexus-ingest-go", "backend/ingestion/",
        "corpus fetch, entity extraction and local embeddings",
    ),
    SidecarSpec(
        "export", "C# (.NET)", "NEXUS_EXPORT_URL", "http://127.0.0.1:8091",
        "nexus-export-dotnet", "backend/services/export.py",
        "GraphML / CSV / markdown report export",
    ),
    SidecarSpec(
        "claims", "Ruby", "NEXUS_CLAIM_URL", "",
        "nexus-claim-resolver-ruby", "backend/ingestion/claims.py",
        "claim parsing, negation handling and opposition scoring",
    ),
)


def _configured_url(spec: SidecarSpec) -> str:
    settings = get_settings().embeddings
    return {
        "planner": settings.planner_service,
        "ingest": settings.ingest_service,
        "export": settings.export_service,
        "claims": settings.claim_service,
    }.get(spec.name, spec.default_url)


def probe(spec: SidecarSpec, timeout: float = 0.8) -> dict[str, Any]:
    """One sidecar's state. Never raises: an unreachable service is a normal answer."""
    url = _configured_url(spec) or spec.default_url
    entry: dict[str, Any] = {
        "name": spec.name,
        "language": spec.language,
        "role": spec.role,
        "env": spec.env,
        "url": url,
        "configured": bool(_configured_url(spec)),
        "reachable": False,
        "engine": spec.engine,
        "fallback": spec.fallback,
        "reported": None,
    }
    if not _configured_url(spec):
        entry["note"] = f"{spec.env} is unset — the Python implementation answers (by design, not by failure)"
        return entry
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url.rstrip("/") + "/health")
        if resp.status_code == 200:
            payload = resp.json()
            entry["reachable"] = True
            entry["reported"] = {
                key: payload[key]
                for key in ("service", "engine", "version", "language", "runtime", "ruby", "go")
                if key in payload
            }
            entry["engine"] = str(payload.get("engine") or payload.get("service") or spec.engine)
    except Exception as exc:  # noqa: BLE001 - unreachable is a state, not an error
        entry["note"] = f"{type(exc).__name__}: {exc}"[:160]
    return entry


def status(timeout: float = 0.8) -> dict[str, Any]:
    """State of every sidecar, plus what it means for a caller."""
    sidecars = [probe(spec, timeout=timeout) for spec in SPECS]
    live = [s for s in sidecars if s["reachable"]]
    return {
        "sidecars": sidecars,
        "live": len(live),
        "total": len(sidecars),
        "summary": (
            f"{len(live)} of {len(sidecars)} polyglot sidecars are reachable; "
            "the rest are answered by the Python implementation behind the same interface"
        ),
        "note": (
            "Sidecars are optional by design. Enabling one (see .env.example) changes which "
            "engine answers, never whether the demo works — and every response names its engine."
        ),
        "engines": {
            "in_process": "backend/store/memory_store.py",
            "neo4j": "backend/graph/neo4j_store.py",
            "kernel": "native/nexus-kernel (C++17)",
        },
    }
