import uuid
from pathlib import Path

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.params import Path, Query
from fastapi.responses import FileResponse

from app.api.modules.auth.services.auth import AuthenticateUser
from app.database.uow import UnitOfWork
from app.settings import Config, get_config

from .schema import (
    EmulationCapturesResponse,
    EmulationHistoryDetailResponse,
    EmulationHistoryParams,
    EmulationHistoryResponse,
    EmulationSessionActionRequest,
    EmulationSessionStatus,
    StartEmulationRequest,
    StartEmulationResponse,
    StopEmulationResponse,
)
from .service import EmulationHistoryService, EmulationSessionService

router = APIRouter(route_class=DishkaRoute)


def _resolve_media_path(media_path: str) -> Path:
    base_path = get_config().storage.ad_captures_path.resolve()
    candidate = (base_path / media_path).resolve()

    try:
        candidate.relative_to(base_path)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Media file not found") from exc

    if not candidate.exists() or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Media file not found")

    return candidate


@router.post("/start", response_model=StartEmulationResponse)
async def start_emulation(
    request: StartEmulationRequest,
    session_service: FromDishka[EmulationSessionService],
) -> StartEmulationResponse:
    return await session_service.start_emulation(request)


@router.get("/history", response_model=EmulationHistoryResponse)
async def get_emulation_history(
    session_service: FromDishka[EmulationSessionService],
    params: EmulationHistoryParams = Query(),
) -> EmulationHistoryResponse:
    return await session_service.get_history(params)


@router.get("/history/{session_id}", response_model=EmulationHistoryDetailResponse)
async def get_emulation_history_detail(
    session_service: FromDishka[EmulationSessionService],
    session_id: uuid.UUID = Path(...),
    include_raw_ads: bool = Query(False),
    include_captures: bool = Query(True),
) -> EmulationHistoryDetailResponse:
    return await session_service.get_session_detail(
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


@router.post("/stop", response_model=StopEmulationResponse)
async def stop_emulation(
    request: EmulationSessionActionRequest,
    session_service: FromDishka[EmulationSessionService],
) -> StopEmulationResponse:
    return await session_service.stop_session(str(request.session_id))


@router.post("/retry", response_model=StartEmulationResponse)
async def retry_emulation(
    request: EmulationSessionActionRequest,
    session_service: FromDishka[EmulationSessionService],
) -> StartEmulationResponse:
    return await session_service.retry_session(str(request.session_id))


@router.post("/resume", response_model=StartEmulationResponse)
async def resume_emulation(
    request: EmulationSessionActionRequest,
    session_service: FromDishka[EmulationSessionService],
) -> StartEmulationResponse:
    return await session_service.resume_session(str(request.session_id))


@router.delete("/history/{session_id}", status_code=204)
async def delete_emulation_history(
    session_id: str,
    session_service: FromDishka[EmulationSessionService],
) -> Response:
    await session_service.delete_session(session_id)
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


@router.get("/media/{media_path:path}")
async def get_emulation_media(
    media_path: str,
    request: Request,
    access_token: str = Query(..., min_length=1),
) -> FileResponse:
    container = request.state.dishka_container
    uow: UnitOfWork = await container.get(UnitOfWork)
    config: Config = await container.get(Config)
    await AuthenticateUser().get_current_user(uow=uow, token=access_token, config=config)
    resolved_path = _resolve_media_path(media_path)
    return FileResponse(
        resolved_path,
        filename=resolved_path.name,
        content_disposition_type="inline",
    )
