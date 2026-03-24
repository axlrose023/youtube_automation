

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "1f4c8c7d9e11"
down_revision: str | None = "52c2a5a03e5e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "emulation_sessions",
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("requested_duration_minutes", sa.Integer(), nullable=False),
        sa.Column(
            "requested_topics",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("mode", sa.String(length=32), nullable=True),
        sa.Column("fatigue", sa.Float(), nullable=True),
        sa.Column("bytes_downloaded", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_duration_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("videos_watched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("watched_videos_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("watched_ads_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "topics_searched",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "watched_videos",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "watched_ads",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "watched_ads_analytics",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("session_id", name=op.f("emulation_sessions_pkey")),
    )
    op.create_index(
        op.f("emulation_sessions_status_idx"),
        "emulation_sessions",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("emulation_sessions_queued_at_idx"),
        "emulation_sessions",
        ["queued_at"],
        unique=False,
    )
    op.create_index(
        op.f("emulation_sessions_started_at_idx"),
        "emulation_sessions",
        ["started_at"],
        unique=False,
    )
    op.create_index(
        op.f("emulation_sessions_finished_at_idx"),
        "emulation_sessions",
        ["finished_at"],
        unique=False,
    )
    op.create_index(
        op.f("emulation_sessions_mode_idx"),
        "emulation_sessions",
        ["mode"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("emulation_sessions_mode_idx"), table_name="emulation_sessions")
    op.drop_index(
        op.f("emulation_sessions_finished_at_idx"), table_name="emulation_sessions"
    )
    op.drop_index(op.f("emulation_sessions_started_at_idx"), table_name="emulation_sessions")
    op.drop_index(op.f("emulation_sessions_queued_at_idx"), table_name="emulation_sessions")
    op.drop_index(op.f("emulation_sessions_status_idx"), table_name="emulation_sessions")
    op.drop_table("emulation_sessions")
