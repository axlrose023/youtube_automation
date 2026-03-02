from __future__ import annotations

import json
import time

from redis.asyncio import Redis

_TTL = 86400


class EmulationSessionStore:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def _key(self, session_id: str) -> str:
        return f"emulation:session:{session_id}"

    async def create(
        self,
        session_id: str,
        topics: list[str],
        duration_minutes: int,
    ) -> None:
        data = {
            "status": "queued",
            "created_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "duration_minutes": duration_minutes,
            "topics": topics,
            "topics_searched": [],
            "videos_watched": 0,
            "watched_videos_count": 0,
            "watched_videos": [],
            "total_duration_seconds": 0,
            "bytes_downloaded": 0,
            "error": None,
        }
        await self._redis.set(self._key(session_id), json.dumps(data), ex=_TTL)

    async def update(self, session_id: str, **fields: object) -> None:
        raw_session_payload = await self._redis.get(self._key(session_id))
        if raw_session_payload is None:
            return
        session_data = json.loads(raw_session_payload)
        session_data.update(fields)
        await self._redis.set(self._key(session_id), json.dumps(session_data), ex=_TTL)

    async def get(self, session_id: str) -> dict | None:
        raw_session_payload = await self._redis.get(self._key(session_id))
        if raw_session_payload is None:
            return None
        return json.loads(raw_session_payload)

    async def sync_progress(
        self,
        session_id: str,
        state: "SessionState",
        bytes_downloaded: int,
    ) -> None:
        await self.update(
            session_id,
            status="running",
            mode=state.mode.value,
            fatigue=round(state.fatigue, 2),
            topics_searched=state.searched_topics,
            videos_watched=state.videos_watched,
            watched_videos_count=len(state.watched_videos),
            bytes_downloaded=bytes_downloaded,
        )
