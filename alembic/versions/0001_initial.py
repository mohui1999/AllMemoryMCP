"""initial schema (multi-tenant)

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-09
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memories",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("owner_agent_id", sa.String(length=128), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("memory_type", sa.String(length=64), nullable=False),
        sa.Column("tags", sa.String(length=4000), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.Column("deleted_at", sa.String(length=32), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("memories_tenant_updated_idx", "memories", ["tenant_id", "updated_at"])
    op.create_index("memories_tenant_owner_idx", "memories", ["tenant_id", "owner_agent_id"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("actor_agent_id", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("object_type", sa.String(length=64), nullable=False),
        sa.Column("object_id", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("details", sa.String(length=4000), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_log_tenant_actor", "audit_log", ["tenant_id", "actor_agent_id"])

    op.create_table(
        "negotiation_threads",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("creator_agent_id", sa.String(length=128), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("subject", sa.String(length=300), nullable=False),
        sa.Column("related_memory_ids", sa.String(length=2000), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("channel IN ('direct','global')", name="ck_thread_channel"),
        sa.CheckConstraint("status IN ('open','resolved','closed')", name="ck_thread_status"),
    )
    op.create_index("threads_tenant_updated_idx", "negotiation_threads", ["tenant_id", "updated_at"])

    op.create_table(
        "thread_recipients",
        sa.Column("thread_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["thread_id"], ["negotiation_threads.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("thread_id", "agent_id"),
    )

    op.create_table(
        "negotiation_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("thread_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("sender_agent_id", sa.String(length=128), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("reply_to_message_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["thread_id"], ["negotiation_threads.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reply_to_message_id"], ["negotiation_messages.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("messages_thread_idx", "negotiation_messages", ["thread_id", "created_at"])


def downgrade() -> None:
    op.drop_index("messages_thread_idx", table_name="negotiation_messages")
    op.drop_table("negotiation_messages")
    op.drop_table("thread_recipients")
    op.drop_table("negotiation_threads")
    op.drop_index("ix_audit_log_tenant_actor", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("memories_tenant_owner_idx", table_name="memories")
    op.drop_index("memories_tenant_updated_idx", table_name="memories")
    op.drop_table("memories")
