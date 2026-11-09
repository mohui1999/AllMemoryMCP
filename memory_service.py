"""Shared persistence and authorization for REST and MCP entry points.

Business logic is backend-agnostic (SQLAlchemy via db.session()). Every operation
is scoped to a Principal = (tenant_id, agent_id). Tenant isolation is enforced in
SQL for every read/write so a token from one tenant can never see or touch
another tenant's memories or negotiation threads.

Authentication returns a Principal (upgraded from a bare agent id). Ownership and
visibility rules live here.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import func, or_, select, update

from db import (
    AuditLog, Memory, MemoryChunk, MemoryRevision, NegotiationMessage,
    NegotiationThread, Principal, StructuredMemory, ThreadRecipient, is_postgres,
    like_ignore_case, memory_revision_to_dict, memory_to_dict, msg_to_dict, now,
    PGVECTOR_AVAILABLE, session, structured_memory_to_dict, thread_to_dict,
)
from embeddings import (
    deserialize, embed, embed_many, is_enabled as embeddings_enabled,
    model_name as embedding_model, provider as embedding_provider, serialize,
)


# --------------------------------------------------------------------------- #
# Identity & scopes
# --------------------------------------------------------------------------- #
def configured_tokens() -> dict[str, dict[str, str]]:
    """AGENT_TOKENS is a nested map: {tenant_id: {agent_id: token}}.

    A single-tenant deployment can use {"default": {"codex": "...", "cursor": "..."}}.
    """
    raw = os.getenv("AGENT_TOKENS", "{}")
    try:
        tokens = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("AGENT_TOKENS must be a JSON object") from exc
    if not isinstance(tokens, dict) or not all(
        isinstance(t, dict) and all(isinstance(k, str) and isinstance(v, str)
                                   for k, v in t.items())
        for t in tokens.values()
    ):
        raise RuntimeError(
            "AGENT_TOKENS must be {tenant_id: {agent_id: token}}"
        )
    return tokens


def authenticate(token: str | None) -> Principal | None:
    if not token:
        return None
    # 1) Static env tokens (backward compatible) — full read+write.
    for tenant_id, agents in configured_tokens().items():
        for agent_id, expected in agents.items():
            if secrets.compare_digest(token, expected):
                return Principal(tenant_id=tenant_id, agent_id=agent_id, scopes=("read", "write"))
    # 2) Managed DB credentials (issued / revoked / rotated at runtime).
    from credentials import verify_token
    return verify_token(token)


class ScopeError(PermissionError):
    """Raised when a Principal lacks the scope required for an operation."""


class InvalidInput(ValueError):
    """Caller-supplied data failed validation; mapped to HTTP 422 by the REST layer."""


class VersionConflict(ValueError):
    """Raised when a summary/event update is based on an old revision."""


def require_scope(principal: Principal, scope: str) -> None:
    if not principal.has(scope):
        raise ScopeError(f"requires scope '{scope}'")


def initialize() -> None:
    from db import initialize as db_init
    db_init()


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #
def _audit(s, principal: Principal, action: str, object_type: str, object_id: str,
          details: dict[str, Any] | None = None) -> None:
    s.add(AuditLog(
        id=str(uuid4()), tenant_id=principal.tenant_id, actor_agent_id=principal.agent_id,
        action=action, object_type=object_type, object_id=object_id,
        created_at=now(), details=json.dumps(details or {}),
    ))


# --------------------------------------------------------------------------- #
# Memories
# --------------------------------------------------------------------------- #
def _cosine_distance(left: list[float], right: list[float]) -> float:
    """Cosine distance for the SQLite path (pgvector does this in SQL)."""
    if len(left) != len(right):
        return 1.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if not norm_left or not norm_right:
        return 1.0
    return max(0.0, min(2.0, 1.0 - dot / (norm_left * norm_right)))


def _split_memory_content(content: str, max_chars: int = 1800,
                          overlap: int = 160) -> list[str]:
    """Split Markdown/text into stable chunks while preserving paragraph boundaries."""
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", content) if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            candidate = f"{current}\n\n{paragraph}" if current else paragraph
            if len(candidate) <= max_chars:
                current = candidate
                continue
            if current:
                chunks.append(current)
            current = paragraph
            continue

        if current:
            chunks.append(current)
            current = ""
        start = 0
        while start < len(paragraph):
            end = min(start + max_chars, len(paragraph))
            chunks.append(paragraph[start:end])
            if end == len(paragraph):
                break
            start = max(start + 1, end - overlap)

    if current:
        chunks.append(current)
    return chunks or [content.strip()]


def _prepare_memory_chunks(content: str) -> list[dict[str, Any]]:
    model = embedding_model()
    enabled = embeddings_enabled()
    content_chunks = _split_memory_content(content)
    vectors = embed_many(content_chunks) if enabled else None
    prepared = []
    for index, chunk in enumerate(content_chunks):
        vector = vectors[index] if vectors and index < len(vectors) else None
        status = "ready" if vector is not None else ("failed" if enabled else "disabled")
        prepared.append({
            "chunk_index": index,
            "content": chunk,
            "content_hash": hashlib.sha256(chunk.encode("utf-8")).hexdigest(),
            "embedding": vector if is_postgres() and PGVECTOR_AVAILABLE else serialize(vector),
            "embedding_model": model,
            "embedding_status": status,
        })
    return prepared


def _replace_memory_chunks(s, memory: StructuredMemory,
                           chunks: list[dict[str, Any]]) -> None:
    # Keep older chunks as an audit/rebuild source. Retrieval always filters to
    # the memory's current version, so stale vectors cannot surface in results.
    for chunk in chunks:
        s.add(MemoryChunk(
            id=str(uuid4()), memory_id=memory.id, tenant_id=memory.tenant_id,
            owner_agent_id=memory.owner_agent_id, memory_layer=memory.memory_layer,
            category=memory.category, event_key=memory.event_key,
            occurred_at=memory.occurred_at, memory_version=memory.version,
            created_at=now(), **chunk,
        ))


def _normalize_tags(tags: list[str] | None) -> str:
    normalized = list(dict.fromkeys(tag.strip() for tag in (tags or []) if tag.strip()))
    if len(normalized) > 30 or any(len(tag) > 128 for tag in normalized):
        raise InvalidInput("tags may contain at most 30 values of 128 characters each")
    encoded = json.dumps(normalized, ensure_ascii=False)
    if len(encoded) > 4000:
        raise InvalidInput("serialized tags exceed the storage limit")
    return encoded


def _normalize_timestamp(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise InvalidInput("occurred_at must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise InvalidInput("occurred_at must include a timezone, for example +00:00 or Z")
    return parsed.astimezone(timezone.utc).isoformat()


def _record_revision(s, memory: StructuredMemory, change_note: str | None = None) -> None:
    s.add(MemoryRevision(
        id=str(uuid4()), memory_id=memory.id, tenant_id=memory.tenant_id,
        owner_agent_id=memory.owner_agent_id, version=memory.version,
        content=memory.content, change_note=(change_note or "")[:1000] or None,
        created_at=now(),
    ))


def _active_summary(s, tenant_id: str, owner_agent_id: str) -> StructuredMemory | None:
    return s.execute(select(StructuredMemory).where(
        StructuredMemory.tenant_id == tenant_id,
        StructuredMemory.owner_agent_id == owner_agent_id,
        StructuredMemory.memory_layer == "summary",
        StructuredMemory.deleted_at.is_(None),
    )).scalars().first()


def get_agent_summary(principal: Principal, agent_id: str) -> dict[str, Any] | None:
    require_scope(principal, "read")
    with session() as s:
        row = _active_summary(s, principal.tenant_id, agent_id)
        return structured_memory_to_dict(row) if row else None


def upsert_agent_summary(principal: Principal, markdown: str,
                         source_watermark: str | None = None,
                         expected_version: int | None = None,
                         change_note: str | None = None) -> dict[str, Any]:
    """Create an Agent's summary or replace it with optimistic concurrency."""
    require_scope(principal, "write")
    markdown = markdown.strip()
    if not markdown:
        raise InvalidInput("summary markdown must not be empty")
    if len(markdown) > 100_000:
        raise InvalidInput("summary markdown exceeds 100000 characters")
    if source_watermark and len(source_watermark) > 40:
        raise InvalidInput("source_watermark exceeds 40 characters")
    chunks = _prepare_memory_chunks(markdown)
    with session() as s:
        current = _active_summary(s, principal.tenant_id, principal.agent_id)
        ts = now()
        if current is None:
            if expected_version not in (None, 0):
                raise VersionConflict("summary does not exist; expected_version must be 0")
            memory = StructuredMemory(
                id=str(uuid4()), tenant_id=principal.tenant_id,
                owner_agent_id=principal.agent_id, memory_layer="summary",
                title=f"{principal.agent_id} memory summary", content=markdown,
                source_watermark=source_watermark, tags="[]", version=1,
                created_at=ts, updated_at=ts,
            )
            s.add(memory)
            s.flush()
            _record_revision(s, memory, change_note or "Initial summary")
            _replace_memory_chunks(s, memory, chunks)
            _audit(s, principal, "create", "summary", memory.id)
        else:
            if expected_version is None:
                raise VersionConflict("expected_version is required when updating a summary")
            if current.version != expected_version:
                raise VersionConflict(f"summary is at version {current.version}")
            next_version = current.version + 1
            result = s.execute(update(StructuredMemory).where(
                StructuredMemory.id == current.id,
                StructuredMemory.version == expected_version,
                StructuredMemory.deleted_at.is_(None),
            ).values(
                content=markdown, source_watermark=source_watermark,
                version=next_version, updated_at=ts,
            ))
            if result.rowcount != 1:
                raise VersionConflict("summary changed during update; read it again")
            s.refresh(current)
            _record_revision(s, current, change_note)
            _replace_memory_chunks(s, current, chunks)
            _audit(s, principal, "update", "summary", current.id,
                   {"version": next_version})
            memory = current
        s.flush()
        return structured_memory_to_dict(memory)


