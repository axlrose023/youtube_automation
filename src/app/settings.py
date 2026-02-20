from functools import lru_cache
from pathlib import Path
from typing import Literal, final

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict
from yarl import URL


class PlaywrightConfig(BaseModel):
    headless: bool = True
    max_browsers: int = 2
    contexts_per_browser: int = 5
    browser_args: list[str] = [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--no-sandbox",
    ]


class ViewportConfig(BaseModel):
    width_min: int = 1280
    width_max: int = 1920
    height_min: int = 800
    height_max: int = 1080


class UserAgentConfig(BaseModel):
    browsers: list[str] = ["Chrome", "Edge"]
    fallback: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )


class AdsPowerConfig(BaseModel):
    base_url: str = "http://local.adspower.net:50325"
    user_id: str = "k19s5uo7"


class PostgresConfig(BaseModel):
    user: str = "postgres"
    password: str = "postgres"
    host: str = "localhost"
    port: int = 5432
    db: str = "app"


class RedisConfig(BaseModel):
    host: str = "localhost"
    port: int = 6379
    db: int = 0


class JwtConfig(BaseModel):
    secret_key: str = "change-me-in-production"
    algorithm: str = "HS256"
    access_token_expires_in_minutes: int = 30
    refresh_expires_in_minutes: int = 1440  # 1 day


class APIConfig(BaseModel):
    title: str = "Template API"
    version: str = "1.0.0"
    port: int = 8000
    host: str = "0.0.0.0"
    allowed_hosts: list[str] = ["*"]

    page_max_size: int = 100
    page_default_size: int = 10


class PathsConfig:
    src_path = Path(__file__).parent.parent
    app_path = src_path / "app"
    database_path = app_path / "database"
    models_path = database_path / "models"
    modules_path = app_path / "api" / "modules"


@final
class Config(BaseSettings):
    model_config: SettingsConfigDict = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="APP__",
        env_nested_delimiter="__",
        extra="ignore",
    )

    env: Literal["local", "dev", "prod"] = "local"
    browser_backend: Literal["chromium", "adspower"] = "adspower"

    api: APIConfig = APIConfig()
    jwt: JwtConfig = JwtConfig()

    postgres: PostgresConfig = PostgresConfig()
    redis: RedisConfig = RedisConfig()

    playwright: PlaywrightConfig = PlaywrightConfig()
    viewport: ViewportConfig = ViewportConfig()
    useragent: UserAgentConfig = UserAgentConfig()
    adspower: AdsPowerConfig = AdsPowerConfig()

    paths: PathsConfig = PathsConfig()

    @property
    def database_url(self) -> str:
        host = "localhost" if self.env == "local" else self.postgres.host
        return URL.build(
            scheme="postgresql+asyncpg",
            user=self.postgres.user,
            password=self.postgres.password,
            host=host,
            port=self.postgres.port,
            path=f"/{self.postgres.db}",
        ).human_repr()

    @property
    def redis_url(self) -> str:
        host = "localhost" if self.env == "local" else self.redis.host
        return URL.build(
            scheme="redis",
            host=host,
            port=self.redis.port,
            path=f"/{self.redis.db}",
        ).human_repr()


@lru_cache
def get_config() -> Config:
    return Config()
