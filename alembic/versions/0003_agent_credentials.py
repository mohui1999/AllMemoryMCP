"""add agent_credentials table

Revision ID: 0003_agent_credentials
Revises: 0002_embedding
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_agent_credentials"
down_revision = "0002_embedding"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_credentials",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("agent_id", sa.String(length=128), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("scopes", sa.String(length=4000), nullable=False),
        sa.Column("expires_at", sa.String(length=32), nullable=True),
        sa.Column("revoked_at", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_credentials_token_hash", "agent_credentials", ["token_hash"])
    op.create_index("ix_credentials_tenant_agent", "agent_credentials", ["tenant_id", "agent_id"])


def downgrade() -> None:
    op.drop_index("ix_credentials_tenant_agent", table_name="agent_credentials")
    op.drop_index("ix_credentials_token_hash", table_name="agent_credentials")
    op.drop_table("agent_credentials")
