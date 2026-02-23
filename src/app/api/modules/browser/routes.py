from fastapi import APIRouter
from taskiq.kicker import AsyncKicker

from app.api.modules.browser.schema import OpenSiteRequest, OpenSiteResponse
from app.tiq import broker

router = APIRouter()


@router.post("/open", response_model=OpenSiteResponse)
async def open_site(request: OpenSiteRequest) -> OpenSiteResponse:
    url = str(request.url)
    await AsyncKicker(broker=broker, task_name="open_site_task", labels={}).kiq(url)
    return OpenSiteResponse(status="ok", url=url)
