from uuid import UUID

from fastapi import HTTPException

from app.api.common.utils import build_filters
from app.api.modules.auth.service import AuthService
from app.api.modules.users.models import User
from app.api.modules.users.schema import (
    CreateUserRequest,
    UsersPaginationParams,
    UsersPaginationResponse,
)
from app.database.uow import UnitOfWork


class UserService:
    def __init__(self, uow: UnitOfWork, auth_service: AuthService):
        self.uow = uow
        self.auth_service = auth_service

    async def get_users(
        self,
        params: UsersPaginationParams,
    ) -> UsersPaginationResponse:
        pagination_data = params.model_dump(exclude_unset=True)
        pagination_data.pop("page_size", None)
        pagination_data.pop("page", None)

        filters = build_filters(User, pagination_data)

        users = await self.uow.users.get_all(
            limit=params.page_size,
            offset=params.offset,
            filters=filters,
        )
        total = await self.uow.users.get_total_count(filters)
        return UsersPaginationResponse(
            total=total,
            items=users,
            page=params.page,
            page_size=params.page_size,
        )

    async def get_user_by_id(
        self,
        user_id: UUID,
    ) -> User:
        user = await self.uow.users.get_by_id(user_id)
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return user

    async def create_user(self, request: CreateUserRequest) -> User:
        hashed_password = self.auth_service.hash_password(request.password)
        user = User(
            username=request.username,
            password=hashed_password,
            is_active=True,
        )
        await self.uow.users.create(user)
        await self.uow.commit()
        return user
