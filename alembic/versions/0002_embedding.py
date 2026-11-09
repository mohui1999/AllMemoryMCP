"""add embedding column for semantic search

Revision ID: 0002_embedding
Revises: 0001_initial
"""
from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op

try:
    from pgvector.sqlalchemy import Vector  # type: ignore
except Exception:  # noqa: BLE001
    Vector = None  # type: ignore

revision = "0002_embedding"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql" and Vector is not None:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.add_column("memories", sa.Column("embedding", Vector(_DIM), nullable=True))
        op.execute(
            "CREATE INDEX IF NOT EXISTS memories_embedding_idx "
            "ON memories USING hnsw (embedding vector_cosine_ops)"
        )
    else:
        # SQLite (and Postgres-without-pgvector) stores the JSON vector as text.
        op.add_column("memories", sa.Column("embedding", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql" and Vector is not None:
        op.execute("DROP INDEX IF EXISTS memories_embedding_idx")
    op.drop_column("memories", "embedding")
