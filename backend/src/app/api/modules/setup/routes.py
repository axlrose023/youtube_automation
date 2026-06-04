import asyncio
import os
import time
import uuid
from uuid import UUID

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from taskiq.kicker import AsyncKicker

from app.api.common.auth import AuthenticateMainRoles
from app.api.modules.emulation.models import AndroidAccountProfile
from app.api.modules.proxies.models import Proxy
from app.database.uow import UnitOfWork
from app.services.emulation.session.store import EmulationSessionStore
from app.services.mobile_app.android.config_ui import (
    ANDROID_CONFIG_SESSION_ID,
    ANDROID_CONFIG_TERMINAL_STATUSES,
    android_config_state_is_active,
    android_config_state_is_stale,
    build_novnc_url,
    load_android_config_state,
    patch_android_config_state,
    save_android_config_state,
)
from app.settings import Config

router = APIRouter(route_class=DishkaRoute, dependencies=[Depends(AuthenticateMainRoles())])


class AndroidUiStartRequest(BaseModel):
    android_account_id: UUID | None = None
    proxy_id: UUID | None = None
    proxy_url: str | None = Field(default=None, max_length=2048)


class AndroidUiStartResponse(BaseModel):
    novnc_url: str
    status: str
    message: str | None = None
    android_account_id: str | None = None
    android_google_email: str | None = None
    android_avd_name: str | None = None
    proxy_id: str | None = None
    proxy_label: str | None = None
    proxy_country_code: str | None = None
    proxy_enabled: bool = False


class AndroidUiStatusResponse(BaseModel):
    status: str
    novnc_url: str | None = None
    message: str | None = None
    serial: str | None = None
    snapshot_name: str | None = None
    snapshot_saved: bool | None = None
    error: str | None = None
    queue_reason: str | None = None
    android_account_id: str | None = None
    android_google_email: str | None = None
    android_avd_name: str | None = None
    proxy_id: str | None = None
    proxy_label: str | None = None
    proxy_country_code: str | None = None
    proxy_enabled: bool = False


def _profile_to_runtime(profile: AndroidAccountProfile) -> dict:
    return {
        "id": str(profile.id),
        "label": profile.label,
        "google_email": profile.google_email,
        "avd_name": profile.avd_name,
        "snapshot_name": profile.snapshot_name,
        "appium_port": profile.appium_port,
        "uiautomator2_system_port": profile.uiautomator2_system_port,
        "mjpeg_server_port": profile.mjpeg_server_port,
        "emulator_port": profile.emulator_port,
        "emulator_memory_mb": profile.emulator_memory_mb,
        "status": profile.status,
        "is_active": profile.is_active,
    }


def _validate_proxy_url(raw: str | None) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None
    scheme, separator, rest = value.partition("://")
    if not separator or scheme.lower() not in {"http", "https", "socks5", "socks5h"} or not rest:
        raise HTTPException(
            status_code=400,
            detail="proxy_url must use http, https, socks5, or socks5h scheme",
        )
    return value


def _proxy_public_fields(proxy: Proxy | None) -> dict:
    if proxy is None:
        return {
            "proxy_id": None,
            "proxy_label": None,
            "proxy_country_code": None,
            "proxy_enabled": False,
        }
    return {
        "proxy_id": str(proxy.id),
        "proxy_label": proxy.label,
        "proxy_country_code": proxy.country_code,
        "proxy_enabled": True,
    }


def _android_ui_status_response(
    *,
    request: Request,
    state: dict,
    message: str | None = None,
) -> AndroidUiStatusResponse:
    return AndroidUiStatusResponse(
        status=str(state.get("status") or "idle"),
        novnc_url=state.get("novnc_url") or _config_novnc_url(request),
        message=message or state.get("message"),
        serial=state.get("serial"),
        snapshot_name=state.get("snapshot_name"),
        snapshot_saved=state.get("snapshot_saved"),
        error=state.get("error"),
        queue_reason=state.get("queue_reason"),
        android_account_id=state.get("android_account_id"),
        android_google_email=state.get("android_google_email"),
        android_avd_name=state.get("android_avd_name"),
        proxy_id=state.get("proxy_id"),
        proxy_label=state.get("proxy_label"),
        proxy_country_code=state.get("proxy_country_code"),
        proxy_enabled=bool(state.get("proxy_enabled")),
    )