def create_timeline_memory(principal: Principal, title: str, content: str,
                           occurred_at: str, category: str | None = None,
                           tags: list[str] | None = None,
                           supersedes_memory_id: str | None = None) -> dict[str, Any]:
    """Append an immutable timeline event; corrections supersede the old row."""
    require_scope(principal, "write")
    title = title.strip()
    content = content.strip()
    if not title or not content:
        raise InvalidInput("title and content must not be empty")
    if len(title) > 300 or len(content) > 20_000:
        raise InvalidInput("timeline title/content exceeds its length limit")
    occurred = _normalize_timestamp(occurred_at)
    category = category.strip() if category and category.strip() else None
    if category and len(category) > 128:
        raise InvalidInput("category exceeds 128 characters")
    tags_json = _normalize_tags(tags)
    chunks = _prepare_memory_chunks(f"{title}\n\n{content}")
    with session() as s:
        ts = now()
        if supersedes_memory_id:
            previous = s.execute(select(StructuredMemory).where(
                StructuredMemory.id == supersedes_memory_id,
                StructuredMemory.tenant_id == principal.tenant_id,
                StructuredMemory.owner_agent_id == principal.agent_id,
                StructuredMemory.memory_layer == "timeline",
                StructuredMemory.deleted_at.is_(None),
                StructuredMemory.superseded_at.is_(None),
            )).scalars().first()
            if not previous:
                raise InvalidInput("superseded timeline memory not found or not owned by this Agent")
            result = s.execute(update(StructuredMemory).where(
                StructuredMemory.id == previous.id,
                StructuredMemory.superseded_at.is_(None),
            ).values(superseded_at=ts, updated_at=ts))
            if result.rowcount != 1:
                raise VersionConflict("timeline memory was already superseded")
        memory = StructuredMemory(
            id=str(uuid4()), tenant_id=principal.tenant_id,
            owner_agent_id=principal.agent_id, memory_layer="timeline",
            title=title, content=content, category=category, event_key=None,
            occurred_at=occurred, tags=tags_json, version=1,
            created_at=ts, updated_at=ts,
            supersedes_memory_id=supersedes_memory_id,
        )
        s.add(memory)
        s.flush()
        _replace_memory_chunks(s, memory, chunks)
        _audit(s, principal, "create", "timeline", memory.id,
               {"occurred_at": occurred, "supersedes": supersedes_memory_id})
        return structured_memory_to_dict(memory)


