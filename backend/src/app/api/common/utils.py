import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import BinaryExpression

from app.database.base import Base

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def build_filters(
    model: type[Base],
    filters: dict[str, Any],
    delimiter: str = "__",
) -> list[BinaryExpression]:

    expressions: list[BinaryExpression] = []
    for field_name, value in filters.items():
        logger.debug(f"Building filter for {field_name} with value {value}")
        op = field_name.split(delimiter)
        if len(op) == 2:
            field_name, operation = op
            column = getattr(model, field_name, None)
            if column is not None:
                if operation == "is":
                    expressions.append(column.is_(value))
                elif operation == "is_not":
                    expressions.append(column.is_not(value))
                elif operation == "in":
                    expressions.append(column.in_(value))
                elif operation == "not_in":
                    expressions.append(column.not_in(value))
                elif operation == "eq":
                    expressions.append(column == value)
                elif operation == "ne":
                    expressions.append(column != value)
                elif operation == "lt":
                    expressions.append(column < value)
                elif operation == "lte":
                    expressions.append(column <= value)
                elif operation == "gt":
                    expressions.append(column > value)
                elif operation == "gte":
                    expressions.append(column >= value)
                elif operation == "search":
                    expressions.append(column.ilike(f"%{value}%"))
        else:
            column = getattr(model, field_name, None)
            if column is not None:
                expressions.append(column == value)
    return expressions
