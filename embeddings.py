"""Embeddings for memory retrieval.

Providers (``EMBEDDING_PROVIDER``):

* ``local`` (default) - offline, deterministic feature hashing. Zero dependencies,
  no API key, no model download. It captures lexical/character similarity (including
  Chinese bigrams), which is enough for "find related memories" on a small host.
* ``openai`` - real semantic vectors from OpenAI or any OpenAI-compatible endpoint
  (set ``OPENAI_BASE_URL`` for Ollama / a private gateway). Requires ``openai``.
* ``none`` - no vectors at all; keyword search only.

Vectors are stored per retrieval chunk: a pgvector column on PostgreSQL, JSON text
on SQLite. Retrieval computes cosine distance in SQL (pgvector/HNSW) or in Python
(SQLite), after tenant/layer filters have been applied. Canonical text is always
persisted first: a provider failure marks chunks as ``failed`` and keyword search
keeps working, so a broken embedding service never blocks a memory write.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
from typing import List, Optional

logger = logging.getLogger("allmemory.embeddings")

_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "local").strip().lower()
_DIM = max(32, int(os.getenv("EMBEDDING_DIM", "1536")))
_LOCAL_MODEL = "local-hash-v1"
_WORD_RE = re.compile(r"\w+", re.UNICODE)
_CJK_RE = re.compile(r"[\u3400-\u9fff]")


def is_enabled() -> bool:
    return _PROVIDER in ("local", "openai")


def provider() -> str:
    return _PROVIDER


def dim() -> int:
    return _DIM


def model_name() -> Optional[str]:
    if _PROVIDER == "local":
        return _LOCAL_MODEL
    if _PROVIDER == "openai":
        return os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")
    return None


def embed(text: str) -> Optional[List[float]]:
    """Return one embedding vector, or None if disabled/unavailable."""
    vectors = embed_many([text])
    return vectors[0] if vectors else None


def embed_many(texts: list[str]) -> Optional[list[List[float]]]:
    """Embed a batch; return None when the provider is disabled or unavailable."""
    if not is_enabled():
        return None
    if not texts:
        return []
    if _PROVIDER == "local":
        return [_embed_local(text) for text in texts]
    if _PROVIDER == "openai":
        vectors = _embed_openai(texts)
        if vectors is None:
            return None
        if any(len(vector) != _DIM for vector in vectors):
            logger.error(
                "embedding dimension mismatch: EMBEDDING_DIM=%s returned=%s; "
                "set EMBEDDING_DIM to the model's output size",
                _DIM, [len(vector) for vector in vectors],
            )
            return None
        return vectors
    return None


# --------------------------------------------------------------------------- #
# Offline provider: signed feature hashing over words, word bigrams and CJK
# character bigrams. Deterministic and cheap enough for a 1-2 GB host.
# --------------------------------------------------------------------------- #
def _features(text: str) -> list[str]:
    tokens = [token.lower() for token in _WORD_RE.findall(text)]
    features: list[str] = []
    for token in tokens:
        features.append("w:" + token)
        if len(token) > 3:
            features.append("p:" + token[:3])
    features.extend("b:" + a + "_" + b for a, b in zip(tokens, tokens[1:]))
    cjk = _CJK_RE.findall(text)
    features.extend("z:" + a + b for a, b in zip(cjk, cjk[1:]))
    if not features:
        features.append("w:" + text.strip().lower())
    return features


def _embed_local(text: str) -> List[float]:
    vector = [0.0] * _DIM
    for feature in _features(text):
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        hashed = int.from_bytes(digest, "big")
        vector[hashed % _DIM] += 1.0 if (hashed >> 63) & 1 == 0 else -1.0
    norm = math.sqrt(sum(value * value for value in vector))
    if norm:
        vector = [value / norm for value in vector]
    return vector


# --------------------------------------------------------------------------- #
# Remote provider: OpenAI-compatible /v1/embeddings
# --------------------------------------------------------------------------- #
def _embed_openai(texts: list[str]) -> Optional[list[List[float]]]:
    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai package not installed; semantic vectors unavailable")
        return None
    base_url = os.getenv("OPENAI_BASE_URL") or os.getenv("EMBEDDING_BASE_URL") or None
    # Local gateways (Ollama, vLLM, ...) often ignore the API key entirely.
    api_key = os.getenv("OPENAI_API_KEY") or "not-needed"
    model = model_name()
    request: dict = {"model": model, "input": texts}
    if model and model.startswith("text-embedding-3-"):
        request["dimensions"] = _DIM
    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        response = client.embeddings.create(**request)
        return [list(item.embedding) for item in sorted(response.data, key=lambda item: item.index)]
    except Exception as exc:  # noqa: BLE001 - degrade gracefully to keyword search
        logger.warning("embedding request failed: %s", exc)
        return None


# --------------------------------------------------------------------------- #
# SQLite storage format: sparse JSON to keep the database small. Both the old
# dense list format and the current {"v": dim, "s": {index: value}} are accepted.
# --------------------------------------------------------------------------- #
def serialize(vector: Optional[List[float]]) -> Optional[str]:
    if vector is None:
        return None
    sparse = {str(index): round(value, 6) for index, value in enumerate(vector) if value}
    return json.dumps({"v": len(vector), "s": sparse}, separators=(",", ":"))


def deserialize(payload) -> Optional[List[float]]:
    if payload is None:
        return None
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if isinstance(payload, dict) and "s" in payload:
        dims = int(payload.get("v") or len(payload["s"]))
        vector = [0.0] * dims
        for index, value in payload["s"].items():
            idx = int(index)
            if idx < dims:
                vector[idx] = float(value)
        return vector
    try:
        return [float(value) for value in payload]
    except (TypeError, ValueError):
        return None
