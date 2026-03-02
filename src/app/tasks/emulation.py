import logging
import time

from dishka import FromDishka
from dishka.integrations.taskiq import inject

from app.services.browser.provider import BrowserSessionProvider
from app.services.emulation import YouTubeEmulator
from app.services.emulation.core.session_store import EmulationSessionStore
from app.tiq import broker

logger = logging.getLogger(__name__)


@broker.task(task_name="emulation_task", timeout=28800)
@inject
async def emulation_task(
    session_id: str,
    duration_minutes: int,
    topics: list[str],
    session_provider: FromDishka[BrowserSessionProvider],
    session_store: FromDishka[EmulationSessionStore],
) -> dict:
    ctx = await session_provider.acquire_context()
    page = None
    try:
        page = await ctx.new_page()

        await session_store.update(session_id, status="running", started_at=time.time())

        emulator = YouTubeEmulator(
            page=page,
            topics=topics,
            duration_minutes=duration_minutes,
            session_store=session_store,
            session_id=session_id,
        )
        result = await emulator.run()

        await session_store.update(
            session_id,
            status="completed",
            finished_at=time.time(),
            bytes_downloaded=result.bytes_downloaded,
            topics_searched=result.topics_searched,
            videos_watched=result.videos_watched,
        )
        logger.info("Session %s completed: %s", session_id, result)
        return {"status": "completed", "session_id": session_id}

    except Exception as e:
        logger.exception("Session %s failed", session_id)
        await session_store.update(
            session_id,
            status="failed",
            finished_at=time.time(),
            error=str(e),
        )
        raise
    finally:
        if page:
            await page.close()
        await session_provider.release_context(ctx)
