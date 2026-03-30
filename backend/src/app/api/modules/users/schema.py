from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.api.common.schema import Pagination, PaginationParams


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    username: str
    is_admin: bool
    is_active: bool


class UsersPaginationResponse(Pagination[UserResponse]):
    model_config = ConfigDict(from_attributes=True)
    pass


class UsersPaginationParams(PaginationParams):
    id: UUID | None = None
    username: str | None = None
    username__search: str | None = None


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)
    is_admin: bool = False

    model_config = ConfigDict(extra="forbid")


class UpdateUserRequest(BaseModel):
    username: str | None = Field(None, min_length=1)
    password: str | None = Field(None, min_length=1)
    is_active: bool | None = None
    is_admin: bool | None = None

    model_config = ConfigDict(extra="forbid")


class LoginRequest(BaseModel):
    username: str
    password: str

    model_config = ConfigDict(extra="forbid")


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str
