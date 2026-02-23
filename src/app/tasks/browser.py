import logging

from dishka import FromDishka
from dishka.integrations.taskiq import inject

from app.api.modules.browser.service import BrowserService
from app.tiq import broker

logger = logging.getLogger(__name__)


@broker.task(task_name="open_site_task")
@inject
async def open_site_task(
    url: str,
    browser_service: FromDishka[BrowserService],
) -> dict:
    await browser_service.open_site(url)
    logger.info("Task open_site completed for %s", url)
    return {"status": "ok", "url": url}
