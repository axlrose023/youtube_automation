import time
import uuid

from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, HTTPException
from taskiq.kicker import AsyncKicker

from app.services.emulation.core.session_store import EmulationSessionStore
from app.tiq import EMULATION_QUEUE_NAME, broker

from .schema import (
    EmulationSessionStatus,
    StartEmulationRequest,
    StartEmulationResponse,
)

router = APIRouter()


@router.post("/start", response_model=StartEmulationResponse)
@inject
async def start_emulation(
    request: StartEmulationRequest,
    session_store: FromDishka[EmulationSessionStore],
) -> StartEmulationResponse:
    session_id = str(uuid.uuid4())
    await session_store.create(session_id, request.topics, request.duration_minutes)

    await AsyncKicker(
        broker=broker,
        task_name="emulation_task",
        labels={"queue_name": EMULATION_QUEUE_NAME},
    ).kiq(session_id, request.duration_minutes, request.topics)

    return StartEmulationResponse(session_id=session_id, status="queued")


@router.get("/{session_id}/status", response_model=EmulationSessionStatus)
@inject
async def get_emulation_status(
    session_id: str,
    session_store: FromDishka[EmulationSessionStore],
) -> EmulationSessionStatus:
    data = await session_store.get(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Session not found")

    elapsed = None
    if data.get("started_at"):
        if data.get("status") in {"completed", "failed"} and data.get("finished_at"):
            elapsed = round((data["finished_at"] - data["started_at"]) / 60, 1)
        else:
            elapsed = round((time.time() - data["started_at"]) / 60, 1)

    return EmulationSessionStatus(
        session_id=session_id,
        status=data["status"],
        elapsed_minutes=elapsed,
        bytes_downloaded=data.get("bytes_downloaded", 0),
        topics_searched=data.get("topics_searched", []),
        videos_watched=data.get("videos_watched", 0),
        watched_videos_count=data.get("watched_videos_count", 0),
        total_duration_seconds=data.get("total_duration_seconds", 0),
        watched_videos=data.get("watched_videos", []),
        mode=data.get("mode"),
        fatigue=data.get("fatigue"),
        error=data.get("error"),
    )
