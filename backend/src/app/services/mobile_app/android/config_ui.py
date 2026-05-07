from __future__ import annotations

import json
import time
from typing import Any

from redis.asyncio import Redis

ANDROID_CONFIG_SESSION_ID = "__android_config__"
ANDROID_CONFIG_STATE_KEY = "android:config-ui:state"
ANDROID_CONFIG_STATE_TTL_SECONDS = 12 * 60 * 60
ANDROID_CONFIG_ACTIVE_STATUSES = {
    "queued",
    "starting",
    "running",
    "saving",
    "stopping",
}
ANDROID_CONFIG_TERMINAL_STATUSES = {"stopped", "failed"}
ANDROID_CONFIG_STALE_SECONDS = 60.0


def android_config_snapshot_name(runtime_snapshot_name: str | None, warm_snapshot_name: str) -> str:
    return (runtime_snapshot_name or "").strip() or warm_snapshot_name


def build_novnc_url(
    *,
    public_url: str | None,
    novnc_port: str | int,
    direct_url: str | None = None,
) -> str:
    query = "autoconnect=true&resize=scale"
    if direct_url:
        separator = "&" if "?" in direct_url else "?"
        return f"{direct_url.rstrip('/')}{separator}{query}"
    if public_url:
        if str(public_url).rstrip("/").endswith(f":{novnc_port}"):
            return f"{public_url.rstrip('/')}/vnc.html?{query}"
        return f"{public_url.rstrip('/')}/novnc/vnc.html?{query}"
    return f"http://localhost:{novnc_port}/vnc.html?{query}"


def android_config_state_is_active(state: dict[str, Any] | None) -> bool:
    return bool(state and state.get("status") in ANDROID_CONFIG_ACTIVE_STATUSES)


def android_config_state_is_stale(state: dict[str, Any] | None) -> bool:
    if not android_config_state_is_active(state):
        return False
    updated_at = state.get("updated_at")
    if not isinstance(updated_at, (int, float)):
        return True
    return time.time() - float(updated_at) >= ANDROID_CONFIG_STALE_SECONDS


async def load_android_config_state(redis: Redis) -> dict[str, Any] | None:
    raw = await redis.get(ANDROID_CONFIG_STATE_KEY)
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def save_android_config_state(redis: Redis, state: dict[str, Any]) -> dict[str, Any]:
    next_state = dict(state)
    next_state["updated_at"] = time.time()
    await redis.set(
        ANDROID_CONFIG_STATE_KEY,
        json.dumps(next_state),
        ex=ANDROID_CONFIG_STATE_TTL_SECONDS,
    )
    return next_state


async def patch_android_config_state(redis: Redis, **fields: Any) -> dict[str, Any]:
    state = await load_android_config_state(redis) or {}
    state.update(fields)
    return await save_android_config_state(redis, state)
