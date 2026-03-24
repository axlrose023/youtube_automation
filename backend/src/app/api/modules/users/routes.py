from uuid import UUID

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.params import Query

from app.api.modules.auth.services.auth import AuthenticateUser
from app.api.modules.users.models import User
from app.api.modules.users.schema import (
    CreateUserRequest,
    UpdateUserRequest,
    UserResponse,
    UsersPaginationParams,
    UsersPaginationResponse,
)
from app.api.modules.users.service import UserService

router = APIRouter(route_class=DishkaRoute)


@router.get("/me", response_model=UserResponse)
async def get_current_user(
    current_user: User = Depends(AuthenticateUser()),
) -> UserResponse:
    return UserResponse.model_validate(current_user)


@router.post("", response_model=UserResponse, status_code=201)
async def create_user(
    request: CreateUserRequest,
    service: FromDishka[UserService],
    current_user: User = Depends(AuthenticateUser()),
) -> UserResponse:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Only admins can create users")
    return await service.create_user(request)


@router.get("", response_model=UsersPaginationResponse)
async def get_users(
    service: FromDishka[UserService],
    current_user: User = Depends(AuthenticateUser()),
    params: UsersPaginationParams = Query(),
) -> UsersPaginationResponse:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return await service.get_users(params=params)


@router.get("/{user_id}", response_model=UserResponse)
async def get_user_by_id(
    service: FromDishka[UserService],
    current_user: User = Depends(AuthenticateUser()),
    user_id: UUID = Path(...),
) -> UserResponse:
    if not current_user.is_admin and current_user.id != user_id:
        raise HTTPException(status_code=403, detail="Admin access required")
    return await service.get_user_by_id(user_id)


@router.patch("/{user_id}", response_model=UserResponse)
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
