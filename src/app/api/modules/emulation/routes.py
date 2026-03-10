import uuid

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter
from fastapi.params import Path, Query

from .schema import (
    EmulationHistoryDetailResponse,
    EmulationHistoryParams,
    EmulationHistoryResponse,
    EmulationSessionStatus,
    StartEmulationRequest,
    StartEmulationResponse,
)
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
