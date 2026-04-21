from __future__ import annotations

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, DateTimeMixin, UUID7IDMixin


class Proxy(Base, UUID7IDMixin, DateTimeMixin):
    __tablename__ = "proxies"

    label: Mapped[str] = mapped_column(String(128))
    scheme: Mapped[str] = mapped_column(String(16), default="socks5")
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    country_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    def to_url(self) -> str:
        auth = ""
        if self.username:
            auth = self.username
            if self.password:
                auth = f"{self.username}:{self.password}"
            auth = f"{auth}@"
        return f"{self.scheme}://{auth}{self.host}:{self.port}"
