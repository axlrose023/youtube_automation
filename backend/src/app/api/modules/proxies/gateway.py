from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.modules.proxies.models import Proxy


class ProxyGateway:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_all(self, active_only: bool = False) -> Sequence[Proxy]:
        stmt = select(Proxy).order_by(Proxy.created_at.desc())
        if active_only:
            stmt = stmt.where(Proxy.is_active.is_(True))
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def count(self) -> int:
        result = await self.session.execute(select(func.count()).select_from(Proxy))
        return int(result.scalar() or 0)

    async def get_by_id(self, proxy_id: UUID) -> Proxy | None:
        result = await self.session.execute(select(Proxy).where(Proxy.id == proxy_id))
        return result.scalar_one_or_none()

    async def create(self, proxy: Proxy) -> Proxy:
        self.session.add(proxy)
        await self.session.flush()
        await self.session.refresh(proxy)
        return proxy

    async def update(self, proxy_id: UUID, **fields) -> Proxy | None:
        proxy = await self.get_by_id(proxy_id)
        if proxy is None:
            return None
        for key, value in fields.items():
            if value is not None:
                setattr(proxy, key, value)
        await self.session.flush()
        await self.session.refresh(proxy)
        return proxy

    async def delete(self, proxy_id: UUID) -> bool:
        proxy = await self.get_by_id(proxy_id)
        if proxy is None:
            return False
        await self.session.delete(proxy)
        await self.session.flush()
        return True
