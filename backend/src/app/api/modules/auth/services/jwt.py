from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from fastapi import HTTPException, status
from jwt import ExpiredSignatureError, InvalidTokenError

from app.api.modules.auth.schema import TokenPairResponse
from app.api.modules.users.models import User
from app.database.uow import UnitOfWork
from app.settings import Config


class JwtService:
    def __init__(self, config: Config):
        self._config = config
        self._access_expires_delta = timedelta(
            minutes=config.jwt.access_token_expires_in_minutes
        )
        self._refresh_expires_delta = timedelta(
            minutes=config.jwt.refresh_expires_in_minutes
        )

    def create_token_pair(self, user: User) -> TokenPairResponse:
        access_token, access_expires = self._create_access_token(user)
        refresh_token, refresh_expires = self._create_refresh_token(user)
        return TokenPairResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=access_expires,
            refresh_expires_in=refresh_expires,
        )

    def validate_refresh_token(self, refresh_token: str) -> dict:
        try:
            payload = jwt.decode(
                refresh_token,
                self._config.jwt.secret_key,
                algorithms=[self._config.jwt.algorithm],
            )
        except ExpiredSignatureError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token expired"
            ) from exc
        except InvalidTokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
            ) from exc

        if payload.get("type") != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token type",
            )

        return payload

    async def refresh(self, refresh_token: str, uow: UnitOfWork) -> TokenPairResponse:
        payload = self.validate_refresh_token(refresh_token)

        try:
            user_id = UUID(payload["sub"])
        except (KeyError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token payload",
            ) from exc

        user = await uow.users.get_by_id(user_id)
        if user is None or not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="User is not allowed"
            )

        return self.create_token_pair(user)

    def _create_access_token(self, user: User) -> tuple[str, int]:
        return self._create_token(
            {"sub": str(user.id), "type": "access"}, self._access_expires_delta
        )

    def _create_refresh_token(self, user: User) -> tuple[str, int]:
        return self._create_token(
            {"sub": str(user.id), "type": "refresh"}, self._refresh_expires_delta
        )

    def _create_token(self, payload: dict, expires_delta: timedelta) -> tuple[str, int]:
        expire_time = datetime.now(UTC) + expires_delta
        complete_payload = {
            **payload,
            "exp": int(expire_time.timestamp()),
        }
        token = jwt.encode(
            complete_payload,
            self._config.jwt.secret_key,
            algorithm=self._config.jwt.algorithm,
        )
        return token, int(expires_delta.total_seconds())
