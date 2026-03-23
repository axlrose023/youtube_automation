import uuid

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter
from fastapi.params import Path, Query

from fastapi import Response

from .schema import (
    EmulationCapturesResponse,
    EmulationHistoryDetailResponse,
    EmulationHistoryParams,
    EmulationHistoryResponse,
    EmulationSessionStatus,
    StartEmulationRequest,
    StartEmulationResponse,
    StopEmulationResponse,
)
from app.services.emulation.core.session.store import EmulationSessionStore

from .service import EmulationHistoryService, EmulationSessionService

router = APIRouter(route_class=DishkaRoute)


@router.post("/start", response_model=StartEmulationResponse)
async def start_emulation(
    request: StartEmulationRequest,
    session_service: FromDishka[EmulationSessionService],
) -> StartEmulationResponse:
    return await session_service.start_emulation(request)


@router.get("/history", response_model=EmulationHistoryResponse)
async def get_emulation_history(
    history_service: FromDishka[EmulationHistoryService],
    params: EmulationHistoryParams = Query(),
) -> EmulationHistoryResponse:
    return await history_service.get_history(params)


@router.get("/history/{session_id}", response_model=EmulationHistoryDetailResponse)
async def get_emulation_history_detail(
    history_service: FromDishka[EmulationHistoryService],
    session_id: uuid.UUID = Path(...),
    include_raw_ads: bool = Query(False),
    include_captures: bool = Query(True),
) -> EmulationHistoryDetailResponse:
    return await history_service.get_session_detail(
        session_id=str(session_id),
        include_raw_ads=include_raw_ads,
        include_captures=include_captures,
    )


@router.get("/{session_id}/status", response_model=EmulationSessionStatus)
async def get_emulation_status(
    session_id: str,
    session_service: FromDishka[EmulationSessionService],
) -> EmulationSessionStatus:
    return await session_service.get_status(session_id)


@router.post("/{session_id}/stop", response_model=StopEmulationResponse)
async def stop_emulation(
    session_id: str,
    session_service: FromDishka[EmulationSessionService],
) -> StopEmulationResponse:
    return await session_service.stop_session(session_id)


@router.post("/{session_id}/retry", response_model=StartEmulationResponse)
async def retry_emulation(
    session_id: str,
    session_service: FromDishka[EmulationSessionService],
) -> StartEmulationResponse:
    return await session_service.retry_session(session_id)


@router.post("/{session_id}/resume", response_model=StartEmulationResponse)
async def resume_emulation(
    session_id: str,
    session_service: FromDishka[EmulationSessionService],
) -> StartEmulationResponse:
    return await session_service.resume_session(session_id)


@router.delete("/history/{session_id}", status_code=204)
async def delete_emulation_history(
    session_id: str,
    history_service: FromDishka[EmulationHistoryService],
    session_store: FromDishka[EmulationSessionStore],
) -> Response:
    await history_service.delete_session(session_id)
    await session_store.delete(session_id)
    return Response(status_code=204)


@router.get("/{session_id}/captures", response_model=EmulationCapturesResponse)
async def get_emulation_captures(
    session_id: str,
    history_service: FromDishka[EmulationHistoryService],
    analysis_status: str | None = Query(None),
) -> EmulationCapturesResponse:
    return await history_service.get_session_captures(
        session_id=session_id,
        analysis_status=analysis_status,
    )
