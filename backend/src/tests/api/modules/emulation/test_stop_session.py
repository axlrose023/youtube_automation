from __future__ import annotations

import asyncio

import pytest

from app.api.modules.emulation.models import SessionStatus
from app.api.modules.emulation.service import EmulationSessionService
from app.tasks.android_emulation import _android_stop_watcher


SESSION_ID = "00000000-0000-0000-0000-000000000001"


class FakeSessionStore:
    def __init__(self, payload: dict[str, object] | None) -> None:
        self.payload = dict(payload) if payload is not None else None
        self.updates: list[dict[str, object]] = []

    async def get(self, session_id: str) -> dict[str, object] | None:
        assert session_id == SESSION_ID
        return dict(self.payload) if self.payload is not None else None

    async def update(self, session_id: str, **fields: object) -> None:
        assert session_id == SESSION_ID
        self.updates.append(dict(fields))
        if self.payload is not None:
            self.payload.update(fields)


class FakeHistoryService:
    def __init__(self) -> None:
        self.stopped_sessions: list[tuple[str, dict[str, object]]] = []

    async def mark_stopped(self, session_id: str, live_payload: dict[str, object]) -> None:
        self.stopped_sessions.append((session_id, live_payload))


@pytest.mark.asyncio
async def test_stop_running_session_sets_real_stopping_state() -> None:
    store = FakeSessionStore({"status": SessionStatus.RUNNING, "stop_requested": False})
    history = FakeHistoryService()
    service = EmulationSessionService(store, history)

    response = await service.stop_session(SESSION_ID)

    assert response.status == SessionStatus.STOPPING
    assert store.payload is not None
    assert store.payload["status"] == SessionStatus.STOPPING
    assert store.payload["stop_requested"] is True
    assert history.stopped_sessions == []


@pytest.mark.asyncio
async def test_stop_running_session_is_idempotent_when_already_stopping() -> None:
    store = FakeSessionStore({"status": SessionStatus.STOPPING, "stop_requested": True})
    history = FakeHistoryService()
    service = EmulationSessionService(store, history)

    response = await service.stop_session(SESSION_ID)

    assert response.status == SessionStatus.STOPPING
    assert store.updates == []
    assert history.stopped_sessions == []


@pytest.mark.asyncio
async def test_stop_queued_session_finishes_immediately() -> None:
    store = FakeSessionStore({"status": SessionStatus.QUEUED, "stop_requested": False})
    history = FakeHistoryService()
    service = EmulationSessionService(store, history)

    response = await service.stop_session(SESSION_ID)

    assert response.status == SessionStatus.STOPPED
    assert store.payload is not None
    assert store.payload["status"] == SessionStatus.STOPPED
    assert store.payload["stop_requested"] is False
    assert len(history.stopped_sessions) == 1


@pytest.mark.asyncio
async def test_android_stop_watcher_accepts_legacy_stop_requested_flag() -> None:
    store = FakeSessionStore({"status": SessionStatus.RUNNING, "stop_requested": True})
    runner_stop_event = asyncio.Event()
    heartbeat_stop = asyncio.Event()

    await asyncio.wait_for(
        _android_stop_watcher(
            session_id=SESSION_ID,
            session_store=store,
            runner_stop_event=runner_stop_event,
            heartbeat_stop=heartbeat_stop,
        ),
        timeout=1,
    )

    assert runner_stop_event.is_set()
