from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a5c91d7b2f30"
down_revision: str | None = "f1a4b6c7d8e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "proxies",
        sa.Column(
            "id",
            sa.UUID(),
            nullable=False,
            server_default=sa.text("uuidv7()"),
        ),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column(
            "scheme",
            sa.String(length=16),
            nullable=False,
            server_default="socks5",
        ),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("password", sa.String(length=255), nullable=True),
        sa.Column("country_code", sa.String(length=8), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
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
        sa.PrimaryKeyConstraint("id", name=op.f("proxies_pkey")),
    )


def downgrade() -> None:
    op.drop_table("proxies")
