from __future__ import annotations

import datetime
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c8f7d3b91e42"
down_revision: str | None = "a5c91d7b2f30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ANALYSIS_TERMINAL_STATUSES = {
    "completed",
    "not_relevant",
    "skipped",
    "failed",
}


def _timestamp(value: object) -> datetime.datetime:
    if isinstance(value, datetime.datetime):
        return value
    return datetime.datetime.min.replace(tzinfo=datetime.UTC)


def _capture_priority(row: dict[str, object]) -> tuple[int, datetime.datetime, datetime.datetime, str]:
    analysis_status = str(row["analysis_status"] or "").lower()
    analysis_rank = 1 if analysis_status in _ANALYSIS_TERMINAL_STATUSES else 0
    return (
        analysis_rank,
        _timestamp(row["updated_at"]),
        _timestamp(row["created_at"]),
        str(row["id"]),
    )


def upgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    ad_captures = sa.Table("ad_captures", metadata, autoload_with=bind)

    duplicate_keys = bind.execute(
        sa.select(
            ad_captures.c.session_id,
            ad_captures.c.ad_position,
        )
        .group_by(
            ad_captures.c.session_id,
            ad_captures.c.ad_position,
        )
        .having(sa.func.count(ad_captures.c.id) > 1)
    ).all()

    for session_id, ad_position in duplicate_keys:
        rows = list(
            bind.execute(
                sa.select(ad_captures).where(
                    ad_captures.c.session_id == session_id,
                    ad_captures.c.ad_position == ad_position,
                )
            ).mappings()
        )
        if len(rows) <= 1:
            continue

        keep_row = max(rows, key=_capture_priority)
        delete_ids = [row["id"] for row in rows if row["id"] != keep_row["id"]]
        if delete_ids:
            bind.execute(
                sa.delete(ad_captures).where(ad_captures.c.id.in_(delete_ids))
            )

    op.create_unique_constraint(
        "uq_ad_captures_session_id_ad_position",
        "ad_captures",
        ["session_id", "ad_position"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_ad_captures_session_id_ad_position",
        "ad_captures",
        type_="unique",
    )
