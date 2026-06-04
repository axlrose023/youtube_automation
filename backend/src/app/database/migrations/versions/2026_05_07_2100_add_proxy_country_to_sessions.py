from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d9e2f1a4b3c5"
down_revision: str | None = "c8f7d3b91e42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "emulation_sessions",
        sa.Column("proxy_country_code", sa.String(length=8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("emulation_sessions", "proxy_country_code")