def upsert_event_memory(principal: Principal, category: str, event_key: str,
                        title: str, content_markdown: str,
                        expected_version: int | None = None,
                        change_note: str | None = None,
                        tags: list[str] | None = None) -> dict[str, Any]:
    """Maintain the current state of one categorized event and append revisions."""
    require_scope(principal, "write")
    category = category.strip()
    event_key = event_key.strip()
    title = title.strip()
    content_markdown = content_markdown.strip()
    if not category or not event_key or not title or not content_markdown:
        raise InvalidInput("category, event_key, title, and content_markdown are required")
    if len(category) > 128 or len(event_key) > 200 or len(title) > 300:
        raise InvalidInput("category, event_key, or title exceeds its length limit")
    if len(content_markdown) > 50_000:
        raise InvalidInput("event memory content exceeds 50000 characters")
    tags_json = _normalize_tags(tags)
    chunks = _prepare_memory_chunks(f"{title}\n\n{content_markdown}")
    with session() as s:
        current = s.execute(select(StructuredMemory).where(
            StructuredMemory.tenant_id == principal.tenant_id,
            StructuredMemory.owner_agent_id == principal.agent_id,
            StructuredMemory.memory_layer == "event",
            StructuredMemory.category == category,
            StructuredMemory.event_key == event_key,
            StructuredMemory.deleted_at.is_(None),
        )).scalars().first()
        ts = now()
        if current is None:
            if expected_version not in (None, 0):
                raise VersionConflict("event memory does not exist; expected_version must be 0")
            memory = StructuredMemory(
                id=str(uuid4()), tenant_id=principal.tenant_id,
                owner_agent_id=principal.agent_id, memory_layer="event",
                title=title, content=content_markdown, category=category,
                event_key=event_key, tags=tags_json, version=1,
                created_at=ts, updated_at=ts,
            )
            s.add(memory)
            s.flush()
            _record_revision(s, memory, change_note or "Initial event memory")
            _replace_memory_chunks(s, memory, chunks)
            _audit(s, principal, "create", "event", memory.id,
                   {"category": category, "event_key": event_key})
        else:
            if expected_version is None:
                raise VersionConflict("expected_version is required when updating an event memory")
            if current.version != expected_version:
                raise VersionConflict(f"event memory is at version {current.version}")
            next_version = current.version + 1
            result = s.execute(update(StructuredMemory).where(
                StructuredMemory.id == current.id,
                StructuredMemory.version == expected_version,
                StructuredMemory.deleted_at.is_(None),
            ).values(
                title=title, content=content_markdown, tags=tags_json,
                version=next_version, updated_at=ts,
            ))
            if result.rowcount != 1:
                raise VersionConflict("event memory changed during update; read it again")
            s.refresh(current)
            _record_revision(s, current, change_note)
            _replace_memory_chunks(s, current, chunks)
            _audit(s, principal, "update", "event", current.id,
                   {"version": next_version})
            memory = current
        s.flush()
        return structured_memory_to_dict(memory)


