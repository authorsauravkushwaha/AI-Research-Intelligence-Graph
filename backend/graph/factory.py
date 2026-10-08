"""Selects the graph store, with an explicit and honest fallback.

Order of preference:

1. **Neo4j** — when `NEO4J_URI` + `NEO4J_PASSWORD` are configured and the instance
   is reachable (this is the store of record; GDS is used when installed),
2. **in-process engine** — the same schema, the same algorithms and the same API,
   so the demo never shows a blank screen.

The decision, the reason and any warning are exposed through `/api/health` and
rendered in the UI status bar: a judge always knows which engine answered.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from backend.config import get_settings
from backend.store.memory_store import MemoryGraphStore

log = logging.getLogger("nexus.store")


@dataclass
class StoreState:
    store: Any
    backend: str
    requested_backend: str
    degraded: bool = False
    reason: str = ""
    warnings: list[str] = field(default_factory=list)

    def status(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "requested_backend": self.requested_backend,
            "degraded": self.degraded,
            "reason": self.reason,
            "warnings": self.warnings,
        }


_lock = threading.Lock()
_state: StoreState | None = None


def _in_process(reason: str, warnings: list[str] | None = None) -> StoreState:
    store = MemoryGraphStore()
    store.load()
    return StoreState(
        store=store,
        backend=store.engine_name,
        requested_backend="neo4j",
        degraded=True,
        reason=reason,
        warnings=warnings or [],
    )


def build_store(force_memory: bool = False) -> StoreState:
    settings = get_settings()
    wants_neo4j = settings.neo4j.configured and settings.neo4j.enabled and not force_memory

    if not wants_neo4j:
        store = MemoryGraphStore()
        store.load()
        reason = (
            "in-process engine requested"
            if force_memory
            else "Neo4j is not configured (set NEO4J_URI and NEO4J_PASSWORD to use it); "
                 "running the embedded engine with the identical schema and algorithms"
        )
        return StoreState(store=store, backend=store.engine_name,
                          requested_backend="in-process", reason=reason)

    try:
        from backend.graph.neo4j_store import Neo4jGraphStore

        store = Neo4jGraphStore()
        store.load()
        return StoreState(store=store, backend=store.engine_name, requested_backend="neo4j")
    except ImportError as exc:
        reason = f"neo4j driver not installed ({exc})"
    except Exception as exc:  # noqa: BLE001 - connectivity/permission problems degrade, not crash
        reason = f"Neo4j unavailable: {exc}"

    log.warning("%s — falling back to the in-process engine", reason)
    return _in_process(
        reason,
        warnings=[
            f"Graph database unavailable ({reason}). Results come from the in-process engine, which uses the "
            "same schema, the same algorithms and the same API. Set the NEO4J_* variables in .env and restart "
            "to serve Neo4j instead."
        ],
    )


def get_state(force_reload: bool = False, force_memory: bool = False) -> StoreState:
    global _state
    with _lock:
        if _state is None or force_reload:
            _state = build_store(force_memory=force_memory)
        return _state


def get_store() -> Any:
    return get_state().store
