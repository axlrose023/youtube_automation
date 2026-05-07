import asyncio
import os
import time
import uuid

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from redis.asyncio import Redis
from taskiq.kicker import AsyncKicker

from app.api.common.auth import AuthenticateMainRoles
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


class AndroidUiStartResponse(BaseModel):
    novnc_url: str
    status: str
    message: str | None = None


class AndroidUiStatusResponse(BaseModel):
    status: str
    novnc_url: str | None = None
    message: str | None = None
    snapshot_name: str | None = None
    snapshot_saved: bool | None = None
    error: str | None = None


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


async def _enqueue_android_config_task(holder: str) -> None:
    from app.tiq import ANDROID_EMULATION_QUEUE_NAME, android_emulation_dispatch_broker

    await AsyncKicker(
        broker=android_emulation_dispatch_broker,
        task_name="android_config_ui_task",
        labels={"queue_name": ANDROID_EMULATION_QUEUE_NAME},
    ).kiq(holder)


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
    config: FromDishka[Config],
) -> AndroidUiStartResponse:
    novnc_url = _config_novnc_url(request)
    state = await load_android_config_state(redis)
    if android_config_state_is_active(state) and not android_config_state_is_stale(state):
        return AndroidUiStartResponse(
            novnc_url=novnc_url,
            status=str(state.get("status") or "running"),
            message="Android UI is already active",
        )

    if android_config_state_is_stale(state):
        from app.tasks.android_emulation import _android_device_lock_id

        await session_store.clear_session_locks(
            ANDROID_CONFIG_SESSION_ID,
            profile_id=_android_device_lock_id(config),
        )

    holder = uuid.uuid4().hex
    await save_android_config_state(
        redis,
        {
            "status": "queued",
            "holder": holder,
            "novnc_url": novnc_url,
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
    await _enqueue_android_config_task(holder)
    return AndroidUiStartResponse(novnc_url=novnc_url, status="queued")


@router.get("/android-ui/status", response_model=AndroidUiStatusResponse)
async def status_android_ui(
    request: Request,
    redis: FromDishka[Redis],
) -> AndroidUiStatusResponse:
    state = await load_android_config_state(redis) or {}
    status = str(state.get("status") or "idle")
    return AndroidUiStatusResponse(
        status=status,
        novnc_url=state.get("novnc_url") or _config_novnc_url(request),
        snapshot_name=state.get("snapshot_name"),
        snapshot_saved=state.get("snapshot_saved"),
        error=state.get("error"),
    )


@router.post("/android-ui/save-and-stop", response_model=AndroidUiStatusResponse)
async def save_and_stop_android_ui(
    request: Request,
    redis: FromDishka[Redis],
) -> AndroidUiStatusResponse:
    state = await load_android_config_state(redis)
    if not android_config_state_is_active(state):
        status = str((state or {}).get("status") or "idle")
        return AndroidUiStatusResponse(
            status=status,
            novnc_url=(state or {}).get("novnc_url") or _config_novnc_url(request),
            snapshot_name=(state or {}).get("snapshot_name"),
            snapshot_saved=(state or {}).get("snapshot_saved"),
            error=(state or {}).get("error"),
            message="Android UI is not active",
        )

    await patch_android_config_state(redis, finish_requested=True)
    state = await _wait_for_config_terminal(redis, timeout_seconds=240.0)
    status = str(state.get("status") or "stopped")
    if status == "failed":
        raise HTTPException(status_code=500, detail=state.get("error") or "Android UI failed")
    return AndroidUiStatusResponse(
        status=status,
        novnc_url=state.get("novnc_url"),
        snapshot_name=state.get("snapshot_name"),
        snapshot_saved=state.get("snapshot_saved"),
    )


@router.post("/android-ui/stop", response_model=AndroidUiStatusResponse)
async def stop_android_ui(
    request: Request,
    redis: FromDishka[Redis],
) -> AndroidUiStatusResponse:
    state = await load_android_config_state(redis)
    if not android_config_state_is_active(state):
        return AndroidUiStatusResponse(
            status=str((state or {}).get("status") or "idle"),
            novnc_url=(state or {}).get("novnc_url") or _config_novnc_url(request),
            snapshot_name=(state or {}).get("snapshot_name"),
            snapshot_saved=(state or {}).get("snapshot_saved"),
            error=(state or {}).get("error"),
            message="Android UI is not active",
        )

    await patch_android_config_state(redis, stop_requested=True)
    state = await _wait_for_config_terminal(redis, timeout_seconds=180.0)
    status = str(state.get("status") or "stopped")
    if status == "failed":
        raise HTTPException(status_code=500, detail=state.get("error") or "Android UI failed")
    return AndroidUiStatusResponse(
        status=status,
        novnc_url=state.get("novnc_url"),
        snapshot_name=state.get("snapshot_name"),
        snapshot_saved=state.get("snapshot_saved"),
    )