def update_event_memory(principal: Principal, memory_id: str, content: str | None,
                        tags: list[str] | None = None,
                        expected_version: int | None = None,
                        change_note: str | None = None) -> dict[str, Any] | None:
    """Update only an event row owned by the caller, without requiring read scope."""
    require_scope(principal, "write")
    with session() as s:
        row = s.execute(select(StructuredMemory).where(
            StructuredMemory.id == memory_id,
            StructuredMemory.tenant_id == principal.tenant_id,
            StructuredMemory.owner_agent_id == principal.agent_id,
            StructuredMemory.memory_layer == "event",
            StructuredMemory.deleted_at.is_(None),
        )).scalars().first()
        if not row:
            return None
        current = structured_memory_to_dict(row)
    return upsert_event_memory(
        principal, current["category"], current["event_key"],
        current.get("title") or current["event_key"],
        content if content is not None else current["content"],
        expected_version, change_note,
        tags if tags is not None else current["tags"],
    )


def list_memory_revisions(principal: Principal, memory_id: str) -> list[dict[str, Any]]:
    require_scope(principal, "read")
    with session() as s:
        memory = s.execute(select(StructuredMemory.id).where(
            StructuredMemory.id == memory_id,
            StructuredMemory.tenant_id == principal.tenant_id,
            StructuredMemory.deleted_at.is_(None),
        )).first()
        if not memory:
            return []
        rows = s.execute(select(MemoryRevision).where(
            MemoryRevision.memory_id == memory_id,
            MemoryRevision.tenant_id == principal.tenant_id,
        ).order_by(MemoryRevision.version.desc())).scalars().all()
        return [memory_revision_to_dict(row) for row in rows]


# Common starting points for event memories. Categories are free-form strings;
# this list only documents the convention used by the bundled agent instructions.
SUGGESTED_CATEGORIES = ("development", "finance", "health")


def list_categories(principal: Principal, agent_id: str | None = None) -> list[dict[str, Any]]:
    """Enumerate categories in use (development / finance / health / ...), with counts."""
    require_scope(principal, "read")
    with session() as s:
        stmt = (
            select(
                StructuredMemory.category,
                StructuredMemory.memory_layer,
                func.count().label("count"),
            ).where(
                StructuredMemory.tenant_id == principal.tenant_id,
                StructuredMemory.deleted_at.is_(None),
                StructuredMemory.superseded_at.is_(None),
                StructuredMemory.category.isnot(None),
                StructuredMemory.memory_layer.in_(("event", "timeline")),
            ).group_by(StructuredMemory.category, StructuredMemory.memory_layer)
        )
        if agent_id:
            stmt = stmt.where(StructuredMemory.owner_agent_id == agent_id)
        rows = s.execute(stmt).all()

    merged: dict[str, dict[str, Any]] = {}
    for category, layer, count in rows:
        entry = merged.setdefault(category, {
            "category": category, "event_count": 0, "timeline_count": 0,
        })
        entry["event_count" if layer == "event" else "timeline_count"] += int(count)
    return sorted(
        merged.values(),
        key=lambda item: (-(item["event_count"] + item["timeline_count"]), item["category"]),
    )


def get_memory_context(principal: Principal, agent_id: str | None = None,
                       query: str = "", category: str | None = None,
                       timeline_limit: int = 10, event_limit: int = 10) -> dict[str, Any]:
    """Load one Agent's whole memory picture in a single call.

    Returns the Markdown summary, recent timeline events, matching event memories
    (both through the same hybrid retrieval as memory_search), and the categories
    currently in use. This is the recommended first call when an Agent starts work.
    """
    require_scope(principal, "read")
    target = agent_id or principal.agent_id
    timeline_limit = max(0, min(timeline_limit, 50))
    event_limit = max(0, min(event_limit, 50))
    timeline = search_memories(
        query, max(1, timeline_limit), target, principal, 0, "timeline", category,
    ) if timeline_limit else []
    events = search_memories(
        query, max(1, event_limit), target, principal, 0, "event", category,
    ) if event_limit else []
    return {
        "agent_id": target,
        "summary": get_agent_summary(principal, target),
        "timeline": timeline,
        "events": events,
        "categories": list_categories(principal, target),
    }


def reindex_memory_chunks(batch_size: int = 200) -> dict[str, Any]:
    """Recompute vectors for current-version chunks that are missing or failed.

    Call this after enabling an embedding provider, changing the model, or after a
    transient provider outage. Canonical memory text is never touched; keyword
    search stays available while chunks are pending.
    """
    if not embeddings_enabled():
        return {"provider": embedding_provider(), "chunks_updated": 0, "legacy_updated": 0,
                "note": "no embedding provider enabled"}
    size = max(1, min(batch_size, 1000))
    pg_native = is_postgres() and PGVECTOR_AVAILABLE
    chunks_updated = 0
    legacy_updated = 0
    with session() as s:
        rows = s.execute(
            select(MemoryChunk)
            .join(StructuredMemory, MemoryChunk.memory_id == StructuredMemory.id)
            .where(
                StructuredMemory.deleted_at.is_(None),
                StructuredMemory.superseded_at.is_(None),
                MemoryChunk.memory_version == StructuredMemory.version,
                MemoryChunk.embedding_status != "ready",
            ).limit(size)
        ).scalars().all()
        for chunk in rows:
            vector = embed(chunk.content)
            if vector is None:
                chunk.embedding_status = "failed"
                continue
            chunk.embedding = vector if pg_native else serialize(vector)
            chunk.embedding_model = embedding_model()
            chunk.embedding_status = "ready"
            chunks_updated += 1

        legacy_rows = s.execute(select(Memory).where(
            Memory.deleted_at.is_(None), Memory.embedding.is_(None),
        ).limit(size)).scalars().all()
        for memory in legacy_rows:
            vector = embed(memory.content)
            if vector is None:
                continue
            memory.embedding = vector if pg_native else serialize(vector)
            legacy_updated += 1
    return {"provider": embedding_provider(), "chunks_updated": chunks_updated,
            "legacy_updated": legacy_updated}


