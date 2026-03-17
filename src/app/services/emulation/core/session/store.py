from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from redis.asyncio import Redis

from ..ad_analytics import build_ads_analytics

_TTL = 86400


def _watched_duration_seconds(watched_videos: list[dict[str, object]]) -> int:
    total = 0.0
    for item in watched_videos:
        try:
            total += float(item.get("watched_seconds") or 0.0)
        except (TypeError, ValueError, AttributeError):
            continue
    return int(round(total))

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

    async def create(
        self,
        session_id: str,
        topics: list[str],
        duration_minutes: int,
        profile_id: str | None = None,
    ) -> None:
        now_ts = time.time()
        data = {
            "status": "queued",
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
        locked = await self._redis.set(
            self._profile_lock_key(profile_id),
            holder,
            ex=max(ttl_seconds, 1),
            nx=True,
        )
        return bool(locked)

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

    async def sync_progress(
        self,
        session_id: str,
        state: SessionState,
        bytes_downloaded: int,
    ) -> None:
        await self.update(
            session_id,
            status="running",
            mode=state.mode.value,
            fatigue=round(state.fatigue, 2),
            current_topic=state.current_topic,
            current_watch=state.current_watch,
            topics_searched=state.searched_topics,
            videos_watched=state.videos_watched,
            watched_videos_count=len(state.watched_videos),
            watched_videos=state.watched_videos,
            watched_ads_count=len(state.watched_ads),
            watched_ads=state.watched_ads,
            watched_ads_analytics=build_ads_analytics(state.watched_ads),
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
