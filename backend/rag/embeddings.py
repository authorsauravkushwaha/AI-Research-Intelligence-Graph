"""Embedding layer for NEXUS semantic search.

Three tiers, in order of preference:

1. **Provider** — any OpenAI-compatible `/embeddings` endpoint
   (`NEXUS_EMBEDDINGS_URL` + `NEXUS_EMBEDDINGS_KEY`). This is the real semantic
   model and the tier the demo uses when a key is present.
2. **Go sidecar** — `polyglot/go` exposes the same OpenAI-compatible route and
   proxies to the provider when configured, otherwise serving its deterministic
   lexical baseline.
3. **Local fallback** — hashed word + character-trigram features, L2-normalised,
   implemented identically in Python and Go so results agree. This is a *lexical*
   baseline, never described as semantic. The API reports which tier answered so
   the UI can label similarity honestly.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from dataclasses import dataclass
from typing import Sequence

import httpx

from backend.config import get_settings

log = logging.getLogger("nexus.embeddings")

_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass(slots=True)
class EmbeddingBatch:
    vectors: list[list[float]]
    engine: str
    note: str = ""


def local_embedding(text: str, dim: int = 384) -> list[float]:
    """Deterministic hashed n-gram embedding (lexical baseline)."""
    vec = [0.0] * dim
    lowered = text.lower()
    for word in _WORD_RE.findall(lowered):
        bucket = int(hashlib.blake2b(("w:" + word).encode(), digest_size=8).hexdigest(), 16) % dim
        vec[bucket] += 1.0
        if len(word) > 4:
            for i in range(len(word) - 2):
                tri = word[i : i + 3]
                b2 = int(hashlib.blake2b(("g:" + tri).encode(), digest_size=8).hexdigest(), 16) % dim
                vec[b2] += 0.35
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _provider_embed(texts: Sequence[str], url: str, key: str, model: str, timeout: float) -> EmbeddingBatch:
    payload = {"input": list(texts), "model": model}
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url.rstrip("/") + "/embeddings", json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    vectors = [item["embedding"] for item in data.get("data", [])]
    if len(vectors) != len(texts):
        raise ValueError(f"provider returned {len(vectors)} vectors for {len(texts)} inputs")
    return EmbeddingBatch(vectors=vectors, engine=f"provider:{model}")


def _sidecar_embed(texts: Sequence[str], base_url: str, model: str, timeout: float) -> EmbeddingBatch:
    payload = {"input": list(texts), "model": model}
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(base_url.rstrip("/") + "/v1/embeddings", json=payload)
        resp.raise_for_status()
        data = resp.json()
    vectors = [item["embedding"] for item in data.get("data", [])]
    if len(vectors) != len(texts):
        raise ValueError("sidecar returned a mismatched vector count")
    engine = data.get("engine", "go-sidecar")
    note = data.get("note", "")
    return EmbeddingBatch(vectors=vectors, engine=f"go-sidecar:{engine}", note=note)


def embed(texts: Sequence[str], *, allow_sidecar: bool = True) -> EmbeddingBatch:
    settings = get_settings()
    dim = settings.embeddings.dim
    model = settings.embeddings.model

    if settings.embeddings.base_url and settings.embeddings.api_key:
        try:
            return _provider_embed(
                texts,
                settings.embeddings.base_url,
                settings.embeddings.api_key,
                model,
                settings.llm.timeout,
            )
        except Exception as exc:  # noqa: BLE001 - any provider failure degrades gracefully
            log.warning("embedding provider failed (%s) — falling back", exc)

    if allow_sidecar and settings.embeddings.go_service:
        try:
            return _sidecar_embed(texts, settings.embeddings.go_service, model, 20.0)
        except Exception as exc:  # noqa: BLE001 - sidecar is optional
            log.debug("go embedding sidecar unavailable (%s)", exc)

    return EmbeddingBatch(
        vectors=[local_embedding(t, dim) for t in texts],
        engine="local:lexical-baseline",
        note="Deterministic hashed n-gram embedding. Lexical baseline, not a semantic model — "
        "similarity is reported as lexical overlap unless a provider is configured.",
    )


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b:
        return 0.0
    dim = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(dim))
    na = math.sqrt(sum(x * x for x in a[:dim]))
    nb = math.sqrt(sum(x * x for x in b[:dim]))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
