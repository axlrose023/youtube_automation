from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e3f5a2b1c4d6"
down_revision: str | None = "d9e2f1a4b3c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "proxies",
        sa.Column("city", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("proxies", "city")
