"""Shared persistence and authorization for REST and MCP entry points.

Business logic is backend-agnostic (SQLAlchemy via db.session()). Every operation
is scoped to a Principal = (tenant_id, agent_id). Tenant isolation is enforced in
SQL for every read/write so a token from one tenant can never see or touch
another tenant's memories or negotiation threads.

Authentication returns a Principal (upgraded from a bare agent id). Ownership and
visibility rules live here.
"""
from __future__ import annotations

import json
import os
import secrets
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import or_, select

from db import (
    AuditLog, Memory, NegotiationMessage, NegotiationThread, Principal,
    ThreadRecipient, is_postgres, like_ignore_case, memory_to_dict, msg_to_dict,
    now, PGVECTOR_AVAILABLE, session, thread_to_dict,
)
from embeddings import embed, serialize


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
def search_memories(query: str = "", limit: int = 20, owner_agent_id: str | None = None,
                    principal: Principal | None = None, offset: int = 0) -> list[dict[str, Any]]:
    if principal is not None:
        require_scope(principal, "read")
    limit = max(1, min(limit, 100))
    with session() as s:
        base = select(Memory).where(Memory.deleted_at.is_(None))
        if principal is not None:
            base = base.where(Memory.tenant_id == principal.tenant_id)
        if owner_agent_id:
            base = base.where(Memory.owner_agent_id == owner_agent_id)

        # Semantic path: PostgreSQL + pgvector + a configured embedder.
        if query and principal is not None and is_postgres() and PGVECTOR_AVAILABLE:
            vec = embed(query)
            if vec is not None:
                stmt = base.where(Memory.embedding.isnot(None)).order_by(
                    Memory.embedding.cosine_distance(vec)
                ).offset(max(0, offset)).limit(limit)
                return [memory_to_dict(m) for m in s.execute(stmt).scalars().all()]

        # Keyword path (default; also the SQLite fallback).
        stmt = base
        if query:
            stmt = stmt.where(or_(
                like_ignore_case(Memory.content, query),
                like_ignore_case(Memory.tags, query),
                like_ignore_case(Memory.memory_type, query),
            ))
        stmt = stmt.order_by(Memory.updated_at.desc()).offset(max(0, offset)).limit(limit)
        return [memory_to_dict(m) for m in s.execute(stmt).scalars().all()]


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
        return memory_to_dict(m) if m else None


def update_memory(principal: Principal, memory_id: str, content: str | None = None,
                  memory_type: str | None = None, tags: list[str] | None = None) -> dict[str, Any] | None:
    require_scope(principal, "write")
    with session() as s:
        m = s.get(Memory, memory_id)
        if (not m or m.deleted_at is not None or m.owner_agent_id != principal.agent_id
                or m.tenant_id != principal.tenant_id):
            return None
        if content is not None:
            m.content = content
            _store_embedding(m, content)
        if memory_type is not None:
            m.memory_type = memory_type
        if tags is not None:
            m.tags = json.dumps(tags, ensure_ascii=False)
        m.updated_at = now()
        m.version = (m.version or 1) + 1
        _audit(s, principal, "update", "memory", memory_id)
        s.flush()
        return memory_to_dict(m)


def delete_memory(principal: Principal, memory_id: str) -> bool:
    require_scope(principal, "write")
    with session() as s:
        m = s.get(Memory, memory_id)
        if (not m or m.deleted_at is not None or m.owner_agent_id != principal.agent_id
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
            raise ValueError("Thread is closed")
        if reply_to_message_id:
            reply = s.get(NegotiationMessage, reply_to_message_id)
            if not reply or reply.thread_id != thread_id or reply.tenant_id != principal.tenant_id:
                raise ValueError("Reply target must be a message in this thread")
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
