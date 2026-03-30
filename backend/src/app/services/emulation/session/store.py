from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING

from redis.asyncio import Redis

from app.api.modules.emulation.models import (
    AnalysisStatus,
    SESSION_TERMINAL_STATUSES,
    SessionStatus,
)
from ..core.ad_analytics import build_ads_analytics

_TTL = 86400
_STALE_TERMINAL_LOCK_GRACE_SECONDS = 20.0
_STALE_ACTIVE_LOCK_GRACE_SECONDS = 30.0
logger = logging.getLogger(__name__)


def _watched_duration_seconds(watched_videos: list[dict[str, object]]) -> int:
    total = 0.0
    for item in watched_videos:
        try:
            total += float(item.get("watched_seconds") or 0.0)
        except (TypeError, ValueError, AttributeError):
            continue
    return int(round(total))


def _merge_live_capture_analysis(
    *,
    current_ads: list[dict[str, object]],
    next_ads: list[dict[str, object]],
) -> list[dict[str, object]]:
    current_by_position: dict[int, dict[str, object]] = {}
    for item in current_ads:
        if not isinstance(item, dict):
            continue
        position = item.get("position")
        capture = item.get("capture")
        if isinstance(position, int) and isinstance(capture, dict):
            current_by_position[position] = dict(capture)

    merged_ads: list[dict[str, object]] = []
    for item in next_ads:
        if not isinstance(item, dict):
            merged_ads.append(item)
            continue

        ad = dict(item)
        position = ad.get("position")
        capture = ad.get("capture")
        existing_capture = (
            current_by_position.get(position)
            if isinstance(position, int)
            else None
        )
        if not isinstance(capture, dict) or existing_capture is None:
            merged_ads.append(ad)
            continue

        merged_capture = dict(capture)
        existing_status = existing_capture.get("analysis_status")
        if existing_status is not None:
            merged_capture["analysis_status"] = existing_status
        if existing_capture.get("analysis_summary") is not None:
            merged_capture["analysis_summary"] = existing_capture.get("analysis_summary")

        if str(existing_status or "").lower() == AnalysisStatus.NOT_RELEVANT:
            merged_capture["video_file"] = None
            merged_capture["landing_url"] = None
            merged_capture["landing_dir"] = None
            merged_capture["screenshot_paths"] = []
        else:
            for key in ("video_file", "landing_url", "landing_dir", "screenshot_paths"):
                if merged_capture.get(key) in (None, []):
                    fallback = existing_capture.get(key)
                    if fallback not in (None, []):
                        merged_capture[key] = fallback

        ad["capture"] = merged_capture
        merged_ads.append(ad)

    return merged_ads

if TYPE_CHECKING:
    from .state import SessionState


