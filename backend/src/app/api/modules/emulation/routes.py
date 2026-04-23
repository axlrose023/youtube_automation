from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Depends, Request
from fastapi.params import Path, Query
from fastapi.responses import FileResponse, StreamingResponse

from app.api.common.auth import AuthenticateMainRoles

from .schema import (
    EmulationCapturesResponse,
    EmulationDashboardSummaryResponse,
    EmulationHistoryDetailResponse,
    EmulationHistoryParams,
    EmulationHistoryResponse,
    EmulationStatusBatchRequest,
    EmulationStatusBatchResponse,
    EmulationSessionActionRequest,
    EmulationSessionStatus,
    StartEmulationRequest,
    StartEmulationResponse,
    StopEmulationResponse,
)
from .service import EmulationHistoryService, EmulationSessionService
from .services.session_runtime import stream_status_events
from .utils import resolve_media_path

router = APIRouter(
    route_class=DishkaRoute,
    dependencies=[Depends(AuthenticateMainRoles())],
)

# Public router — no auth required for serving artifact files (screenshots, videos).
# Mounted at the same /emulation prefix but without the auth dependency.
public_router = APIRouter(route_class=DishkaRoute)
@router.post("/start")
async def start_emulation(
    request: StartEmulationRequest,
    session_service: FromDishka[EmulationSessionService],
) -> StartEmulationResponse:
    return await session_service.start_emulation(request)


@router.get("/history")
async def get_emulation_history(
    session_service: FromDishka[EmulationSessionService],
    params: EmulationHistoryParams = Query(),
) -> EmulationHistoryResponse:
    return await session_service.get_history(params)


@router.get("/dashboard/summary")
async def get_emulation_dashboard_summary(
    session_service: FromDishka[EmulationSessionService],
) -> EmulationDashboardSummaryResponse:
    return await session_service.get_dashboard_summary()


@router.get("/history/{session_id}")
async def get_emulation_history_detail(
    session_service: FromDishka[EmulationSessionService],
    session_id: str = Path(...),
    include_raw_ads: bool = Query(False),
    include_captures: bool = Query(True),
) -> EmulationHistoryDetailResponse:
    return await session_service.get_session_detail(
        session_id=session_id,
        include_raw_ads=include_raw_ads,
        include_captures=include_captures,
    )


@router.get("/{session_id}/status")
async def get_emulation_status(
    session_id: str,
    session_service: FromDishka[EmulationSessionService],
) -> EmulationSessionStatus:
    return await session_service.get_status(session_id)


@router.post("/status/batch")
async def get_emulation_status_batch(
    request: EmulationStatusBatchRequest,
    session_service: FromDishka[EmulationSessionService],
) -> EmulationStatusBatchResponse:
    return await session_service.get_status_batch([str(session_id) for session_id in request.session_ids])


@router.get("/{session_id}/status/stream")
async def stream_emulation_status(
    session_id: str,
    request: Request,
    session_service: FromDishka[EmulationSessionService],
) -> StreamingResponse:
    initial_status = await session_service.get_status(session_id)

    return StreamingResponse(
        stream_status_events(
            initial_status=initial_status,
            get_status=lambda: session_service.get_status(session_id),
            is_disconnected=request.is_disconnected,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/stop")
async def stop_emulation(
    request: EmulationSessionActionRequest,
    session_service: FromDishka[EmulationSessionService],
) -> StopEmulationResponse:
    return await session_service.stop_session(str(request.session_id))


@router.post("/retry")
async def retry_emulation(
    request: EmulationSessionActionRequest,
    session_service: FromDishka[EmulationSessionService],
) -> StartEmulationResponse:
    return await session_service.retry_session(str(request.session_id))


@router.post("/resume")
async def resume_emulation(
    request: EmulationSessionActionRequest,
    session_service: FromDishka[EmulationSessionService],
) -> StartEmulationResponse:
    return await session_service.resume_session(str(request.session_id))


@router.delete("/history/{session_id}", status_code=204)
async def delete_emulation_history(
    session_id: str,
    session_service: FromDishka[EmulationSessionService],
) -> None:
    await session_service.delete_session(session_id)


@router.get("/{session_id}/captures")
async def get_emulation_captures(
    session_id: str,
    history_service: FromDishka[EmulationHistoryService],
    analysis_status: str | None = Query(None),
) -> EmulationCapturesResponse:
    return await history_service.get_session_captures(
        session_id=session_id,
        analysis_status=analysis_status,
    )


@public_router.get("/media/{media_path:path}")
async def get_emulation_media(
    media_path: str,
) -> FileResponse:
    resolved_path = resolve_media_path(media_path)
    return FileResponse(
        resolved_path,
        filename=resolved_path.name,
        content_disposition_type="inline",
    )
