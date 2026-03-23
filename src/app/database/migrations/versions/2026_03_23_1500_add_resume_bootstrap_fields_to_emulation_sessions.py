
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8f1a9e7b2d3"
down_revision: str | None = "a3b7d9f12e44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "emulation_sessions",
        sa.Column("current_topic", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "emulation_sessions",
        sa.Column(
            "personality",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("emulation_sessions", "personality")
    op.drop_column("emulation_sessions", "current_topic")
