from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e2f8a1c5b6"
down_revision: str | None = "c8f1a9e7b2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", "is_admin")
