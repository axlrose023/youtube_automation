from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter

from app.api.modules.auth.schema import (
    LoginRequest,
    RefreshRequest,
    TokenPairResponse,
)
from app.api.modules.auth.service import AuthService
from app.api.modules.auth.services.jwt import JwtService
from app.database.uow import UnitOfWork

router = APIRouter(route_class=DishkaRoute)


@router.post("/login", response_model=TokenPairResponse, status_code=200)
async def login(
    request: LoginRequest,
    service: FromDishka[AuthService],
) -> TokenPairResponse:
    return await service.login(request)


@router.post("/refresh", response_model=TokenPairResponse, status_code=200)
async def refresh_token(
    request: RefreshRequest,
    jwt_service: FromDishka[JwtService],
    uow: FromDishka[UnitOfWork],
) -> TokenPairResponse:
    return await jwt_service.refresh(request.refresh_token, uow)
