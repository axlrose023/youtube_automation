from __future__ import annotations

import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProxyBase(BaseModel):
    label: str = Field(min_length=1, max_length=128)
    scheme: str = Field(default="socks5", pattern=r"^(socks5|socks5h|http|https)$")
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    username: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, max_length=255)
    country_code: str | None = Field(default=None, max_length=8)
    notes: str | None = None
    is_active: bool = True


class ProxyCreate(ProxyBase):
    pass


class ProxyUpdate(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=128)
    scheme: str | None = Field(default=None, pattern=r"^(socks5|socks5h|http|https)$")
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = None
    password: str | None = None
    country_code: str | None = Field(default=None, max_length=8)
    notes: str | None = None
    is_active: bool | None = None


class ProxyRead(ProxyBase):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    url: str
    created_at: datetime.datetime
    updated_at: datetime.datetime


class ProxyListResponse(BaseModel):
    items: list[ProxyRead]
    total: int
