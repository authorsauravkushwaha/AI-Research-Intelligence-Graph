"""NEXUS — runtime configuration.

Everything is environment driven (see .env.example). No credential is ever
hardcoded, and nothing in this module is importable by the front end: the API
never forwards these values to the browser, it only exposes *capability flags*
(can the server reach Neo4j? is an LLM key configured?).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("NEXUS_DATA_DIR", REPO_ROOT / "data"))
DEMO_DIR = DATA_DIR / "demo"
NATIVE_BIN = Path(
    os.getenv("NEXUS_NATIVE_KERNEL", REPO_ROOT / "native" / "build" / "bin" / "nexus-kernel")
)


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _load_dotenv_fallback() -> None:
    """Minimal .env loader.

    We deliberately avoid a hard dependency on python-dotenv: a hackathon judge
    may clone the repo and run it with nothing but FastAPI installed.
    Existing environment variables always win over .env entries.
    """
    for candidate in (REPO_ROOT / ".env", Path.cwd() / ".env"):
        if not candidate.exists():
            continue
        try:
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        except OSError:
            continue
        break


_load_dotenv_fallback()


@dataclass(frozen=True)
class Neo4jConfig:
    uri: str = field(default_factory=lambda: os.getenv("NEO4J_URI", "").strip())
    username: str = field(default_factory=lambda: os.getenv("NEO4J_USERNAME", "neo4j").strip())
    password: str = field(default_factory=lambda: os.getenv("NEO4J_PASSWORD", ""))
    database: str = field(default_factory=lambda: os.getenv("NEO4J_DATABASE", "neo4j").strip())
    enabled: bool = field(default_factory=lambda: _bool("NEXUS_ENABLE_NEO4J", True))
    timeout: float = field(default_factory=lambda: _float("NEO4J_TIMEOUT_SECONDS", 4.0))
    # Graph Data Science is optional: the engine writes GDS projections when the
    # plugin exists and always keeps its own C++ kernel results as the fallback.
    use_gds: bool = field(default_factory=lambda: _bool("NEXUS_USE_GDS", True))

    @property
    def configured(self) -> bool:
        return bool(self.uri and self.username and self.password)


@dataclass(frozen=True)
class LLMConfig:
    provider: str = field(default_factory=lambda: os.getenv("NEXUS_LLM_PROVIDER", "auto").strip().lower())
    api_key: str = field(default_factory=lambda: (os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip())
    base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/"))
    model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "gpt-4o-mini").strip())
    temperature: float = field(default_factory=lambda: _float("LLM_TEMPERATURE", 0.2))
    max_tokens: int = field(default_factory=lambda: _int("LLM_MAX_TOKENS", 1200))
    timeout: float = field(default_factory=lambda: _float("LLM_TIMEOUT_SECONDS", 60.0))

    @property
    def available(self) -> bool:
        return bool(self.api_key) and self.provider not in {"none", "off", "disabled"}


@dataclass(frozen=True)
class EmbeddingConfig:
    base_url: str = field(
        default_factory=lambda: os.getenv("NEXUS_EMBEDDINGS_URL", "").rstrip("/")
    )
    api_key: str = field(default_factory=lambda: os.getenv("NEXUS_EMBEDDINGS_KEY", "").strip())
    model: str = field(default_factory=lambda: os.getenv("NEXUS_EMBEDDING_MODEL", "text-embedding-3-small"))
    dim: int = field(default_factory=lambda: _int("NEXUS_EMBEDDING_DIM", 384))
    # Local Go service (polyglot worker). Used when no provider key is present.
    go_service: str = field(default_factory=lambda: os.getenv("NEXUS_INGEST_URL", "http://127.0.0.1:8090").rstrip("/"))
    ingest_service: str = field(default_factory=lambda: os.getenv("NEXUS_INGEST_URL", "http://127.0.0.1:8090").rstrip("/"))
    export_service: str = field(default_factory=lambda: os.getenv("NEXUS_EXPORT_URL", "http://127.0.0.1:8091").rstrip("/"))
    planner_service: str = field(default_factory=lambda: os.getenv("NEXUS_PLANNER_URL", "http://127.0.0.1:8092").rstrip("/"))
    claim_service: str = field(default_factory=lambda: os.getenv("NEXUS_CLAIM_URL", "").rstrip("/"))


@dataclass(frozen=True)
class ServerConfig:
    host: str = field(default_factory=lambda: os.getenv("NEXUS_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _int("NEXUS_PORT", 8000))
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            o.strip()
            for o in os.getenv("NEXUS_CORS_ORIGINS", "*").split(",")
            if o.strip()
        )
    )
    max_graph_nodes: int = field(default_factory=lambda: _int("NEXUS_MAX_GRAPH_NODES", 400))
    max_graph_edges: int = field(default_factory=lambda: _int("NEXUS_MAX_GRAPH_EDGES", 1200))
    rate_limit_per_minute: int = field(default_factory=lambda: _int("NEXUS_RATE_LIMIT_PER_MINUTE", 240))
    demo_mode: bool = field(default_factory=lambda: _bool("NEXUS_DEMO_MODE", True))


@dataclass(frozen=True)
class Settings:
    neo4j: Neo4jConfig = field(default_factory=Neo4jConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    embeddings: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    version: str = "1.0.0"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def capability_flags() -> dict[str, object]:
    """Non-sensitive capability report used by the UI's status bar."""
    s = get_settings()
    return {
        "version": s.version,
        "neo4j_configured": s.neo4j.configured,
        "neo4j_enabled": s.neo4j.enabled,
        "gds_enabled": s.neo4j.use_gds,
        "llm_configured": s.llm.available,
        "llm_provider": s.llm.provider,
        "llm_model": s.llm.model if s.llm.available else None,
        "embeddings_provider": bool(s.embeddings.base_url and s.embeddings.api_key),
        "demo_mode": s.server.demo_mode,
    }
