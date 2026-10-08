"""NEXUS backend package.

`backend.api.app:create_app` builds the FastAPI application; `backend.graph.factory`
selects the graph store (Neo4j when reachable, in-process otherwise).
"""

__all__ = ["create_app"]


def create_app():  # pragma: no cover - convenience re-export
    from backend.api.app import create_app as _create_app

    return _create_app()
