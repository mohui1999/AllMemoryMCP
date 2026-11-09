"""Optional text embeddings for semantic memory search.

Semantic search is OFF by default — memory_search falls back to case-insensitive
LIKE. Enable it by pointing DATABASE_URL at PostgreSQL+pgvector and setting
EMBEDDING_PROVIDER=openai plus OPENAI_API_KEY. Embeddings are computed remotely
(OpenAI), so the server stays light and no local model is loaded. This keeps the
feature usable on memory-constrained hosts.

Extend `embed()` with more providers as needed; the rest of the pipeline
(create_storage / search) is provider-agnostic.
"""
from __future__ import annotations

import json
import logging
import os
from typing import List, Optional

logger = logging.getLogger("allmemory.embeddings")

_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "").lower()
_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))


def is_enabled() -> bool:
    return _PROVIDER in ("openai",)


def dim() -> int:
    return _DIM


def embed(text: str) -> Optional[List[float]]:
    """Return an embedding vector for `text`, or None if disabled/unavailable."""
    if not is_enabled():
        return None
    if _PROVIDER == "openai":
        return _embed_openai(text)
    return None


def _embed_openai(text: str) -> Optional[List[float]]:
    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai package not installed; semantic search disabled")
        return None
    try:
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        model = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
        resp = client.embeddings.create(model=model, input=text)
        return list(resp.data[0].embedding)
    except Exception as exc:  # noqa: BLE001 - degrade gracefully to LIKE
        logger.warning("embedding request failed: %s", exc)
        return None


def serialize(vector: Optional[List[float]]) -> Optional[str]:
    return json.dumps(vector) if vector is not None else None


def deserialize(payload) -> Optional[List[float]]:
    if payload is None:
        return None
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return None
    return list(payload)
