import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx
from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright

from app.settings import AdsPowerConfig, PlaywrightConfig, ViewportConfig

from .context import ContextFactory
from .pool import BrowserPool
from .useragent import UserAgentProvider

logger = logging.getLogger(__name__)


class BrowserSessionProvider(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def acquire_context(self, profile_id: str | None = None) -> BrowserContext: ...
    async def release_context(self, ctx: BrowserContext) -> None: ...


class ChromiumSessionProvider:
    def __init__(
        self,
        playwright_config: PlaywrightConfig,
        viewport_config: ViewportConfig,
        user_agent_provider: UserAgentProvider,
    ) -> None:
        self._headless = playwright_config.headless
        self._browser_args = playwright_config.browser_args
        self._pool = BrowserPool(
            headless=self._headless,
            args=self._browser_args,
            config=playwright_config,
        )
        self._context_factory = ContextFactory(user_agent_provider, viewport_config)
        self._contexts: dict[BrowserContext, int] = {}
        self._task_index = 0

    async def start(self) -> None:
        await self._pool.start()

    async def stop(self) -> None:
        await self._pool.stop()

    async def acquire_context(self, profile_id: str | None = None) -> BrowserContext:
        browser, queue = self._pool.get_browser(self._task_index)
        self._task_index += 1
        slot = await queue.get()
        ctx, _ = await self._context_factory.create(browser)
        self._contexts[ctx] = slot
        return ctx

    async def release_context(self, ctx: BrowserContext) -> None:
        slot = self._contexts.pop(ctx)
        browser = ctx.browser
        await ctx.close()
        if browser:
            _, queue = self._pool.get_browser_by_instance(browser)
            queue.put_nowait(slot)


@dataclass(frozen=True)
class _AdsPowerProfileSession:
    profile_id: str
    browser: Browser
    context: BrowserContext


class AdsPowerSessionProvider:
    def __init__(self, config: AdsPowerConfig) -> None:
        self._base_url = config.base_url.rstrip("/")
        self._default_user_id = config.user_id.strip()
        self._api_key = config.api_key.strip() if config.api_key else None
        self._playwright: Playwright | None = None
        self._sessions: dict[str, _AdsPowerProfileSession] = {}
        self._profile_locks: dict[str, asyncio.Lock] = {}
        self._busy_profiles: set[str] = set()

    async def start(self) -> None:
        if self._playwright is None:
            self._playwright = await async_playwright().start()

    async def stop(self) -> None:
        for profile_id in list(self._sessions):
            await self._stop_profile_session(profile_id)
        self._busy_profiles.clear()

        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.debug("AdsPower provider stopped")

    async def acquire_context(self, profile_id: str | None = None) -> BrowserContext:
        resolved_profile_id = self._resolve_profile_id(profile_id)
        profile_lock = self._profile_locks.setdefault(resolved_profile_id, asyncio.Lock())
        async with profile_lock:
            if resolved_profile_id in self._busy_profiles:
                raise RuntimeError(f"AdsPower profile {resolved_profile_id} is already in use")

            session = self._sessions.get(resolved_profile_id)
            if session is None:
                session = await self._start_profile_session(resolved_profile_id)
                self._sessions[resolved_profile_id] = session

            await self._cleanup_context_pages(session.context, stage="acquire")
            self._busy_profiles.add(resolved_profile_id)
            return session.context

    async def release_context(self, ctx: BrowserContext) -> None:
        profile_id = self._find_profile_by_context(ctx)
        if profile_id is None:
            return
        profile_lock = self._profile_locks.setdefault(profile_id, asyncio.Lock())
        async with profile_lock:
            await self._cleanup_context_pages(ctx, stage="release")
            self._busy_profiles.discard(profile_id)
            await self._stop_profile_session(profile_id)

    async def _start_profile_session(self, profile_id: str) -> _AdsPowerProfileSession:
        if self._playwright is None:
            raise RuntimeError("AdsPower provider is not started")

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base_url}/api/v1/browser/start",
                params={"user_id": profile_id},
                headers=self._build_auth_headers(),
                timeout=30.0,
            )
            data = resp.json()

        if data.get("code") != 0:
            raise RuntimeError(
                f"AdsPower failed to start profile {profile_id}: {data.get('msg')}"
            )

        ws_endpoint = data.get("data", {}).get("ws", {}).get("puppeteer")
        if not isinstance(ws_endpoint, str) or not ws_endpoint:
            raise RuntimeError(f"AdsPower returned invalid ws endpoint for profile {profile_id}")
        ws_endpoint = self._normalize_ws_endpoint(ws_endpoint)
        browser = await self._playwright.chromium.connect_over_cdp(ws_endpoint)
        contexts = browser.contexts
        context = contexts[0] if contexts else await browser.new_context()
        logger.info("AdsPower profile %s started", profile_id)
        return _AdsPowerProfileSession(profile_id=profile_id, browser=browser, context=context)

    async def _stop_profile_session(self, profile_id: str) -> None:
        session = self._sessions.pop(profile_id, None)
        if session is None:
            return

        try:
            await session.browser.close()
        except Exception as exc:
            logger.warning("AdsPower browser close failed for profile %s: %s", profile_id, exc)

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self._base_url}/api/v1/browser/stop",
                    params={"user_id": profile_id},
                    headers=self._build_auth_headers(),
                    timeout=10.0,
                )
                data = resp.json()
                if data.get("code") != 0:
                    logger.warning(
                        "AdsPower failed to stop profile %s: %s",
                        profile_id,
                        data.get("msg"),
                    )
        except Exception as exc:
            logger.warning("AdsPower stop request failed for profile %s: %s", profile_id, exc)

        logger.info("AdsPower profile %s stopped", profile_id)

    def _build_auth_headers(self) -> dict[str, str] | None:
        if not self._api_key:
            return None
        return {"Authorization": f"Bearer {self._api_key}"}

    def _resolve_profile_id(self, profile_id: str | None) -> str:
        resolved = (profile_id or self._default_user_id).strip()
        if not resolved:
            raise RuntimeError("AdsPower profile id is not configured")
        return resolved

    def _find_profile_by_context(self, ctx: BrowserContext) -> str | None:
        for profile_id, session in self._sessions.items():
            if session.context is ctx:
                return profile_id
        return None

    def _normalize_ws_endpoint(self, ws_endpoint: str) -> str:
        base_host = urlsplit(self._base_url).hostname
        if not base_host:
            return ws_endpoint
        try:
            parsed = urlsplit(ws_endpoint)
        except Exception:
            return ws_endpoint
        ws_host = parsed.hostname
        if ws_host not in {"127.0.0.1", "localhost"}:
            return ws_endpoint
        if base_host in {"127.0.0.1", "localhost"}:
            return ws_endpoint
        if parsed.port:
            netloc = f"{base_host}:{parsed.port}"
        else:
            netloc = base_host
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))

    async def _cleanup_context_pages(self, ctx: BrowserContext, stage: str) -> None:
        pages = list(ctx.pages)
        closed = 0
        keep_page = None
        if stage == "acquire":
            for candidate in pages:
                if candidate.is_closed():
                    continue
                try:
                    candidate_url = candidate.url or ""
                except Exception:
                    candidate_url = ""
                if candidate_url.startswith("chrome-extension://"):
                    continue
                keep_page = candidate
                break

        for page in pages:
            if page.is_closed():
                continue
            try:
                page_url = page.url or ""
            except Exception:
                page_url = ""
            if page_url.startswith("chrome-extension://"):
                continue
            if keep_page is page:
                continue
            try:
                await page.close()
                closed += 1
            except Exception as exc:
                logger.debug(
                    "AdsPower context cleanup (%s) failed to close page '%s': %s",
                    stage,
                    page_url,
                    exc,
                )
        if closed:
            logger.debug("AdsPower context cleanup (%s): closed %d pages", stage, closed)