class EmulationSessionStore:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def _key(self, session_id: str) -> str:
        return f"emulation:session:{session_id}"

    def _run_lock_key(self, session_id: str) -> str:
        return f"emulation:session:lock:{session_id}"

    def _profile_lock_key(self, profile_id: str) -> str:
        return f"emulation:profile:lock:{profile_id}"

    def _analysis_lock_key(self, session_id: str) -> str:
        return f"emulation:session:analysis_lock:{session_id}"

    @staticmethod
    def _holder_session_id(holder: str | None) -> str | None:
        if not holder:
            return None
        session_id, _, _ = holder.partition(":")
        return session_id or None

    async def create(
        self,
        session_id: str,
        topics: list[str],
        duration_minutes: int,
        profile_id: str | None = None,
    ) -> None:
        now_ts = time.time()
        data = {
            "status": SessionStatus.QUEUED,
            "stop_requested": False,
            "created_at": now_ts,
            "updated_at": now_ts,
            "started_at": None,
            "finished_at": None,
            "duration_minutes": duration_minutes,
            "topics": topics,
            "profile_id": profile_id,
            "current_topic": None,
            "current_watch": None,
            "topics_searched": [],
            "videos_watched": 0,
            "watched_videos_count": 0,
            "watched_videos": [],
            "watched_ads_count": 0,
            "watched_ads": [],
            "watched_ads_analytics": [],
            "total_duration_seconds": 0,
            "bytes_downloaded": 0,
            "post_processing_status": None,
            "post_processing_done": 0,
            "post_processing_total": 0,
            "mode": None,
            "fatigue": None,
            "personality": None,
            "orchestration": None,
            "error": None,
        }
        await self._redis.set(self._key(session_id), json.dumps(data), ex=_TTL)

    async def update(self, session_id: str, **fields: object) -> None:
        raw_session_payload = await self._redis.get(self._key(session_id))
        if raw_session_payload is None:
            return
        session_data = json.loads(raw_session_payload)
        fields.setdefault("updated_at", time.time())
        session_data.update(fields)
        await self._redis.set(self._key(session_id), json.dumps(session_data), ex=_TTL)

    async def get(self, session_id: str) -> dict | None:
        raw_session_payload = await self._redis.get(self._key(session_id))
        if raw_session_payload is None:
            return None
        return json.loads(raw_session_payload)

    async def delete(self, session_id: str) -> None:
        await self._redis.delete(self._key(session_id))

    async def try_acquire_run_lock(
        self,
        session_id: str,
        holder: str,
        ttl_seconds: int,
    ) -> bool:
        locked = await self._redis.set(
            self._run_lock_key(session_id),
            holder,
            ex=max(ttl_seconds, 1),
            nx=True,
        )
        return bool(locked)

    async def release_run_lock(self, session_id: str, holder: str) -> None:
        key = self._run_lock_key(session_id)
        current_holder = await self._redis.get(key)
        if current_holder is None:
            return
        if isinstance(current_holder, bytes):
            current_holder = current_holder.decode("utf-8", errors="ignore")
        if str(current_holder) != holder:
            return
        await self._redis.delete(key)

    async def is_run_lock_active(self, session_id: str) -> bool:
        return bool(await self._redis.exists(self._run_lock_key(session_id)))

    async def try_acquire_profile_lock(
        self,
        profile_id: str,
        holder: str,
        ttl_seconds: int,
    ) -> bool:
        key = self._profile_lock_key(profile_id)
        ttl = max(ttl_seconds, 1)

        for attempt in range(3):
            locked = await self._redis.set(key, holder, ex=ttl, nx=True)
            if locked:
                return True

            current_holder = await self._redis.get(key)
            if isinstance(current_holder, bytes):
                current_holder = current_holder.decode("utf-8", errors="ignore")
            current_ttl_ms = await self._redis.pttl(key)
            current_session_id = self._holder_session_id(str(current_holder) if current_holder else None)

            is_stale = False
            current_status = None
            current_age_seconds = None
            if current_session_id:
                current_payload = await self.get(current_session_id)
                if current_payload is None:
                    is_stale = True
                else:
                    current_status = current_payload.get("status")
                    updated_at = current_payload.get("updated_at")
                    if isinstance(updated_at, (int, float)):
                        current_age_seconds = max(0.0, time.time() - updated_at)

                run_lock_active = await self.is_run_lock_active(current_session_id)
                if not run_lock_active:
                    is_stale = True
                elif (
                    current_status in SESSION_TERMINAL_STATUSES
                    and current_age_seconds is not None
                    and current_age_seconds >= _STALE_TERMINAL_LOCK_GRACE_SECONDS
                ):
                    is_stale = True
                elif (
                    current_status in {SessionStatus.RUNNING, SessionStatus.QUEUED}
                    and current_age_seconds is not None
                    and current_age_seconds >= _STALE_ACTIVE_LOCK_GRACE_SECONDS
                ):
                    is_stale = True

            if is_stale and current_holder:
                logger.warning(
                    "Clearing stale profile lock for %s held by %s (ttl_ms=%s status=%s age=%.1fs)",
                    profile_id,
                    current_holder,
                    current_ttl_ms,
                    current_status,
                    current_age_seconds or -1.0,
                )
                latest_holder = await self._redis.get(key)
                if isinstance(latest_holder, bytes):
                    latest_holder = latest_holder.decode("utf-8", errors="ignore")
                if latest_holder == current_holder:
                    if current_session_id:
                        await self._redis.delete(self._run_lock_key(current_session_id))
                    await self._redis.delete(key)
                    continue

            logger.warning(
                "Profile lock busy for %s on attempt %s/3 (holder=%s ttl_ms=%s stale=%s status=%s age=%.1fs)",
                profile_id,
                attempt + 1,
                current_holder,
                current_ttl_ms,
                is_stale,
                current_status,
                current_age_seconds or -1.0,
            )
            if attempt < 2:
                await asyncio.sleep(0.25 * (attempt + 1))

        return False

    async def release_profile_lock(self, profile_id: str, holder: str) -> None:
        key = self._profile_lock_key(profile_id)
        current_holder = await self._redis.get(key)
        if current_holder is None:
            return
        if isinstance(current_holder, bytes):
            current_holder = current_holder.decode("utf-8", errors="ignore")
        if str(current_holder) != holder:
            return
        await self._redis.delete(key)

    async def try_acquire_analysis_lock(
        self,
        session_id: str,
        holder: str,
        ttl_seconds: int,
    ) -> bool:
        locked = await self._redis.set(
            self._analysis_lock_key(session_id),
            holder,
            ex=max(ttl_seconds, 1),
            nx=True,
        )
        return bool(locked)

    async def release_analysis_lock(self, session_id: str, holder: str) -> None:
        key = self._analysis_lock_key(session_id)
        current_holder = await self._redis.get(key)
        if current_holder is None:
            return
        if isinstance(current_holder, bytes):
            current_holder = current_holder.decode("utf-8", errors="ignore")
        if str(current_holder) != holder:
            return
        await self._redis.delete(key)

    async def clear_session_locks(
        self,
        session_id: str,
        *,
        profile_id: str | None = None,
    ) -> None:
        await self._redis.delete(self._run_lock_key(session_id))
        await self._redis.delete(self._analysis_lock_key(session_id))
        if not profile_id:
            return

        key = self._profile_lock_key(profile_id)
        current_holder = await self._redis.get(key)
        if current_holder is None:
            return
        if isinstance(current_holder, bytes):
            current_holder = current_holder.decode("utf-8", errors="ignore")
        if self._holder_session_id(str(current_holder)) != session_id:
            return
        await self._redis.delete(key)

    async def sync_progress(
        self,
        session_id: str,
        state: SessionState,
        bytes_downloaded: int,
    ) -> None:
        current_payload = await self.get(session_id) or {}
        watched_ads = _merge_live_capture_analysis(
            current_ads=current_payload.get("watched_ads") or [],
            next_ads=state.watched_ads,
        )
        await self.update(
            session_id,
            status=SessionStatus.RUNNING,
            mode=state.mode.value,
            fatigue=round(state.fatigue, 2),
            current_topic=state.current_topic,
            current_watch=state.current_watch,
            topics_searched=state.searched_topics,
            videos_watched=state.completed_watched_videos_count(),
            watched_videos_count=state.watched_videos_count(),
            watched_videos=state.watched_videos,
            watched_ads_count=len(watched_ads),
            watched_ads=watched_ads,
            watched_ads_analytics=build_ads_analytics(watched_ads),
            total_duration_seconds=_watched_duration_seconds(state.watched_videos),
            bytes_downloaded=bytes_downloaded,
            personality={
                "pace": state.personality.pace,
                "patience": state.personality.patience,
                "focus_span": state.personality.focus_span,
                "search_style": state.personality.search_style,
                "ad_tolerance": state.personality.ad_tolerance,
            },
        )