def _filter_memory_query(stmt, model, principal: Principal | None,
                         owner_agent_id: str | None, query: str,
                         category: str | None, event_key: str | None,
                         occurred_from: str | None, occurred_to: str | None):
    if principal is not None:
        stmt = stmt.where(model.tenant_id == principal.tenant_id)
    if owner_agent_id:
        stmt = stmt.where(model.owner_agent_id == owner_agent_id)
    if category:
        stmt = stmt.where(model.category == category)
    if event_key:
        stmt = stmt.where(model.event_key == event_key)
    if occurred_from:
        stmt = stmt.where(model.occurred_at >= occurred_from)
    if occurred_to:
        stmt = stmt.where(model.occurred_at <= occurred_to)
    if query:
        predicates = [like_ignore_case(model.content, query)]
        for attr in ("title", "category", "event_key", "memory_layer", "tags"):
            if hasattr(model, attr):
                predicates.append(like_ignore_case(getattr(model, attr), query))
        stmt = stmt.where(or_(*predicates))
    return stmt


def search_memories(query: str = "", limit: int = 20, owner_agent_id: str | None = None,
                    principal: Principal | None = None, offset: int = 0,
                    memory_layer: str | None = None, category: str | None = None,
                    event_key: str | None = None, occurred_from: str | None = None,
                    occurred_to: str | None = None) -> list[dict[str, Any]]:
    if principal is not None:
        require_scope(principal, "read")
    if occurred_from:
        occurred_from = _normalize_timestamp(occurred_from)
    if occurred_to:
        occurred_to = _normalize_timestamp(occurred_to)
    if occurred_from and occurred_to and occurred_from > occurred_to:
        raise InvalidInput("occurred_from must not be later than occurred_to")
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    if memory_layer not in (None, "legacy", "summary", "timeline", "event"):
        raise InvalidInput("memory_layer must be summary, timeline, event, or legacy")

    include_legacy = memory_layer in (None, "legacy") and not any(
        (category, event_key, occurred_from, occurred_to)
    )
    include_structured = memory_layer in (None, "summary", "timeline", "event")
    candidate_limit = min(1000, max(200, limit + offset)) if query else limit + offset

    with session() as s:
        legacy_rows: list[Memory] = []
        structured_rows: list[StructuredMemory] = []
        if include_legacy:
            legacy_stmt = select(Memory).where(Memory.deleted_at.is_(None))
            if principal is not None:
                legacy_stmt = legacy_stmt.where(Memory.tenant_id == principal.tenant_id)
            if owner_agent_id:
                legacy_stmt = legacy_stmt.where(Memory.owner_agent_id == owner_agent_id)
            if query:
                legacy_stmt = legacy_stmt.where(or_(
                    like_ignore_case(Memory.content, query),
                    like_ignore_case(Memory.tags, query),
                    like_ignore_case(Memory.memory_type, query),
                ))
            legacy_rows = s.execute(
                legacy_stmt.order_by(Memory.updated_at.desc()).limit(candidate_limit)
            ).scalars().all()

        if include_structured:
            structured_stmt = select(StructuredMemory).where(
                StructuredMemory.deleted_at.is_(None),
                StructuredMemory.superseded_at.is_(None),
            )
            if memory_layer:
                structured_stmt = structured_stmt.where(
                    StructuredMemory.memory_layer == memory_layer
                )
            structured_stmt = _filter_memory_query(
                structured_stmt, StructuredMemory, principal, owner_agent_id, query,
                category, event_key, occurred_from, occurred_to,
            )
            sort_column = (
                StructuredMemory.occurred_at
                if memory_layer == "timeline" else StructuredMemory.updated_at
            )
            structured_rows = s.execute(
                structured_stmt.order_by(sort_column.desc()).limit(candidate_limit)
            ).scalars().all()

        if not query:
            entries = [memory_to_dict(row) for row in legacy_rows]
            entries.extend(structured_memory_to_dict(row) for row in structured_rows)
            sort_key = "occurred_at" if memory_layer == "timeline" else "updated_at"
            entries.sort(key=lambda item: item.get(sort_key) or "", reverse=True)
            return entries[offset:offset + limit]

        lexical = [(f"legacy:{row.id}", memory_to_dict(row)) for row in legacy_rows]
        lexical.extend(
            (f"structured:{row.id}", structured_memory_to_dict(row))
            for row in structured_rows
        )
        lexical.sort(key=lambda item: item[1].get("updated_at") or "", reverse=True)
        ranked_lists = [lexical]

        query_vector = embed(query)
        if query_vector is not None:
            vector_results: list[tuple[float, str, dict[str, Any]]] = []
            if is_postgres() and PGVECTOR_AVAILABLE:
                # Native path: pgvector computes cosine distance inside SQL so the
                # HNSW index and the ACL predicates are applied before ranking.
                if include_structured:
                    distance = MemoryChunk.embedding.cosine_distance(query_vector)
                    vector_stmt = (
                        select(StructuredMemory, func.min(distance).label("distance"))
                        .join(MemoryChunk, MemoryChunk.memory_id == StructuredMemory.id)
                        .where(
                            StructuredMemory.deleted_at.is_(None),
                            StructuredMemory.superseded_at.is_(None),
                            MemoryChunk.memory_version == StructuredMemory.version,
                            MemoryChunk.embedding.isnot(None),
                        )
                    )
                    if principal is not None:
                        vector_stmt = vector_stmt.where(
                            StructuredMemory.tenant_id == principal.tenant_id
                        )
                    if owner_agent_id:
                        vector_stmt = vector_stmt.where(
                            StructuredMemory.owner_agent_id == owner_agent_id
                        )
                    if memory_layer:
                        vector_stmt = vector_stmt.where(
                            StructuredMemory.memory_layer == memory_layer
                        )
                    if category:
                        vector_stmt = vector_stmt.where(StructuredMemory.category == category)
                    if event_key:
                        vector_stmt = vector_stmt.where(StructuredMemory.event_key == event_key)
                    if occurred_from:
                        vector_stmt = vector_stmt.where(
                            StructuredMemory.occurred_at >= occurred_from
                        )
                    if occurred_to:
                        vector_stmt = vector_stmt.where(
                            StructuredMemory.occurred_at <= occurred_to
                        )
                    vector_stmt = (
                        vector_stmt.group_by(StructuredMemory.id)
                        .order_by(func.min(distance)).limit(candidate_limit)
                    )
                    vector_results.extend(
                        (float(distance_value), f"structured:{row.id}", structured_memory_to_dict(row))
                        for row, distance_value in s.execute(vector_stmt).all()
                    )

                if include_legacy and not any((category, event_key, occurred_from, occurred_to)):
                    legacy_distance = Memory.embedding.cosine_distance(query_vector)
                    legacy_vector_stmt = select(Memory, legacy_distance.label("distance")).where(
                        Memory.deleted_at.is_(None), Memory.embedding.isnot(None)
                    )
                    if principal is not None:
                        legacy_vector_stmt = legacy_vector_stmt.where(
                            Memory.tenant_id == principal.tenant_id
                        )
                    if owner_agent_id:
                        legacy_vector_stmt = legacy_vector_stmt.where(
                            Memory.owner_agent_id == owner_agent_id
                        )
                    legacy_vector_stmt = legacy_vector_stmt.order_by(legacy_distance).limit(candidate_limit)
                    vector_results.extend(
                        (float(distance_value), f"legacy:{row.id}", memory_to_dict(row))
                        for row, distance_value in s.execute(legacy_vector_stmt).all()
                    )
            else:
                # Portable path (default SQLite deployment, or PG without the
                # pgvector package): vectors are stored as sparse JSON and scored
                # here. The same tenant/layer/version filters are applied first.
                if include_structured:
                    chunk_stmt = (
                        select(MemoryChunk.embedding, StructuredMemory)
                        .join(StructuredMemory, MemoryChunk.memory_id == StructuredMemory.id)
                        .where(
                            StructuredMemory.deleted_at.is_(None),
                            StructuredMemory.superseded_at.is_(None),
                            MemoryChunk.memory_version == StructuredMemory.version,
                            MemoryChunk.embedding_status == "ready",
                        )
                    )
                    if principal is not None:
                        chunk_stmt = chunk_stmt.where(StructuredMemory.tenant_id == principal.tenant_id)
                    if owner_agent_id:
                        chunk_stmt = chunk_stmt.where(StructuredMemory.owner_agent_id == owner_agent_id)
                    if memory_layer:
                        chunk_stmt = chunk_stmt.where(StructuredMemory.memory_layer == memory_layer)
                    if category:
                        chunk_stmt = chunk_stmt.where(StructuredMemory.category == category)
                    if event_key:
                        chunk_stmt = chunk_stmt.where(StructuredMemory.event_key == event_key)
                    if occurred_from:
                        chunk_stmt = chunk_stmt.where(StructuredMemory.occurred_at >= occurred_from)
                    if occurred_to:
                        chunk_stmt = chunk_stmt.where(StructuredMemory.occurred_at <= occurred_to)

                    best: dict[str, tuple[float, dict[str, Any]]] = {}
                    for payload, row in s.execute(
                        chunk_stmt.limit(max(2000, candidate_limit * 20))
                    ).all():
                        vector = deserialize(payload)
                        if vector is None or len(vector) != len(query_vector):
                            continue
                        distance_value = _cosine_distance(query_vector, vector)
                        key = f"structured:{row.id}"
                        if key not in best or distance_value < best[key][0]:
                            best[key] = (distance_value, structured_memory_to_dict(row))
                    vector_results.extend(
                        (distance_value, key, item) for key, (distance_value, item) in best.items()
                    )

                if include_legacy and not any((category, event_key, occurred_from, occurred_to)):
                    legacy_scan = select(Memory).where(
                        Memory.deleted_at.is_(None), Memory.embedding.isnot(None)
                    )
                    if principal is not None:
                        legacy_scan = legacy_scan.where(Memory.tenant_id == principal.tenant_id)
                    if owner_agent_id:
                        legacy_scan = legacy_scan.where(Memory.owner_agent_id == owner_agent_id)
                    for row in s.execute(
                        legacy_scan.limit(max(1000, candidate_limit * 10))
                    ).scalars().all():
                        vector = deserialize(row.embedding)
                        if vector is None or len(vector) != len(query_vector):
                            continue
                        vector_results.append((
                            _cosine_distance(query_vector, vector),
                            f"legacy:{row.id}", memory_to_dict(row),
                        ))

            vector_results.sort(key=lambda item: item[0])
            ranked_lists.append([(key, item) for _, key, item in vector_results[:candidate_limit]])

        scores: dict[str, float] = {}
        items_by_key: dict[str, dict[str, Any]] = {}
        for candidates in ranked_lists:
            for rank, (key, item) in enumerate(candidates, start=1):
                scores[key] = scores.get(key, 0.0) + 1.0 / (60 + rank)
                items_by_key[key] = item
        ordered = sorted(
            items_by_key,
            key=lambda key: (scores[key], items_by_key[key].get("updated_at") or ""),
            reverse=True,
        )
        return [items_by_key[key] for key in ordered[offset:offset + limit]]


