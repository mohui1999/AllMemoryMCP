"""SQLAlchemy engine, session, and ORM models for AllMemoryMCP.

Storage is selected by DATABASE_URL:
  - sqlite:///path/to/memory.db   (dev / single-file, default)
  - postgresql+psycopg://user:pass@host:5432/db  (prod, with pgvector)

The same models back both backends; business code in memory_service.py never
touches raw SQL. Migrations for PostgreSQL are managed with Alembic; SQLite dev
databases are created on demand via Base.metadata.create_all (see initialize()).
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from sqlalchemy import (
    CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text,
    create_engine, func, select,
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker,
)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Principal:
    """An authenticated identity: tenant + agent, plus the granted scopes.

    Scopes: "read", "write", "admin". Static env tokens grant ("read", "write").
    DB-issued credentials carry explicit scopes. Admin tokens are a separate
    concept (see ADMIN_TOKENS) and are not used for agent operations.
    """

    tenant_id: str
    agent_id: str
    scopes: tuple[str, ...] = ("read", "write")

    def has(self, scope: str) -> bool:
        return scope in self.scopes


def database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    # Fall back to the legacy DATABASE_PATH (SQLite) for backward compatibility.
    path = os.path.expanduser(os.getenv("DATABASE_PATH", "./data/memory.db"))
    return f"sqlite:///{path}"


_engine = None
_SessionLocal = None


def engine():
    global _engine
    if _engine is None:
        url = database_url()
        connect_args = {}
        if url.startswith("sqlite"):
            # Allow the REST and MCP processes (or supervisord children) to share
            # the connection, matching the original WAL setup.
            connect_args = {"check_same_thread": False}
        _engine = create_engine(url, future=True, pool_pre_ping=True, connect_args=connect_args)
    return _engine


def SessionLocal() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=engine(), expire_on_commit=False, future=True)
    return _SessionLocal


@contextmanager
def session() -> Iterator[Any]:
    """Yield a committed-or-rolled-back session."""
    s = SessionLocal()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------- #
# Embedding column type (optional pgvector on PostgreSQL; Text on SQLite)
# --------------------------------------------------------------------------- #
_EMBED_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))
try:
    from pgvector.sqlalchemy import Vector  # type: ignore
    EMBEDDING_TYPE = Vector(_EMBED_DIM).with_variant(Text(), "sqlite")
    PGVECTOR_AVAILABLE = True
except Exception:  # noqa: BLE001 - pgvector only required for Postgres vector search
    Vector = None  # type: ignore
    EMBEDDING_TYPE = Text()
    PGVECTOR_AVAILABLE = False


def is_postgres() -> bool:
    return database_url().startswith("postgresql")


class Memory(Base):
    __tablename__ = "memories"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    owner_agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    memory_type: Mapped[str] = mapped_column(String(64), nullable=False, default="fact")
    tags: Mapped[str] = mapped_column(String(4000), nullable=False, default="[]")
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=now)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False, default=now)
    deleted_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    embedding: Mapped[Any] = mapped_column(EMBEDDING_TYPE, nullable=True)

    __table_args__ = (
        Index("memories_tenant_updated_idx", "tenant_id", "updated_at"),
        Index("memories_tenant_owner_idx", "tenant_id", "owner_agent_id"),
    )


class StructuredMemory(Base):
    """Summary, timeline, and event memories with a shared searchable shape."""

    __tablename__ = "structured_memories"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    owner_agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    memory_layer: Mapped[str] = mapped_column(String(24), nullable=False)
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    event_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    occurred_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    source_watermark: Mapped[str | None] = mapped_column(String(40), nullable=True)
    tags: Mapped[str] = mapped_column(String(4000), nullable=False, default="[]")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=now)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False, default=now)
    deleted_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    supersedes_memory_id: Mapped[str | None] = mapped_column(
        ForeignKey("structured_memories.id"), nullable=True
    )
    superseded_at: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "memory_layer IN ('summary','timeline','event')",
            name="ck_structured_memory_layer",
        ),
        CheckConstraint(
            "memory_layer != 'timeline' OR occurred_at IS NOT NULL",
            name="ck_timeline_occurred_at",
        ),
        CheckConstraint(
            "memory_layer != 'event' OR (category IS NOT NULL AND event_key IS NOT NULL)",
            name="ck_event_identity",
        ),
        Index("structured_tenant_updated_idx", "tenant_id", "updated_at"),
        Index("structured_tenant_layer_idx", "tenant_id", "memory_layer", "updated_at"),
        Index("structured_tenant_owner_idx", "tenant_id", "owner_agent_id", "updated_at"),
        Index(
            "structured_timeline_time_idx", "tenant_id", "occurred_at",
            sqlite_where=text("memory_layer = 'timeline' AND deleted_at IS NULL"),
            postgresql_where=text("memory_layer = 'timeline' AND deleted_at IS NULL"),
        ),
        Index(
            "structured_summary_owner_uq", "tenant_id", "owner_agent_id", unique=True,
            sqlite_where=text("memory_layer = 'summary' AND deleted_at IS NULL"),
            postgresql_where=text("memory_layer = 'summary' AND deleted_at IS NULL"),
        ),
        Index(
            "structured_event_owner_key_uq", "tenant_id", "owner_agent_id", "category", "event_key",
            unique=True,
            sqlite_where=text("memory_layer = 'event' AND deleted_at IS NULL"),
            postgresql_where=text("memory_layer = 'event' AND deleted_at IS NULL"),
        ),
    )


class MemoryRevision(Base):
    """Immutable snapshots for updates to summaries and event memories."""

    __tablename__ = "memory_revisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    memory_id: Mapped[str] = mapped_column(
        ForeignKey("structured_memories.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    change_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=now)

    __table_args__ = (
        UniqueConstraint("memory_id", "version", name="uq_memory_revision_version"),
        Index("memory_revisions_tenant_memory_idx", "tenant_id", "memory_id", "version"),
    )


class MemoryChunk(Base):
    """Versioned retrieval chunks; canonical text remains in StructuredMemory."""

    __tablename__ = "memory_chunks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    memory_id: Mapped[str] = mapped_column(
        ForeignKey("structured_memories.id", ondelete="CASCADE"), nullable=False
    )
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    memory_layer: Mapped[str] = mapped_column(String(24), nullable=False)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    event_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    occurred_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    memory_version: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[Any] = mapped_column(EMBEDDING_TYPE, nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    embedding_status: Mapped[str] = mapped_column(String(16), nullable=False, default="disabled")
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=now)

    __table_args__ = (
        UniqueConstraint("memory_id", "memory_version", "chunk_index", name="uq_memory_chunk_version"),
        CheckConstraint(
            "embedding_status IN ('ready','disabled','failed')",
            name="ck_memory_chunk_embedding_status",
        ),
        Index("memory_chunks_tenant_memory_idx", "tenant_id", "memory_id", "memory_version"),
        Index("memory_chunks_tenant_layer_idx", "tenant_id", "memory_layer", "category", "event_key"),
    )


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    actor_agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=now)
    details: Mapped[str] = mapped_column(String(4000), nullable=False, default="{}")

    __table_args__ = (Index("ix_audit_log_tenant_actor", "tenant_id", "actor_agent_id"),)


class NegotiationThread(Base):
    __tablename__ = "negotiation_threads"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    creator_agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False)
    related_memory_ids: Mapped[str] = mapped_column(String(2000), nullable=False, default="[]")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=now)
    updated_at: Mapped[str] = mapped_column(String(32), nullable=False, default=now)

    recipients: Mapped[list["ThreadRecipient"]] = relationship(
        back_populates="thread", cascade="all, delete-orphan"
    )
    messages: Mapped[list["NegotiationMessage"]] = relationship(
        back_populates="thread", cascade="all, delete-orphan",
        order_by="NegotiationMessage.created_at",
    )

    __table_args__ = (
        Index("threads_tenant_updated_idx", "tenant_id", "updated_at"),
        CheckConstraint("channel IN ('direct','global')", name="ck_thread_channel"),
        CheckConstraint("status IN ('open','resolved','closed')", name="ck_thread_status"),
    )


class ThreadRecipient(Base):
    __tablename__ = "thread_recipients"
    thread_id: Mapped[str] = mapped_column(ForeignKey("negotiation_threads.id", ondelete="CASCADE"), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    thread: Mapped[NegotiationThread] = relationship(back_populates="recipients")


class NegotiationMessage(Base):
    __tablename__ = "negotiation_messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    thread_id: Mapped[str] = mapped_column(ForeignKey("negotiation_threads.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    sender_agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    reply_to_message_id: Mapped[str | None] = mapped_column(ForeignKey("negotiation_messages.id"), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=now)
    thread: Mapped[NegotiationThread] = relationship(back_populates="messages")

    __table_args__ = (
        Index("messages_thread_idx", "thread_id", "created_at"),
    )


class AgentCredential(Base):
    """Managed, DB-backed agent credential (issued/revoked/rotated at runtime).

    Replaces the need to hard-code every agent token in AGENT_TOKENS. The raw
    token is shown only once at issue time; only its SHA-256 hash is stored.
    """

    __tablename__ = "agent_credentials"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scopes: Mapped[str] = mapped_column(String(4000), nullable=False, default='["read","write"]')
    expires_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    revoked_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), nullable=False, default=now)

    __table_args__ = (
        Index("ix_credentials_tenant_agent", "tenant_id", "agent_id"),
    )


def initialize() -> None:
    """Create tables. For SQLite dev this is sufficient; PostgreSQL uses Alembic."""
    if database_url().startswith("sqlite"):
        Base.metadata.create_all(engine())


def memory_to_dict(m: Memory) -> dict[str, Any]:
    import json
    return {
        "id": m.id,
        "owner_agent_id": m.owner_agent_id,
        "memory_layer": "legacy",
        "content": m.content,
        "title": None,
        "memory_type": m.memory_type,
        "category": None,
        "event_key": None,
        "occurred_at": None,
        "source_watermark": None,
        "tags": json.loads(m.tags) if m.tags else [],
        "created_at": m.created_at,
        "updated_at": m.updated_at,
        "version": m.version,
    }


def structured_memory_to_dict(m: StructuredMemory) -> dict[str, Any]:
    import json
    return {
        "id": m.id,
        "owner_agent_id": m.owner_agent_id,
        "memory_layer": m.memory_layer,
        "content": m.content,
        "title": m.title,
        "memory_type": m.memory_layer,
        "category": m.category,
        "event_key": m.event_key,
        "occurred_at": m.occurred_at,
        "source_watermark": m.source_watermark,
        "tags": json.loads(m.tags) if m.tags else [],
        "created_at": m.created_at,
        "updated_at": m.updated_at,
        "version": m.version,
        "supersedes_memory_id": m.supersedes_memory_id,
        "superseded_at": m.superseded_at,
    }


def memory_revision_to_dict(revision: MemoryRevision) -> dict[str, Any]:
    return {
        "id": revision.id,
        "memory_id": revision.memory_id,
        "owner_agent_id": revision.owner_agent_id,
        "version": revision.version,
        "content": revision.content,
        "change_note": revision.change_note,
        "created_at": revision.created_at,
    }


def thread_to_dict(t: NegotiationThread, messages: bool = True) -> dict[str, Any]:
    import json
    return {
        "id": t.id,
        "creator_agent_id": t.creator_agent_id,
        "channel": t.channel,
        "subject": t.subject,
        "related_memory_ids": json.loads(t.related_memory_ids) if t.related_memory_ids else [],
        "status": t.status,
        "recipients": [r.agent_id for r in t.recipients],
        "created_at": t.created_at,
        "updated_at": t.updated_at,
        "messages": (
            [msg_to_dict(msg) for msg in t.messages] if messages else []
        ),
    }


def msg_to_dict(msg: NegotiationMessage) -> dict[str, Any]:
    return {
        "id": msg.id,
        "thread_id": msg.thread_id,
        "sender_agent_id": msg.sender_agent_id,
        "content": msg.content,
        "reply_to_message_id": msg.reply_to_message_id,
        "created_at": msg.created_at,
    }


def like_ignore_case(column, needle: str):
    """Case-insensitive LIKE that works on both SQLite and PostgreSQL."""
    return func.lower(column).like(func.lower(f"%{needle}%"))