def _config_novnc_url(request: Request | None = None) -> str:
    public_url = (
        os.environ.get("YTA_ANDROID_UI_PUBLIC_URL", "").strip()
        or os.environ.get("YTA_PUBLIC_URL", "").strip()
        or None
    )
    if public_url is None and request is not None:
        forwarded_proto = request.headers.get("x-forwarded-proto")
        forwarded_host = request.headers.get("x-forwarded-host") or request.headers.get("host")
        if forwarded_host:
            public_url = f"{forwarded_proto or request.url.scheme}://{forwarded_host}"
    return build_novnc_url(
        public_url=public_url,
        novnc_port=os.environ.get("YTA_ANDROID_BOOTSTRAP_NOVNC_PORT", "6080"),
        direct_url=os.environ.get("YTA_ANDROID_UI_NOVNC_URL", "").strip() or None,
    )


async def _enqueue_android_config_task(
    holder: str,
    android_account_profile: dict | None,
    proxy_url: str | None,
) -> None:
    from app.tiq import ANDROID_EMULATION_QUEUE_NAME, android_emulation_dispatch_broker

    await AsyncKicker(
        broker=android_emulation_dispatch_broker,
        task_name="android_config_ui_task",
        labels={"queue_name": ANDROID_EMULATION_QUEUE_NAME},
    ).kiq(holder, android_account_profile, proxy_url)


async def _wait_for_config_terminal(
    redis: Redis,
    *,
    timeout_seconds: float,
) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = await load_android_config_state(redis) or {}
        status = str(state.get("status") or "")
        if status in ANDROID_CONFIG_TERMINAL_STATUSES:
            return state
        await asyncio.sleep(1.0)
    raise HTTPException(status_code=504, detail="Timed out waiting for Android UI task")


