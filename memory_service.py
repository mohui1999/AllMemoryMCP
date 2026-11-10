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
from sqlalchemy.exc import IntegrityError

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
    ).order_by(StructuredMemory.updated_at.desc())).scalars().first()


def get_agent_summary(principal: Principal, agent_id: str) -> dict[str, Any] | None:
    require_scope(principal, "read")
    with session() as s:
        row = _active_summary(s, principal.tenant_id, agent_id)
        return structured_memory_to_dict(row) if row else None


def upsert_agent_summary(principal: Principal, markdown: str,
                         source_watermark: str | None = None,
                         expected_version: int | None = None,
                         change_note: str | None = None) -> dict[str, Any]:
    """Create or REPLACE the Agent's ONE summary (its whole-memory overview).

    There is exactly one active summary per (tenant_id, agent_id), enforced by a
    partial unique index. Writing again never creates a second row: it replaces
    the whole Markdown document and appends a revision snapshot. The document is
    intentionally allowed to be long — it is the digest of ALL of this Agent's
    memories and the first thing other Agents should read.

    ``expected_version`` is optional: when omitted, the current version is read
    automatically and the update still uses an optimistic-lock WHERE clause. Pass
    it explicitly when two writers may update the same summary concurrently.
    """
    require_scope(principal, "write")
    markdown = markdown.strip()
    if not markdown:
        raise InvalidInput("summary markdown must not be empty")
    if len(markdown) > 100_000:
        raise InvalidInput("summary markdown exceeds 100000 characters")
    if source_watermark and len(source_watermark) > 40:
        raise InvalidInput("source_watermark exceeds 40 characters")
    chunks = _prepare_memory_chunks(markdown)

    for attempt in range(2):
        auto_version = False
        try:
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
                    effective_version = expected_version
                    if effective_version is None:
                        effective_version = current.version
                        auto_version = True
                    if current.version != effective_version:
                        raise VersionConflict(f"summary is at version {current.version}")
                    next_version = current.version + 1
                    result = s.execute(update(StructuredMemory).where(
                        StructuredMemory.id == current.id,
                        StructuredMemory.version == effective_version,
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
                payload = structured_memory_to_dict(memory)
                payload["auto_version"] = auto_version
                payload["layer_guide"] = {
                    "layer": "summary",
                    "meaning": LAYER_GUIDE[0]["meaning"],
                    "cardinality": LAYER_GUIDE[0]["cardinality"],
                    "hint": "This is the ONLY summary for this Agent. It was replaced, not appended; previous versions are in memory_revisions.",
                }
                return payload
        except (IntegrityError, VersionConflict):
            # A concurrent first write may have won the unique index, or changed
            # the version between our read and update. Retry once against the
            # fresh row when the caller did not pin a version.
            if attempt == 0 and expected_version is None:
                continue
            raise
    raise VersionConflict("summary changed during update; read it again")


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


# --------------------------------------------------------------------------- #
# Memory model: a machine-readable contract Agents can ask for at runtime
# --------------------------------------------------------------------------- #
# The three layers are easy to confuse from their names alone. Every Agent can
# retrieve this contract with memory_guide / GET /v1/memory-guide, and the most
# important parts are also attached to whoami / memory_context / search results.
MEMORY_MODEL_VERSION = "1.0"

LAYER_GUIDE: list[dict[str, Any]] = [
    {
        "layer": "summary",
        "priority": 1,
        "meaning": (
            "ONE long Markdown document that is the Agent's overview of ALL of its "
            "memories: who it is, what it is working on, key decisions, conventions, "
            "current state, and pointers to details. It is a digest of the whole "
            "memory set, not a single memory entry."
        ),
        "meaning_zh": "每个 Agent 只有一条，是它全部记忆的总体概要（可以比较长），别人读它就能知道这个 Agent 在干什么。",
        "cardinality": "exactly ONE active row per (tenant_id, agent_id); enforced by a unique index",
        "length": "may be long, up to 100000 characters; a structured Markdown overview is recommended",
        "read_with": "memory_agents (all Agents at once), memory_summary_get(agent_id), memory_context",
        "write_with": "memory_summary_write",
        "update_rule": "writing REPLACES the entire document; version snapshots are kept; expected_version is optional",
        "use_when": "FIRST thing to read, especially before reading another Agent's details",
    },
    {
        "layer": "timeline",
        "priority": 3,
        "meaning": (
            "An append-only, dated log entry: what happened at a specific time. Each "
            "row has occurred_at (timezone-qualified ISO 8601) and an optional category."
        ),
        "meaning_zh": "时间记忆：按发生时间追加的一条条记录，回答「什么时候发生了什么」，不可改写。",
        "cardinality": "many rows per Agent, one per dated event",
        "length": "short, up to 20000 characters",
        "read_with": "memory_search(memory_layer='timeline', occurred_from=..., occurred_to=..., category=...)",
        "write_with": "memory_timeline_write",
        "update_rule": "immutable; a correction appends a new row with supersedes_memory_id",
        "use_when": "you need the dated history behind a summary, or a specific time window",
    },
    {
        "layer": "event",
        "priority": 2,
        "meaning": (
            "The CURRENT state of one long-running subject, identified by "
            "(category, event_key). Examples: (development, AllMemoryMCP), "
            "(finance, long-term allocation), (health, allergy treatment). It is a "
            "living document, not a log entry: updates replace its content and keep "
            "revisions."
        ),
        "meaning_zh": "事件记忆：某个持续主题（开发/理财/健康等）的最新状态文档，按 category + event_key 区分，可反复更新并保留历史版本。",
        "cardinality": "ONE active row per (tenant_id, agent_id, category, event_key)",
        "length": "medium to long, up to 50000 characters",
        "read_with": "memory_search(memory_layer='event', category=..., event_key=...), memory_revisions(memory_id)",
        "write_with": "memory_event_write",
        "update_rule": "upsert by (category, event_key); use expected_version to avoid lost updates",
        "use_when": "you need the detailed current state of one subject named in a summary",
    },
    {
        "layer": "legacy",
        "priority": 4,
        "meaning": "Older free-form records created through memory_write; kept searchable for compatibility.",
        "meaning_zh": "旧版自由格式记忆，仅为兼容保留。",
        "cardinality": "many rows per Agent",
        "length": "up to 20000 characters",
        "read_with": "memory_search(memory_layer='legacy')",
        "write_with": "memory_write",
        "update_rule": "normal versioned updates",
        "use_when": "only when a legacy record is referenced",
    },
]

MEMORY_READING_ORDER: list[dict[str, Any]] = [
    {"step": 1, "tool": "memory_whoami", "why": "Confirm which Agent identity this connection uses."},
    {"step": 2, "tool": "memory_agents", "why": "Read EVERY Agent's single summary first to learn who is doing what."},
    {"step": 3, "tool": "memory_summary_get(agent_id=...)", "why": "Read the full long summary of the Agent you care about. Highest priority source."},
    {"step": 4, "tool": "memory_search(memory_layer='event', category=..., query=...)", "why": "Then fetch the detailed current state of a subject named in the summary."},
    {"step": 5, "tool": "memory_search(memory_layer='timeline', occurred_from=..., occurred_to=...)", "why": "Then fetch the dated history for a period or event."},
    {"step": 6, "tool": "memory_revisions(memory_id=...)", "why": "Optional: inspect how a summary or event document changed."},
]

MEMORY_WRITING_ORDER: list[dict[str, Any]] = [
    {"step": 1, "tool": "memory_timeline_write", "why": "Append dated facts as they happen."},
    {"step": 2, "tool": "memory_event_write", "why": "Keep the current state of each long-running subject up to date."},
    {"step": 3, "tool": "memory_summary_write", "why": "Periodically REPLACE your one summary with a refreshed overview of ALL your memories."},
]

MEMORY_RULES: list[str] = [
    "Every Agent has exactly ONE summary; it is a global digest of all its memories and may be long.",
    "Read another Agent's summary FIRST. Only fetch its event/timeline rows for the details you actually need.",
    "Other Agents can read a summary but only its owner can replace or delete it.",
    "A timeline row is immutable; corrections append a new row that supersedes the old one.",
    "An event memory is keyed by (category, event_key); use expected_version when updating.",
    "Do not create a second summary to record new information: update the existing one.",
]


def memory_guide(principal: Principal | None = None) -> dict[str, Any]:
    """Return the three-layer memory contract, in a form an Agent can act on."""
    if principal is not None:
        require_scope(principal, "read")
    return {
        "model_version": MEMORY_MODEL_VERSION,
        "principle": (
            "summary is the single, long, whole-memory overview of one Agent and has "
            "the highest read priority; event is the detailed current state per subject; "
            "timeline is the append-only dated history. Read the summary first, then "
            "drill into event/timeline only for what you need."
        ),
        "principle_zh": "每个 Agent 只有一条 summary，是全部记忆的总览（可长），读取优先级最高；先看它知道对方在干什么，再按需查 event / timeline 细节。",
        "layers": [dict(item) for item in LAYER_GUIDE],
        "reading_order": [dict(item) for item in MEMORY_READING_ORDER],
        "writing_order": [dict(item) for item in MEMORY_WRITING_ORDER],
        "rules": list(MEMORY_RULES),
    }


def list_agents(principal: Principal, summary_mode: str = "full",
                limit: int = 50) -> dict[str, Any]:
    """Directory of Agents in this tenant, each with its ONE summary up front.

    This is the cross-agent entry point: instead of guessing which Agent to
    inspect, a caller sees every Agent's global overview in one call and only
    then drills into its event/timeline rows.
    """
    require_scope(principal, "read")
    if summary_mode not in ("full", "preview", "none"):
        raise InvalidInput("summary_mode must be full, preview, or none")
    limit = max(1, min(limit, 100))
    with session() as s:
        rows = s.execute(
            select(
                StructuredMemory.owner_agent_id,
                StructuredMemory.memory_layer,
                StructuredMemory.category,
                func.count().label("count"),
                func.max(StructuredMemory.updated_at).label("last_updated_at"),
            ).where(
                StructuredMemory.tenant_id == principal.tenant_id,
                StructuredMemory.deleted_at.is_(None),
                StructuredMemory.superseded_at.is_(None),
            ).group_by(
                StructuredMemory.owner_agent_id,
                StructuredMemory.memory_layer,
                StructuredMemory.category,
            )
        ).all()
        legacy_rows = s.execute(
            select(
                Memory.owner_agent_id,
                func.count().label("count"),
                func.max(Memory.updated_at).label("last_updated_at"),
            ).where(
                Memory.tenant_id == principal.tenant_id,
                Memory.deleted_at.is_(None),
            ).group_by(Memory.owner_agent_id)
        ).all()

    agents: dict[str, dict[str, Any]] = {}

    def entry(agent_id: str) -> dict[str, Any]:
        return agents.setdefault(agent_id, {
            "agent_id": agent_id, "summary": None, "timeline_count": 0,
            "event_count": 0, "legacy_count": 0, "categories": set(),
            "last_updated_at": "",
        })

    for agent_id, layer, category, count, last_updated in rows:
        item = entry(agent_id)
        if layer == "timeline":
            item["timeline_count"] += int(count)
        elif layer == "event":
            item["event_count"] += int(count)
        if category:
            item["categories"].add(category)
        item["last_updated_at"] = max(item["last_updated_at"], last_updated or "")
    for agent_id, count, last_updated in legacy_rows:
        item = entry(agent_id)
        item["legacy_count"] += int(count)
        item["last_updated_at"] = max(item["last_updated_at"], last_updated or "")

    with session() as s:
        for item in agents.values():
            row = _active_summary(s, principal.tenant_id, item["agent_id"])
            if row and summary_mode != "none":
                summary = structured_memory_to_dict(row)
                if summary_mode == "preview" and len(summary["content"]) > 600:
                    summary["content"] = summary["content"][:600] + " …"
                    summary["content_truncated"] = True
                item["summary"] = summary
            item["has_summary"] = row is not None
            item["summary_version"] = row.version if row else None
            item["summary_updated_at"] = row.updated_at if row else None
            item["categories"] = sorted(item["categories"])

    ordered = sorted(
        agents.values(),
        key=lambda item: (item["has_summary"], item["last_updated_at"], item["agent_id"]),
        reverse=True,
    )[:limit]
    return {
        "tenant_id": principal.tenant_id,
        "caller_agent_id": principal.agent_id,
        "count": len(ordered),
        "summary_mode": summary_mode,
        "items": ordered,
        "model_version": MEMORY_MODEL_VERSION,
        "layers": [dict(item) for item in LAYER_GUIDE if item["layer"] != "legacy"],
        "reading_order": [dict(item) for item in MEMORY_READING_ORDER[:4]],
        "hint": (
            "Read item.summary first: it is that Agent's ONLY whole-memory overview "
            "and tells you what it is working on. For details, call memory_search "
            "with memory_layer='event' or 'timeline' and owner_agent_id set to that Agent."
        ),
    }


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


def _context_next_calls(target: str, categories: list[str]) -> list[dict[str, Any]]:
    """Concrete, copyable follow-up calls for drilling into the details."""
    calls: list[dict[str, Any]] = [
        {"tool": "memory_summary_get", "args": {"agent_id": target},
         "why": "Re-read this Agent's ONLY whole-memory overview at any time."},
        {"tool": "memory_search",
         "args": {"query": "<subject from the summary>", "memory_layer": "event",
                  "owner_agent_id": target},
         "why": "Fetch the detailed CURRENT STATE of one subject (category/event_key)."},
        {"tool": "memory_search",
         "args": {"memory_layer": "timeline", "owner_agent_id": target,
                  "occurred_from": "<ISO 8601>", "occurred_to": "<ISO 8601>"},
         "why": "Fetch the append-only dated history for a period."},
    ]
    for category in categories[:3]:
        calls.append({
            "tool": "memory_search",
            "args": {"memory_layer": "event", "owner_agent_id": target,
                     "category": category},
            "why": f"All current event knowledge in the '{category}' category.",
        })
    return calls


def get_memory_context(principal: Principal, agent_id: str | None = None,
                       query: str = "", category: str | None = None,
                       timeline_limit: int = 10, event_limit: int = 10,
                       detail_level: str = "full") -> dict[str, Any]:
    """Load one Agent's memory picture, with the summary deliberately first.

    ``detail_level='summary'`` returns only the high-priority overview plus the
    categories in use; use the returned ``next_calls`` to fetch event/timeline
    details on demand. ``full`` (default) also includes recent timeline rows and
    matching event rows for backward compatibility.
    """
    require_scope(principal, "read")
    if detail_level not in ("full", "summary"):
        raise InvalidInput("detail_level must be 'full' or 'summary'")
    target = agent_id or principal.agent_id
    timeline_limit = max(0, min(timeline_limit, 50))
    event_limit = max(0, min(event_limit, 50))
    if detail_level == "full":
        timeline = search_memories(
            query, max(1, timeline_limit), target, principal, 0, "timeline", category,
        ) if timeline_limit else []
        events = search_memories(
            query, max(1, event_limit), target, principal, 0, "event", category,
        ) if event_limit else []
    else:
        timeline, events = [], []
    summary = get_agent_summary(principal, target)
    categories = list_categories(principal, target)
    return {
        "agent_id": target,
        "model_version": MEMORY_MODEL_VERSION,
        "detail_level": detail_level,
        "details_omitted": detail_level == "summary",
        "priority": "summary",
        "layers": [dict(item) for item in LAYER_GUIDE if item["layer"] != "legacy"],
        "summary": summary,
        "timeline": timeline,
        "events": events,
        "categories": categories,
        "counts": {
            "timeline_returned": len(timeline),
            "event_returned": len(events),
            "categories": len(categories),
            "summary_chars": len(summary["content"]) if summary else 0,
        },
        "reading_order": [dict(item) for item in MEMORY_READING_ORDER[:5]],
        "next_calls": _context_next_calls(target, [c["category"] for c in categories]),
        "hint": (
            "Read 'summary' FIRST — it is this Agent's single, potentially long "
            "overview of everything it remembers, and tells you what it is working "
            "on. Only then use next_calls (or memory_search with memory_layer='event' "
            "or 'timeline') for the detailed records you actually need."
        ),
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
            # The single summary has the highest read priority: when browsing a
            # layer-free list, put every Agent's whole-memory overview on top.
            entries.sort(key=lambda item: 0 if item.get("memory_layer") == "summary" else 1)
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
        # A matching summary is the best next thing to read, so give it a small
        # RRF boost. It must still be a keyword/vector candidate to appear.
        for key, item in items_by_key.items():
            if item.get("memory_layer") == "summary":
                scores[key] += 0.5 / 61
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


def admin_delete_memory(principal: Principal, memory_id: str) -> dict[str, Any] | None:
    """Soft-delete any memory as the browser console's unified admin account.

    Agent-facing ``delete_memory`` keeps the uploader-only rule. This separate
    path is used only by an identity holding the ``admin`` scope, is limited to
    that identity's tenant (or all tenants for ``*``), and records the original
    owner in the audit log. It never removes rows or their revision history.
    """
    require_scope(principal, "admin")
    with session() as s:
        row = s.get(Memory, memory_id)
        if row is None:
            row = s.get(StructuredMemory, memory_id)
        if (row is None or row.deleted_at is not None
                or (principal.tenant_id != "*" and row.tenant_id != principal.tenant_id)):
            return None
        layer = getattr(row, "memory_layer", "legacy")
        owner = row.owner_agent_id
        ts = now()
        row.deleted_at = ts
        row.updated_at = ts
        row.version = (row.version or 1) + 1
        _audit(s, principal, "admin_delete", layer, memory_id,
               {"owner_agent_id": owner})
        return {"id": memory_id, "layer": layer, "owner_agent_id": owner,
                "deleted": True}


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
