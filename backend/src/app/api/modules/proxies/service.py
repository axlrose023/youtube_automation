from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException

from app.api.modules.proxies.models import Proxy
from app.api.modules.proxies.schema import (
    ProxyCreate,
    ProxyListResponse,
    ProxyRead,
    ProxyUpdate,
)
from app.database.uow import UnitOfWork


class ProxyService:
    def __init__(self, uow: UnitOfWork) -> None:
        self.uow = uow

    @staticmethod
    def _to_read(proxy: Proxy) -> ProxyRead:
        return ProxyRead(
            id=proxy.id,
            label=proxy.label,
            scheme=proxy.scheme,
            host=proxy.host,
            port=proxy.port,
            username=proxy.username,
            password=proxy.password,
            country_code=proxy.country_code,
            notes=proxy.notes,
            is_active=proxy.is_active,
            url=proxy.to_url(),
            created_at=proxy.created_at,
            updated_at=proxy.updated_at,
        )

    async def list_proxies(self, active_only: bool = False) -> ProxyListResponse:
        rows = await self.uow.proxies.list_all(active_only=active_only)
        items = [self._to_read(p) for p in rows]
        return ProxyListResponse(items=items, total=len(items))

    async def create_proxy(self, payload: ProxyCreate) -> ProxyRead:
        proxy = Proxy(**payload.model_dump())
        created = await self.uow.proxies.create(proxy)
        await self.uow.commit()
        return self._to_read(created)

    async def update_proxy(self, proxy_id: UUID, payload: ProxyUpdate) -> ProxyRead:
        updated = await self.uow.proxies.update(
            proxy_id,
            **payload.model_dump(exclude_unset=True),
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="Proxy not found")
        await self.uow.commit()
        return self._to_read(updated)

    async def delete_proxy(self, proxy_id: UUID) -> None:
        ok = await self.uow.proxies.delete(proxy_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Proxy not found")
        await self.uow.commit()

    async def resolve_url(self, proxy_id: UUID) -> str | None:
        proxy = await self.uow.proxies.get_by_id(proxy_id)
        if proxy is None:
            return None
        return proxy.to_url()
