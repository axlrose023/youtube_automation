from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import BinaryExpression, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.modules.users.models import User


class UserGateway:
    def __init__(self, session: AsyncSession):
        self.session = session

    def _not_deleted_expr(self) -> BinaryExpression:
        return User.is_deleted.is_(False)

    async def get_total_count(self, filters: list[BinaryExpression]) -> int:
        stmt = select(func.count()).select_from(User).where(self._not_deleted_expr(), *filters)
        result = await self.session.execute(stmt)
        return result.scalar()

    async def get_all(
        self,
        limit: int,
        offset: int,
        filters: list[BinaryExpression],
    ) -> Sequence[User]:
        stmt = (
            select(User)
            .where(self._not_deleted_expr(), *filters)
            .offset(offset=offset)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        users = result.scalars().all()
        return users

    async def get_by_id(self, user_id: UUID) -> User | None:
        stmt = select(User).where(User.id == user_id, self._not_deleted_expr())
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, user: User) -> User:
        self.session.add(user)
        await self.session.flush()
        return user

    async def update(self, user_id: UUID, **fields) -> User | None:
        stmt = update(User).where(User.id == user_id, self._not_deleted_expr()).values(**fields)
        await self.session.execute(stmt)
        await self.session.flush()
        return await self.get_by_id(user_id)

    async def soft_delete(self, user_id: UUID) -> User | None:
        stmt = (
            update(User)
            .where(User.id == user_id, self._not_deleted_expr())
            .values(is_deleted=True, is_active=False)
        )
        await self.session.execute(stmt)
        await self.session.flush()
        return None