def _store_embedding(m: Memory, content: str) -> None:
    """Compute and attach an embedding if a provider is configured."""
    vec = embed(content)
    if vec is None:
        return
    m.embedding = vec if (is_postgres() and PGVECTOR_AVAILABLE) else serialize(vec)


def create_memory(principal: Principal, content: str, memory_type: str = "fact",
                 tags: list[str] | None = None) -> dict[str, Any]:
    require_scope(principal, "write")
    memory_id = str(uuid4())
    with session() as s:
        ts = now()
        m = Memory(
            id=memory_id, tenant_id=principal.tenant_id, owner_agent_id=principal.agent_id,
            content=content, memory_type=memory_type,
            tags=json.dumps(tags or [], ensure_ascii=False),
            created_at=ts, updated_at=ts, version=1,
        )
        _store_embedding(m, content)
        s.add(m)
        _audit(s, principal, "create", "memory", memory_id)
        s.flush()
        return memory_to_dict(m)


def get_memory(memory_id: str, principal: Principal | None = None) -> dict[str, Any] | None:
    if principal is not None:
        require_scope(principal, "read")
    with session() as s:
        stmt = select(Memory).where(Memory.id == memory_id, Memory.deleted_at.is_(None))
        if principal is not None:
            stmt = stmt.where(Memory.tenant_id == principal.tenant_id)
        m = s.execute(stmt).scalars().first()
        if m:
            return memory_to_dict(m)
        structured_stmt = select(StructuredMemory).where(
            StructuredMemory.id == memory_id,
            StructuredMemory.deleted_at.is_(None),
        )
        if principal is not None:
            structured_stmt = structured_stmt.where(
                StructuredMemory.tenant_id == principal.tenant_id
            )
        structured = s.execute(structured_stmt).scalars().first()
        return structured_memory_to_dict(structured) if structured else None


