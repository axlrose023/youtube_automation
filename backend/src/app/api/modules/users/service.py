from uuid import UUID

from fastapi import HTTPException

from app.api.common.utils import build_filters
from app.api.modules.auth.service import AuthService
from app.api.modules.users.models import User
from app.api.modules.users.schema import (
    CreateUserRequest,
    UpdateUserRequest,
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
            is_admin=request.is_admin,
            is_deleted=False,
        )
        await self.uow.users.create(user)
        await self.uow.commit()
        return user

    async def update_user(
        self,
        user_id: UUID,
        request: UpdateUserRequest,
        current_user: User,
    ) -> User:
        target = await self.uow.users.get_by_id(user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found")

        is_self = current_user.id == user_id
        if not is_self and not current_user.is_admin:
            raise HTTPException(status_code=403, detail="Not enough permissions")

        fields = request.model_dump(exclude_unset=True)

        # Only admins can change is_admin and is_active
        if not current_user.is_admin:
            fields.pop("is_admin", None)
            fields.pop("is_active", None)

        if fields.get("is_admin") is False:
            raise HTTPException(status_code=403, detail="Admin demotion is disabled")

        if is_self and current_user.is_admin and fields.get("is_active") is False:
            raise HTTPException(status_code=403, detail="Admins cannot disable themselves")

        if "password" in fields:
            fields["password"] = self.auth_service.hash_password(fields["password"])

        if not fields:
            return target

        user = await self.uow.users.update(user_id, **fields)
        await self.uow.commit()
        return user

    async def delete_user(
        self,
        user_id: UUID,
        current_user: User,
    ) -> None:
        if not current_user.is_admin:
            raise HTTPException(status_code=403, detail="Only admins can delete users")

        target = await self.uow.users.get_by_id(user_id)
        if not target:
            raise HTTPException(status_code=404, detail="User not found")

        is_self = current_user.id == user_id
        if target.is_admin and not is_self:
            raise HTTPException(
                status_code=403,
                detail="Admins cannot delete other admins",
            )

        await self.uow.users.soft_delete(user_id)
        await self.uow.commit()
