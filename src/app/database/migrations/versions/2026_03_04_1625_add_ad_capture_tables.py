

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "52c2a5a03e5e"
down_revision: str | None = "aac9c3981adb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ad_captures",
        sa.Column("session_id", sa.String(length=64), nullable=False),
        sa.Column("ad_position", sa.Integer(), nullable=False),
        sa.Column("advertiser_domain", sa.String(length=255), nullable=True),
        sa.Column("cta_href", sa.Text(), nullable=True),
        sa.Column("display_url", sa.Text(), nullable=True),
        sa.Column("headline_text", sa.Text(), nullable=True),
        sa.Column("ad_duration_seconds", sa.Float(), nullable=True),
        sa.Column("landing_url", sa.Text(), nullable=True),
        sa.Column("landing_dir", sa.Text(), nullable=True),
        sa.Column("landing_status", sa.String(length=20), nullable=False),
        sa.Column("video_src_url", sa.Text(), nullable=True),
        sa.Column("video_file", sa.Text(), nullable=True),
        sa.Column("video_status", sa.String(length=20), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("ad_captures_pkey")),
    )
    op.create_index(
        op.f("ad_captures_session_id_idx"),
        "ad_captures",
        ["session_id"],
        unique=False,
    )

    op.create_table(
        "ad_capture_screenshots",
        sa.Column("capture_id", sa.UUID(), nullable=False),
        sa.Column("offset_ms", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("id", sa.UUID(), server_default=sa.text("uuidv7()"), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["capture_id"],
            ["ad_captures.id"],
            name=op.f("ad_capture_screenshots_capture_id_fkey"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("ad_capture_screenshots_pkey")),
    )
    op.create_index(
        op.f("ad_capture_screenshots_capture_id_idx"),
        "ad_capture_screenshots",
        ["capture_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ad_capture_screenshots_capture_id_idx"),
        table_name="ad_capture_screenshots",
    )
    op.drop_table("ad_capture_screenshots")
    op.drop_index(op.f("ad_captures_session_id_idx"), table_name="ad_captures")
    op.drop_table("ad_captures")
