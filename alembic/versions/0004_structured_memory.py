"""add structured memory layers and chunk vector index

Revision ID: 0004_structured_memory
Revises: 0003_agent_credentials
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op

try:
    from pgvector.sqlalchemy import Vector  # type: ignore
except Exception:  # noqa: BLE001
    Vector = None  # type: ignore

revision = "0004_structured_memory"
down_revision = "0003_agent_credentials"
branch_labels = None
depends_on = None

_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))


def upgrade() -> None:
    bind = op.get_bind()
    is_pgvector = bind.dialect.name == "postgresql" and Vector is not None
    embedding_type = Vector(_DIM) if is_pgvector else sa.Text()

    if is_pgvector:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "structured_memories",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("owner_agent_id", sa.String(length=128), nullable=False),
        sa.Column("memory_layer", sa.String(length=24), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("category", sa.String(length=128), nullable=True),
        sa.Column("event_key", sa.String(length=200), nullable=True),
        sa.Column("occurred_at", sa.String(length=40), nullable=True),
        sa.Column("source_watermark", sa.String(length=40), nullable=True),
        sa.Column("tags", sa.String(length=4000), nullable=False, server_default="[]"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.Column("deleted_at", sa.String(length=32), nullable=True),
        sa.Column("supersedes_memory_id", sa.String(length=36), nullable=True),
        sa.Column("superseded_at", sa.String(length=32), nullable=True),
        sa.CheckConstraint(
            "memory_layer IN ('summary','timeline','event')",
            name="ck_structured_memory_layer",
        ),
        sa.CheckConstraint(
            "memory_layer != 'timeline' OR occurred_at IS NOT NULL",
            name="ck_timeline_occurred_at",
        ),
        sa.CheckConstraint(
            "memory_layer != 'event' OR (category IS NOT NULL AND event_key IS NOT NULL)",
            name="ck_event_identity",
        ),
        sa.ForeignKeyConstraint(["supersedes_memory_id"], ["structured_memories.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("structured_tenant_updated_idx", "structured_memories", ["tenant_id", "updated_at"])
    op.create_index(
        "structured_tenant_layer_idx", "structured_memories",
        ["tenant_id", "memory_layer", "updated_at"],
    )
    op.create_index(
        "structured_tenant_owner_idx", "structured_memories",
        ["tenant_id", "owner_agent_id", "updated_at"],
    )
    op.create_index(
        "structured_timeline_time_idx", "structured_memories", ["tenant_id", "occurred_at"],
        sqlite_where=sa.text("memory_layer = 'timeline' AND deleted_at IS NULL"),
        postgresql_where=sa.text("memory_layer = 'timeline' AND deleted_at IS NULL"),
    )
    op.create_index(
        "structured_summary_owner_uq", "structured_memories",
        ["tenant_id", "owner_agent_id"], unique=True,
        sqlite_where=sa.text("memory_layer = 'summary' AND deleted_at IS NULL"),
        postgresql_where=sa.text("memory_layer = 'summary' AND deleted_at IS NULL"),
    )
    op.create_index(
        "structured_event_owner_key_uq", "structured_memories",
        ["tenant_id", "owner_agent_id", "category", "event_key"], unique=True,
        sqlite_where=sa.text("memory_layer = 'event' AND deleted_at IS NULL"),
        postgresql_where=sa.text("memory_layer = 'event' AND deleted_at IS NULL"),
    )

    op.create_table(
        "memory_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("memory_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("owner_agent_id", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("change_note", sa.String(length=1000), nullable=True),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["memory_id"], ["structured_memories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("memory_id", "version", name="uq_memory_revision_version"),
    )
    op.create_index(
        "memory_revisions_tenant_memory_idx", "memory_revisions",
        ["tenant_id", "memory_id", "version"],
    )

    op.create_table(
        "memory_chunks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("memory_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("owner_agent_id", sa.String(length=128), nullable=False),
        sa.Column("memory_layer", sa.String(length=24), nullable=False),
        sa.Column("category", sa.String(length=128), nullable=True),
        sa.Column("event_key", sa.String(length=200), nullable=True),
        sa.Column("occurred_at", sa.String(length=40), nullable=True),
        sa.Column("memory_version", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", embedding_type, nullable=True),
        sa.Column("embedding_model", sa.String(length=128), nullable=True),
        sa.Column("embedding_status", sa.String(length=16), nullable=False, server_default="disabled"),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "embedding_status IN ('ready','disabled','failed')",
            name="ck_memory_chunk_embedding_status",
        ),
        sa.ForeignKeyConstraint(["memory_id"], ["structured_memories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("memory_id", "memory_version", "chunk_index", name="uq_memory_chunk_version"),
    )
    op.create_index(
        "memory_chunks_tenant_memory_idx", "memory_chunks",
        ["tenant_id", "memory_id", "memory_version"],
    )
    op.create_index(
        "memory_chunks_tenant_layer_idx", "memory_chunks",
        ["tenant_id", "memory_layer", "category", "event_key"],
    )
    if is_pgvector:
        op.execute(
            "CREATE INDEX memory_chunks_embedding_idx ON memory_chunks "
            "USING hnsw (embedding vector_cosine_ops)"
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS memory_chunks_embedding_idx")
    op.drop_index("memory_chunks_tenant_layer_idx", table_name="memory_chunks")
    op.drop_index("memory_chunks_tenant_memory_idx", table_name="memory_chunks")
    op.drop_table("memory_chunks")
    op.drop_index("memory_revisions_tenant_memory_idx", table_name="memory_revisions")
    op.drop_table("memory_revisions")
    op.drop_index("structured_event_owner_key_uq", table_name="structured_memories")
    op.drop_index("structured_summary_owner_uq", table_name="structured_memories")
    op.drop_index("structured_timeline_time_idx", table_name="structured_memories")
    op.drop_index("structured_tenant_owner_idx", table_name="structured_memories")
    op.drop_index("structured_tenant_layer_idx", table_name="structured_memories")
    op.drop_index("structured_tenant_updated_idx", table_name="structured_memories")
    op.drop_table("structured_memories")
