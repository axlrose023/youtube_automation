from uuid import UUID

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Depends, Query

from app.api.common.auth import AuthenticateMainRoles

from .schema import ProxyCreate, ProxyListResponse, ProxyRead, ProxyUpdate
from .service import ProxyService

router = APIRouter(
    route_class=DishkaRoute,
    dependencies=[Depends(AuthenticateMainRoles())],
)


@router.get("")
async def list_proxies(
    service: FromDishka[ProxyService],
    active_only: bool = Query(False),
) -> ProxyListResponse:
    return await service.list_proxies(active_only=active_only)


@router.post("", status_code=201)
async def create_proxy(
    payload: ProxyCreate,
    service: FromDishka[ProxyService],
) -> ProxyRead:
    return await service.create_proxy(payload)


@router.patch("/{proxy_id}")
async def update_proxy(
    proxy_id: UUID,
    payload: ProxyUpdate,
    service: FromDishka[ProxyService],
) -> ProxyRead:
    return await service.update_proxy(proxy_id, payload)


@router.delete("/{proxy_id}", status_code=204)
async def delete_proxy(
    proxy_id: UUID,
    service: FromDishka[ProxyService],
) -> None:
    await service.delete_proxy(proxy_id)
