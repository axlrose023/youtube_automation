import bcrypt
from fastapi import HTTPException, status

from app.api.common.utils import build_filters
from app.api.modules.auth.schema import LoginRequest, TokenPairResponse
from app.api.modules.auth.services.jwt import JwtService
from app.api.modules.users.models import User
from app.database.uow import UnitOfWork


class AuthService:
    def __init__(self, uow: UnitOfWork, jwt_service: JwtService):
        self.uow = uow
        self.jwt_service = jwt_service

    async def login(self, request: LoginRequest) -> TokenPairResponse:
        filters = build_filters(User, {"username": request.username})
        users = await self.uow.users.get_all(limit=1, offset=0, filters=filters)
        user = users[0] if users else None
        if user is None or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
            )

        if not bcrypt.checkpw(
            request.password.encode("utf-8"), user.password.encode("utf-8")
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
            )

        return self.jwt_service.create_token_pair(user)

    def hash_password(self, password: str) -> str:
        """Hash a password."""
        return bcrypt.hashpw(
            password.encode("utf-8"), bcrypt.gensalt(rounds=12)
        ).decode("utf-8")
