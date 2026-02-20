from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import BinaryExpression, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.modules.users.models import User


class UserGateway:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_total_count(self, filters: list[BinaryExpression]) -> int:
        stmt = select(func.count()).select_from(User).where(*filters)
        result = await self.session.execute(stmt)
        return result.scalar()

    async def get_all(
        self,
        limit: int,
        offset: int,
        filters: list[BinaryExpression],
    ) -> Sequence[User]:
        stmt = select(User).filter(*filters).offset(offset=offset).limit(limit)
        result = await self.session.execute(stmt)
        users = result.scalars().all()
        return users

    async def get_by_id(self, user_id: UUID) -> User | None:
        stmt = select(User).where(User.id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, user: User) -> User:
        self.session.add(user)
        await self.session.flush()
        return user
