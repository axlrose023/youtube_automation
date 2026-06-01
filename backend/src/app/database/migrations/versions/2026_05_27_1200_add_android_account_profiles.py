from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b6a8d2f4c901"
down_revision: str | None = "e3f5a2b1c4d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "android_account_profiles",
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("google_email", sa.String(length=255), nullable=False),
        sa.Column("avd_name", sa.String(length=128), nullable=False),
        sa.Column("snapshot_name", sa.String(length=128), nullable=True),
        sa.Column("appium_port", sa.Integer(), nullable=True),
        sa.Column("uiautomator2_system_port", sa.Integer(), nullable=True),
        sa.Column("mjpeg_server_port", sa.Integer(), nullable=True),
        sa.Column("emulator_port", sa.Integer(), nullable=True),
        sa.Column("emulator_memory_mb", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="ready", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_android_account_profiles_avd_name"),
        "android_account_profiles",
        ["avd_name"],
        unique=True,
    )
    op.create_index(
        op.f("ix_android_account_profiles_google_email"),
        "android_account_profiles",
        ["google_email"],
        unique=True,
    )
    op.create_index(
        op.f("ix_android_account_profiles_is_active"),
        "android_account_profiles",
        ["is_active"],
        unique=False,
    )
    op.create_index(
        op.f("ix_android_account_profiles_status"),
        "android_account_profiles",
        ["status"],
        unique=False,
    )

    op.add_column(
        "emulation_sessions",
        sa.Column("android_account_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "emulation_sessions",
        sa.Column("android_google_email", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "emulation_sessions",
        sa.Column("android_avd_name", sa.String(length=128), nullable=True),
    )
    op.create_index(
        op.f("ix_emulation_sessions_android_account_id"),
        "emulation_sessions",
        ["android_account_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_emulation_sessions_android_account_id",
        "emulation_sessions",
        "android_account_profiles",
        ["android_account_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_emulation_sessions_android_account_id",
        "emulation_sessions",
        type_="foreignkey",
    )
    op.drop_index(
        op.f("ix_emulation_sessions_android_account_id"),
        table_name="emulation_sessions",
    )
    op.drop_column("emulation_sessions", "android_avd_name")
    op.drop_column("emulation_sessions", "android_google_email")
    op.drop_column("emulation_sessions", "android_account_id")

    op.drop_index(
        op.f("ix_android_account_profiles_status"),
        table_name="android_account_profiles",
    )
    op.drop_index(
        op.f("ix_android_account_profiles_is_active"),
        table_name="android_account_profiles",
    )
    op.drop_index(
        op.f("ix_android_account_profiles_google_email"),
        table_name="android_account_profiles",
    )
    op.drop_index(
        op.f("ix_android_account_profiles_avd_name"),
        table_name="android_account_profiles",
    )
    op.drop_table("android_account_profiles")
