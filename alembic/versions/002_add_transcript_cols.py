"""Add transcript_url_pattern and transcript_parser to podcast_channels."""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "002_add_transcript_cols"
down_revision = "001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "podcast_channels",
        sa.Column("transcript_url_pattern", sa.Text(), nullable=True),
    )
    op.add_column(
        "podcast_channels",
        sa.Column("transcript_parser", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("podcast_channels", "transcript_parser")
    op.drop_column("podcast_channels", "transcript_url_pattern")