def update_memory(principal: Principal, memory_id: str, content: str | None = None,
                  memory_type: str | None = None, tags: list[str] | None = None,
                  expected_version: int | None = None,
                  change_note: str | None = None) -> dict[str, Any] | None:
    require_scope(principal, "write")
    current = None
    with session() as s:
        m = s.get(Memory, memory_id)
        if m and m.deleted_at is None and m.owner_agent_id == principal.agent_id \
                and m.tenant_id == principal.tenant_id:
            if content is not None:
                m.content = content
                _store_embedding(m, content)
            if memory_type is not None:
                m.memory_type = memory_type
            if tags is not None:
                m.tags = _normalize_tags(tags)
            m.updated_at = now()
            m.version = (m.version or 1) + 1
            _audit(s, principal, "update", "memory", memory_id)
            s.flush()
            return memory_to_dict(m)
        structured_stmt = select(StructuredMemory).where(
            StructuredMemory.id == memory_id,
            StructuredMemory.tenant_id == principal.tenant_id,
            StructuredMemory.owner_agent_id == principal.agent_id,
            StructuredMemory.deleted_at.is_(None),
        )
        structured = s.execute(structured_stmt).scalars().first()
        if structured:
            current = structured_memory_to_dict(structured)
    if not current:
        return None
    if current["memory_layer"] == "timeline":
        raise InvalidInput("timeline memories are immutable; append a correction that supersedes it")
    next_content = content if content is not None else current["content"]
    next_tags = tags if tags is not None else current["tags"]
    if current["memory_layer"] == "summary":
        return upsert_agent_summary(
            principal, next_content, current.get("source_watermark"),
            expected_version, change_note,
        )
    if current["memory_layer"] == "event":
        return update_event_memory(
            principal, memory_id, next_content, next_tags,
            expected_version, change_note,
        )
    return None


