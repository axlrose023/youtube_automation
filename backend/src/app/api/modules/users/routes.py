from uuid import UUID

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Depends
from fastapi.params import Path, Query

from app.api.modules.users.models import User
from app.api.modules.users.schema import (
    CreateUserRequest,
    LoginRequest,
    RefreshRequest,
    TokenPairResponse,
    UpdateUserRequest,
    UserResponse,
    UsersPaginationParams,
    UsersPaginationResponse,
)
from app.api.modules.users.service import AuthService, UserService
from app.api.common.auth import AuthenticateAdmin, AuthenticateUser
from app.api.modules.users.services.jwt import JwtService
from app.database.uow import UnitOfWork

router = APIRouter(route_class=DishkaRoute)
auth_router = APIRouter(route_class=DishkaRoute)


# ── Auth ──


@auth_router.post("/login")
async def login(
    request: LoginRequest,
    service: FromDishka[AuthService],
) -> TokenPairResponse:
    return await service.login(request)


@auth_router.post("/refresh")
async def refresh_token(
    request: RefreshRequest,
    jwt_service: FromDishka[JwtService],
    uow: FromDishka[UnitOfWork],
) -> TokenPairResponse:
    return await jwt_service.refresh(request.refresh_token, uow)


# ── Users ──


@router.get("/me")
async def get_current_user(
    current_user: User = Depends(AuthenticateUser()),
) -> UserResponse:
    return UserResponse.model_validate(current_user)


@router.post("", status_code=201)
async def create_user(
    request: CreateUserRequest,
    service: FromDishka[UserService],
    _: User = Depends(AuthenticateAdmin()),
) -> UserResponse:
    return await service.create_user(request)


@router.get("")
async def get_users(
    service: FromDishka[UserService],
    _: User = Depends(AuthenticateAdmin()),
    params: UsersPaginationParams = Query(),
) -> UsersPaginationResponse:
    return await service.get_users(params=params)


@router.get("/{user_id}")
async def get_user_by_id(
    service: FromDishka[UserService],
    _: User = Depends(AuthenticateAdmin()),
    user_id: UUID = Path(...),
) -> UserResponse:
    return await service.get_user_by_id(user_id)


@router.patch("/{user_id}")
async def update_user(
    request: UpdateUserRequest,
    service: FromDishka[UserService],
    current_user: User = Depends(AuthenticateUser()),
    user_id: UUID = Path(...),
) -> UserResponse:
    return await service.update_user(user_id, request, current_user)


@router.delete("/{user_id}", status_code=204)
async def delete_user(
    service: FromDishka[UserService],
    current_user: User = Depends(AuthenticateUser()),
    user_id: UUID = Path(...),
) -> None:
    await service.delete_user(user_id, current_user)