@router.post("/android-ui/start", response_model=AndroidUiStartResponse)
async def start_android_ui(
    request: Request,
    redis: FromDishka[Redis],
    session_store: FromDishka[EmulationSessionStore],
    uow: FromDishka[UnitOfWork],
    config: FromDishka[Config],
    payload: AndroidUiStartRequest = Body(default_factory=AndroidUiStartRequest),
) -> AndroidUiStartResponse:
    novnc_url = _config_novnc_url(request)
    state = await load_android_config_state(redis)
    if android_config_state_is_active(state) and not android_config_state_is_stale(state):
        return AndroidUiStartResponse(
            novnc_url=novnc_url,
            status=str(state.get("status") or "running"),
            message="Android UI is already active",
            android_account_id=state.get("android_account_id"),
            android_google_email=state.get("android_google_email"),
            android_avd_name=state.get("android_avd_name"),
            proxy_id=state.get("proxy_id"),
            proxy_label=state.get("proxy_label"),
            proxy_country_code=state.get("proxy_country_code"),
            proxy_enabled=bool(state.get("proxy_enabled")),
        )

    if android_config_state_is_stale(state):
        from app.tasks.android_emulation import _android_device_lock_id

        await session_store.clear_session_locks(
            ANDROID_CONFIG_SESSION_ID,
            profile_id=str((state or {}).get("device_lock_id") or _android_device_lock_id(config)),
        )

    android_account_profile: dict | None = None
    if payload.android_account_id is not None:
        profile = await uow.android_account_profiles.get_by_id(payload.android_account_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Android account profile not found")
        if not profile.is_active or profile.status == "disabled":
            raise HTTPException(status_code=400, detail="Android account profile is disabled")
        android_account_profile = _profile_to_runtime(profile)

    if payload.proxy_id is not None and payload.proxy_url:
        raise HTTPException(status_code=400, detail="Use either proxy_id or proxy_url, not both")

    proxy_url = _validate_proxy_url(payload.proxy_url)
    proxy_fields = _proxy_public_fields(None)
    if payload.proxy_id is not None:
        proxy = await uow.proxies.get_by_id(payload.proxy_id)
        if proxy is None:
            raise HTTPException(status_code=404, detail="Proxy not found")
        if not proxy.is_active:
            raise HTTPException(status_code=400, detail="Proxy is disabled")
        proxy_url = proxy.to_url()
        proxy_fields = _proxy_public_fields(proxy)
    elif proxy_url:
        proxy_fields = {
            "proxy_id": None,
            "proxy_label": "Custom proxy",
            "proxy_country_code": None,
            "proxy_enabled": True,
        }

    from app.tasks.android_emulation import _android_config_avd_name, _android_config_lock_id

    android_account_id = (
        str(android_account_profile.get("id"))
        if android_account_profile and android_account_profile.get("id")
        else None
    )
    android_google_email = (
        str(android_account_profile.get("google_email"))
        if android_account_profile and android_account_profile.get("google_email")
        else None
    )
    android_avd_name = _android_config_avd_name(config, android_account_profile)
    device_lock_id = _android_config_lock_id(config, android_account_profile)

    holder = uuid.uuid4().hex
    await save_android_config_state(
        redis,
        {
            "status": "queued",
            "holder": holder,
            "novnc_url": novnc_url,
            "android_account_profile": android_account_profile,
            "android_account_id": android_account_id,
            "android_google_email": android_google_email,
            "android_avd_name": android_avd_name,
            "device_lock_id": device_lock_id,
            "proxy_url": proxy_url,
            **proxy_fields,
            "finish_requested": False,
            "stop_requested": False,
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "serial": None,
            "snapshot_name": None,
            "error": None,
        },
    )
    await _enqueue_android_config_task(holder, android_account_profile, proxy_url)
    return AndroidUiStartResponse(
        novnc_url=novnc_url,
        status="queued",
        android_account_id=android_account_id,
        android_google_email=android_google_email,
        android_avd_name=android_avd_name,
        **proxy_fields,
    )


@router.get("/android-ui/status", response_model=AndroidUiStatusResponse)
async def status_android_ui(
    request: Request,
    redis: FromDishka[Redis],
) -> AndroidUiStatusResponse:
    state = await load_android_config_state(redis) or {}
    return _android_ui_status_response(request=request, state=state)


@router.post("/android-ui/save-and-stop", response_model=AndroidUiStatusResponse)
async def save_and_stop_android_ui(
    request: Request,
    redis: FromDishka[Redis],
) -> AndroidUiStatusResponse:
    state = await load_android_config_state(redis)
    if not android_config_state_is_active(state):
        status = str((state or {}).get("status") or "idle")
        return _android_ui_status_response(
            request=request,
            state={**(state or {}), "status": status},
            message="Android UI is not active",
        )

    await patch_android_config_state(redis, finish_requested=True)
    state = await _wait_for_config_terminal(redis, timeout_seconds=240.0)
    status = str(state.get("status") or "stopped")
    if status == "failed":
        raise HTTPException(status_code=500, detail=state.get("error") or "Android UI failed")
    return _android_ui_status_response(request=request, state={**state, "status": status})


@router.post("/android-ui/stop", response_model=AndroidUiStatusResponse)
async def stop_android_ui(
    request: Request,
    redis: FromDishka[Redis],
) -> AndroidUiStatusResponse:
    state = await load_android_config_state(redis)
    if not android_config_state_is_active(state):
        return _android_ui_status_response(
            request=request,
            state={**(state or {}), "status": str((state or {}).get("status") or "idle")},
            message="Android UI is not active",
        )

    await patch_android_config_state(redis, stop_requested=True)
    state = await _wait_for_config_terminal(redis, timeout_seconds=180.0)
    status = str(state.get("status") or "stopped")
    if status == "failed":
        raise HTTPException(status_code=500, detail=state.get("error") or "Android UI failed")
    return _android_ui_status_response(request=request, state={**state, "status": status})
