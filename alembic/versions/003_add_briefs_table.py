"""Add briefs table for Stage 8 output storage.

Revision ID: 003_add_briefs_table
Revises: 002_add_transcript_cols
Create Date: 2026-03-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "003_add_briefs_table"
down_revision: Union[str, None] = "002_add_transcript_cols"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "briefs",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("content_markdown", sa.Text(), nullable=False),
        sa.Column("sections", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("claim_ids", postgresql.ARRAY(sa.UUID()), server_default=sa.text("'{}'::uuid[]"), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'draft'"), nullable=False),
        sa.Column("generation_cost", sa.Numeric(), server_default=sa.text("0"), nullable=False),
        sa.Column("delivered_telegram", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("delivered_email", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_briefs_created_at", "briefs", ["created_at"])
    op.create_index("ix_briefs_status", "briefs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_briefs_status", table_name="briefs")
    op.drop_index("ix_briefs_created_at", table_name="briefs")
    op.drop_table("briefs")
