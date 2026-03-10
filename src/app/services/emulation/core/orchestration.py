from __future__ import annotations

import random
import time

from .config import (
    ORCHESTRATION_ACTIVE_BUDGET_FRACTION,
    ORCHESTRATION_ACTIVE_CHUNK_SECONDS,
    ORCHESTRATION_BREAK_SECONDS,
    ORCHESTRATION_MIN_ACTIVE_REMAINDER_SECONDS,
    ORCHESTRATION_MIN_NEXT_CHUNK_SECONDS,
    ORCHESTRATION_MIN_WINDOW_MINUTES,
)

type OrchestrationPayload = dict[str, object]


def should_orchestrate_window(
    duration_minutes: int,
    realistic_window: bool | None,
) -> bool:
    if realistic_window is False:
        return False
    return duration_minutes >= ORCHESTRATION_MIN_WINDOW_MINUTES


def build_orchestration_payload(
    live_payload: dict,
    duration_minutes: int,
    realistic_window: bool | None,
) -> OrchestrationPayload | None:
    if not should_orchestrate_window(
        duration_minutes=duration_minutes,
        realistic_window=realistic_window,
    ):
        return None

    existing = live_payload.get("orchestration")
    watched_ads_len = len(live_payload.get("watched_ads") or [])
    now_ts = time.time()
    window_seconds = max(duration_minutes * 60, ORCHESTRATION_MIN_NEXT_CHUNK_SECONDS)

    if isinstance(existing, dict) and existing.get("enabled"):
        payload = dict(existing)
        payload["enabled"] = True
        payload["phase"] = "running"
        payload["next_resume_at"] = None
        payload["total_window_seconds"] = _positive_int(
            payload.get("total_window_seconds"),
            default=window_seconds,
        )
        payload["window_started_at"] = _positive_float(
            payload.get("window_started_at"),
            default=_positive_float(live_payload.get("started_at"), default=now_ts),
        )
        payload["active_budget_seconds"] = _positive_int(
            payload.get("active_budget_seconds"),
            default=max(
                ORCHESTRATION_MIN_NEXT_CHUNK_SECONDS,
                int(
                    payload["total_window_seconds"]
                    * random.uniform(*ORCHESTRATION_ACTIVE_BUDGET_FRACTION)
                ),
            ),
        )
        payload["active_spent_seconds"] = min(
            _positive_int(payload.get("active_spent_seconds"), default=0),
            payload["active_budget_seconds"],
        )
        payload["chunk_index"] = clamp_non_negative_int(payload.get("chunk_index"))
        payload["persisted_ads_count"] = clamp_non_negative_int(
            payload.get("persisted_ads_count")
        )
        if payload["persisted_ads_count"] > watched_ads_len:
            payload["persisted_ads_count"] = watched_ads_len
        return payload

    active_budget_seconds = int(
        window_seconds * random.uniform(*ORCHESTRATION_ACTIVE_BUDGET_FRACTION)
    )
    active_budget_seconds = max(
        ORCHESTRATION_MIN_NEXT_CHUNK_SECONDS,
        min(active_budget_seconds, window_seconds),
    )
    return {
        "enabled": True,
        "phase": "running",
        "window_started_at": _positive_float(
            live_payload.get("started_at"),
            default=now_ts,
        ),
        "total_window_seconds": window_seconds,
        "active_budget_seconds": active_budget_seconds,
        "active_spent_seconds": 0,
        "next_resume_at": None,
        "chunk_index": 0,
        "persisted_ads_count": watched_ads_len,
        "last_chunk_seconds": 0,
        "last_break_seconds": 0,
    }


def pick_chunk_seconds(orchestration: OrchestrationPayload) -> int:
    now_ts = time.time()
    remaining_window = remaining_window_seconds(orchestration, now_ts)
    active_budget = clamp_non_negative_int(orchestration.get("active_budget_seconds"))
    active_spent = clamp_non_negative_int(orchestration.get("active_spent_seconds"))
    remaining_active = max(active_budget - active_spent, 0)
    max_chunk = min(remaining_window, remaining_active)
    if max_chunk <= ORCHESTRATION_MIN_ACTIVE_REMAINDER_SECONDS:
        return max_chunk

    upper = min(ORCHESTRATION_ACTIVE_CHUNK_SECONDS[1], max_chunk)
    lower = min(ORCHESTRATION_ACTIVE_CHUNK_SECONDS[0], upper)
    if upper <= 0:
        return 0

    chunk = random.randint(lower, upper)
    if (max_chunk - chunk) < ORCHESTRATION_MIN_ACTIVE_REMAINDER_SECONDS:
        chunk = max_chunk
    if (
        chunk < ORCHESTRATION_MIN_NEXT_CHUNK_SECONDS
        and max_chunk >= ORCHESTRATION_MIN_NEXT_CHUNK_SECONDS
    ):
        chunk = ORCHESTRATION_MIN_NEXT_CHUNK_SECONDS
    return max(min(chunk, max_chunk), 0)


def pick_break_seconds(orchestration: OrchestrationPayload, now_ts: float) -> int:
    remaining_window = remaining_window_seconds(orchestration, now_ts)
    if remaining_window <= ORCHESTRATION_MIN_NEXT_CHUNK_SECONDS:
        return 1

    max_break = min(
        ORCHESTRATION_BREAK_SECONDS[1],
        max(remaining_window - ORCHESTRATION_MIN_NEXT_CHUNK_SECONDS, 1),
    )
    min_break = min(ORCHESTRATION_BREAK_SECONDS[0], max_break)
    if max_break <= 1:
        return 1
    return max(1, random.randint(min_break, max_break))


def remaining_window_seconds(orchestration: OrchestrationPayload, now_ts: float) -> int:
    total_window = clamp_non_negative_int(orchestration.get("total_window_seconds"))
    window_started_at = _positive_float(
        orchestration.get("window_started_at"),
        default=now_ts,
    )
    elapsed = max(now_ts - window_started_at, 0.0)
    return max(total_window - int(elapsed), 0)


def should_finalize_window(
    orchestration: OrchestrationPayload,
    active_spent_seconds: int,
    now_ts: float,
) -> bool:
    active_budget = clamp_non_negative_int(orchestration.get("active_budget_seconds"))
    remaining_active = max(active_budget - active_spent_seconds, 0)
    remaining_window = remaining_window_seconds(orchestration, now_ts)
    return (
        remaining_window <= ORCHESTRATION_MIN_NEXT_CHUNK_SECONDS
        or remaining_active <= ORCHESTRATION_MIN_ACTIVE_REMAINDER_SECONDS
    )


def clamp_non_negative_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float):
        return max(int(value), 0)
    return 0


def _positive_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return max(int(value), 0)
    return default


def _positive_float(value: object, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, int | float):
        return max(float(value), 0.0)
    return default
