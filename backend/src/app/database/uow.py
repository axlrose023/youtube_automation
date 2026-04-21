from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.modules.emulation.gateway import AdCaptureGateway, EmulationHistoryGateway
from app.api.modules.proxies.gateway import ProxyGateway
from app.api.modules.users.gateway import UserGateway


class UnitOfWork:
    users: UserGateway
    ad_captures: AdCaptureGateway
    emulation_history: EmulationHistoryGateway
    proxies: ProxyGateway

    def __init__(self, session: AsyncSession):
        self.session = session
        self.users = UserGateway(session)
        self.ad_captures = AdCaptureGateway(session)
        self.emulation_history = EmulationHistoryGateway(session)
        self.proxies = ProxyGateway(session)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            await self.rollback()
        await self.session.close()

    async def commit(self: Self):
        await self.session.commit()

    async def flush(self: Self):
        await self.session.flush()

    async def refresh(self: Self, instance: object):
        await self.session.refresh(instance)

    async def rollback(self: Self):
        await self.session.rollback()

    async def close(self: Self):
        await self.session.close()