def delete_memory(principal: Principal, memory_id: str) -> bool:
    require_scope(principal, "write")
    with session() as s:
        m = s.get(Memory, memory_id)
        if not m:
            structured = s.get(StructuredMemory, memory_id)
            if (not structured or structured.deleted_at is not None
                    or structured.owner_agent_id != principal.agent_id
                    or structured.tenant_id != principal.tenant_id):
                return False
            ts = now()
            structured.deleted_at = ts
            structured.updated_at = ts
            structured.version = (structured.version or 1) + 1
            _audit(s, principal, "delete", structured.memory_layer, memory_id)
            return True
        if (m.deleted_at is not None or m.owner_agent_id != principal.agent_id
                or m.tenant_id != principal.tenant_id):
            return False
        ts = now()
        m.deleted_at = ts
        m.updated_at = ts
        m.version = (m.version or 1) + 1
        _audit(s, principal, "delete", "memory", memory_id)
        return True


# --------------------------------------------------------------------------- #
# Negotiation threads
# --------------------------------------------------------------------------- #
def start_thread(principal: Principal, subject: str, channel: str, recipients: list[str],
                 related_memory_ids: list[str]) -> dict[str, Any]:
    require_scope(principal, "write")
    thread_id = str(uuid4())
    with session() as s:
        ts = now()
        t = NegotiationThread(
            id=thread_id, tenant_id=principal.tenant_id, creator_agent_id=principal.agent_id,
            channel=channel, subject=subject,
            related_memory_ids=json.dumps(related_memory_ids, ensure_ascii=False),
            status="open", created_at=ts, updated_at=ts,
        )
        for recipient in set(recipients):
            t.recipients.append(ThreadRecipient(
                thread_id=thread_id, agent_id=recipient, tenant_id=principal.tenant_id))
        s.add(t)
        _audit(s, principal, "start", "thread", thread_id, {"channel": channel})
        s.flush()
        return thread_to_dict(t)


def _visible_thread_ids(s, principal: Principal) -> set[str]:
    stmt = (
        select(NegotiationThread.id)
        .outerjoin(ThreadRecipient, ThreadRecipient.thread_id == NegotiationThread.id)
        .where(NegotiationThread.tenant_id == principal.tenant_id)
        .where(or_(
            NegotiationThread.channel == "global",
            NegotiationThread.creator_agent_id == principal.agent_id,
            ThreadRecipient.agent_id == principal.agent_id,
        ))
    )
    return {row[0] for row in s.execute(stmt).all()}


def get_thread(principal: Principal, thread_id: str) -> dict[str, Any] | None:
    require_scope(principal, "read")
    with session() as s:
        t = s.get(NegotiationThread, thread_id)
        if not t or t.tenant_id != principal.tenant_id:
            return None
        if t.channel != "global" and principal.agent_id != t.creator_agent_id and \
                principal.agent_id not in {r.agent_id for r in t.recipients}:
            return None
        return thread_to_dict(t)


def list_threads(principal: Principal, limit: int = 50) -> list[dict[str, Any]]:
    require_scope(principal, "read")
    limit = max(1, min(limit, 100))
    with session() as s:
        visible = _visible_thread_ids(s, principal)
        if not visible:
            return []
        stmt = (
            select(NegotiationThread)
            .where(NegotiationThread.id.in_(visible), NegotiationThread.tenant_id == principal.tenant_id)
            .order_by(NegotiationThread.updated_at.desc())
            .limit(limit)
        )
        return [thread_to_dict(t) for t in s.execute(stmt).scalars().all()]


def post_message(principal: Principal, thread_id: str, content: str,
                 reply_to_message_id: str | None = None) -> dict[str, Any] | None:
    require_scope(principal, "write")
    with session() as s:
        t = s.get(NegotiationThread, thread_id)
        if not t or t.tenant_id != principal.tenant_id:
            return None
        allowed = (
            t.channel == "global"
            or principal.agent_id == t.creator_agent_id
            or any(r.agent_id == principal.agent_id for r in t.recipients)
        )
        if not allowed:
            return None
        if t.status == "closed":
            raise InvalidInput("Thread is closed")
        if reply_to_message_id:
            reply = s.get(NegotiationMessage, reply_to_message_id)
            if not reply or reply.thread_id != thread_id or reply.tenant_id != principal.tenant_id:
                raise InvalidInput("Reply target must be a message in this thread")
        msg = NegotiationMessage(
            id=str(uuid4()), thread_id=thread_id, tenant_id=principal.tenant_id,
            sender_agent_id=principal.agent_id, content=content,
            reply_to_message_id=reply_to_message_id, created_at=now(),
        )
        s.add(msg)
        t.updated_at = now()
        _audit(s, principal, "post", "thread", thread_id, {"message_id": msg.id})
        s.flush()
        return msg_to_dict(msg)


def resolve_thread(principal: Principal, thread_id: str, status: str) -> dict[str, Any] | None:
    require_scope(principal, "write")
    with session() as s:
        t = s.get(NegotiationThread, thread_id)
        if (not t or t.tenant_id != principal.tenant_id
                or t.creator_agent_id != principal.agent_id
                or status not in ("resolved", "closed")):
            return None
        t.status = status
        t.updated_at = now()
        _audit(s, principal, status, "thread", thread_id)
        return thread_to_dict(t)
