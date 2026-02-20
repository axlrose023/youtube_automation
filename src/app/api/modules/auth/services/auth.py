from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from app.api.modules.users.models import User
from app.database.uow import UnitOfWork
from app.settings import Config

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


class AuthenticateUser:
    async def __call__(
        self,
        request: Request,
        token: str = Depends(oauth2_scheme),
    ) -> User:
        container = request.state.dishka_container
        uow: UnitOfWork = await container.get(UnitOfWork)
        config: Config = await container.get(Config)
        return await self.get_current_user(uow=uow, token=token, config=config)

    async def get_current_user(
        self,
        uow: UnitOfWork,
        token: str,
        config: Config,
    ) -> User:
        credential_exception = self._build_credential_exception()
        payload = self._validate_token(token, config, credential_exception)
        return await self._get_user(uow, payload["sub"], credential_exception)

    def _validate_token(
        self,
        token: str,
        config: Config,
        credential_exception: HTTPException,
    ) -> dict:
        try:
            payload = jwt.decode(
                token,
                config.jwt.secret_key,
                algorithms=[config.jwt.algorithm],
            )

            if payload.get("type") != "access":
                raise ValueError("Invalid token type")

            if not payload.get("sub"):
                raise ValueError("Missing user ID")

            return payload

        except Exception as exc:
            raise credential_exception from exc

    async def _get_user(
        self,
        uow: UnitOfWork,
        user_id: str,
        credential_exception: HTTPException,
    ) -> User:
        user = await uow.users.get_by_id(UUID(user_id))

        if user is None or not user.is_active:
            raise credential_exception

        return user

    def _build_credential_exception(self) -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
