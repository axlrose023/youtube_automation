from functools import lru_cache
from pathlib import Path
from typing import Literal, final

from pydantic import BaseModel, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from yarl import URL


BACKEND_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class PlaywrightConfig(BaseModel):
    headless: bool = False
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
    api_key: str | None = None


class AndroidAppConfig(BaseModel):
    enabled: bool = False
    appium_server_url: str = "http://127.0.0.1:4723"
    appium_host: str = "127.0.0.1"
    appium_port: int = 4723
    appium_base_path: str = "/"
    manage_appium_server: bool = True
    appium_command_timeout_seconds: int = 240
    appium_runtime_command_timeout_seconds: int = 60
    appium_new_command_timeout_seconds: int = 600
    appium_create_session_timeout_seconds: int = 90
    appium_validate_session_timeout_seconds: int = 20
    appium_skip_device_initialization: bool = True
    default_avd_name: str = "yt_android_playstore_api35_clean"
    bootstrap_avd_name: str = "yt_android_playstore_api35_clean"
    bootstrap_system_image_package: str = (
        "system-images;android-35;google_apis_playstore;arm64-v8a"
    )
    bootstrap_device_preset: str = "pixel_7"
    warm_snapshot_name: str = "youtube_warm_updated"
    runtime_snapshot_name: str | None = None
    bootstrap_emulator_gpu_mode: str = "host"
    bootstrap_emulator_accel_mode: str | None = None
    emulator_headless: bool = False
    emulator_gpu_mode: str = "host"
    emulator_accel_mode: str | None = None
    emulator_use_snapshots: bool = False
    emulator_skip_adb_auth: bool = True
    emulator_force_restart_before_run: bool = True
    emulator_stop_after_run: bool = True
    probe_watch_seconds: int = 20
    probe_watch_sample_interval_seconds: int = 1
    probe_watch_min_progress_delta_seconds: int = 3
    probe_ad_min_watch_seconds: int = 20
    probe_post_ad_watch_seconds: int = 18
    session_topic_start_buffer_seconds: int = 180
    session_engagement_enabled: bool = False
    probe_screenrecord_enabled: bool = True
    probe_screenrecord_bitrate: int = 4_000_000
    probe_screenrecord_artifacts_subdir: str = "android_probe/video"
    emulator_start_timeout_seconds: int = 240
    device_ready_timeout_seconds: int = 180
    adb_exec_timeout_seconds: int = 60
    uiautomator2_server_install_timeout_seconds: int = 120
    youtube_package: str = "com.google.android.youtube"
    youtube_activity: str = ".app.honeycomb.Shell$HomeActivity"
    artifacts_subdir: str = "android_probe"
    session_artifacts_subdir: str = "android_sessions"
    bootstrap_artifacts_subdir: str = "android_bootstrap"


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
    refresh_expires_in_minutes: int = 1440


class APIConfig(BaseModel):
    title: str = "Template API"
    version: str = "1.0.0"
    port: int = 8000
    host: str = "0.0.0.0"
    allowed_hosts: list[str] = ["*"]

    page_max_size: int = 100
    page_default_size: int = 10


class GeminiConfig(BaseModel):
    api_key: str = ""
    model: str = "gemini-2.5-flash"


class StorageConfig(BaseModel):
    base_path: Path = Path("artifacts")
    ad_captures_subdir: str = "ad_captures"

    @model_validator(mode="after")
    def _resolve_base_path(self) -> "StorageConfig":
        if not self.base_path.is_absolute():
            project_root = Path(__file__).resolve().parents[3]
            self.base_path = (project_root / self.base_path).resolve()
        return self

    @property
    def ad_captures_path(self) -> Path:
        return self.base_path / self.ad_captures_subdir


class PathsConfig:
    src_path = Path(__file__).parent.parent
    app_path = src_path / "app"
    database_path = app_path / "database"
    models_path = database_path / "models"
    modules_path = app_path / "api" / "modules"


@final
class Config(BaseSettings):
    model_config: SettingsConfigDict = SettingsConfigDict(
        env_file=str(BACKEND_ENV_FILE),
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
    android_app: AndroidAppConfig = AndroidAppConfig()
    storage: StorageConfig = StorageConfig()
    gemini: GeminiConfig = GeminiConfig()

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
