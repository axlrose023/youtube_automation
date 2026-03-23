

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3b7d9f12e44"
down_revision: str | None = "1f4c8c7d9e11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ad_captures",
        sa.Column("analysis_summary", sa.Text(), nullable=True),
    )
    op.add_column(
        "ad_captures",
        sa.Column(
            "analysis_status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
    )


def downgrade() -> None:
    op.drop_column("ad_captures", "analysis_status")
    op.drop_column("ad_captures", "analysis_summary")
