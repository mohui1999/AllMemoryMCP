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
    CheckConstraint, ForeignKey, Index, Integer, String, Text,
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
    EMBEDDING_TYPE = Vector(_EMBED_DIM)
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
        "content": m.content,
        "memory_type": m.memory_type,
        "tags": json.loads(m.tags) if m.tags else [],
        "created_at": m.created_at,
        "updated_at": m.updated_at,
        "version": m.version,
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
