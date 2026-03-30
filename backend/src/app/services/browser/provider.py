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

_ADSPOWER_START_RETRY_DELAYS_SECONDS = (2, 5, 10)
_TRANSIENT_START_ERROR_TOKENS = (
    "enotfound",
    "getaddrinfo",
    "econnreset",
    "timed out",
    "timeout",
    "temporary failure",
    "temporarily unavailable",
    "network error",
    "connection reset",
    "connection refused",
    "connecterror",
    "api-global.adspower.net",
)
_NON_RETRYABLE_START_ERROR_TOKENS = (
    "already in use",
    "invalid ws endpoint",
    "not configured",
    "not found",
    "unauthorized",
    "forbidden",
)


class _AdsPowerTransientStartError(RuntimeError):
    pass


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

        attempts_total = len(_ADSPOWER_START_RETRY_DELAYS_SECONDS) + 1
        last_error: Exception | None = None

        for attempt in range(1, attempts_total + 1):
            try:
                session = await self._start_profile_session_once(profile_id)
                if attempt > 1:
                    logger.info(
                        "AdsPower profile %s started after retry %d/%d",
                        profile_id,
                        attempt,
                        attempts_total,
                    )
                return session
            except Exception as exc:
                last_error = exc
                if attempt >= attempts_total or not self._is_transient_start_error(exc):
                    raise

                delay = _ADSPOWER_START_RETRY_DELAYS_SECONDS[attempt - 1]
                logger.warning(
                    "AdsPower transient start failure for profile %s (attempt %d/%d): %s; retrying in %ss",
                    profile_id,
                    attempt,
                    attempts_total,
                    exc,
                    delay,
                )
                await self._best_effort_stop_profile(profile_id)
                await asyncio.sleep(delay)

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"AdsPower failed to start profile {profile_id}")

    async def _start_profile_session_once(self, profile_id: str) -> _AdsPowerProfileSession:
        if self._playwright is None:
            raise RuntimeError("AdsPower provider is not started")

        data = await self._request_profile_start(profile_id)
        if data.get("code") != 0:
            message = f"AdsPower failed to start profile {profile_id}: {data.get('msg')}"
            if self._looks_like_transient_start_message(message):
                raise _AdsPowerTransientStartError(message)
            raise RuntimeError(message)

        ws_endpoint = data.get("data", {}).get("ws", {}).get("puppeteer")
        if not isinstance(ws_endpoint, str) or not ws_endpoint:
            raise RuntimeError(f"AdsPower returned invalid ws endpoint for profile {profile_id}")

        browser: Browser | None = None
        try:
            ws_endpoint = self._normalize_ws_endpoint(ws_endpoint)
            browser = await self._playwright.chromium.connect_over_cdp(ws_endpoint)
            contexts = browser.contexts
            context = contexts[0] if contexts else await browser.new_context()
            logger.info("AdsPower profile %s started", profile_id)
            return _AdsPowerProfileSession(profile_id=profile_id, browser=browser, context=context)
        except Exception as exc:
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass
            if self._is_transient_start_error(exc):
                raise _AdsPowerTransientStartError(
                    f"AdsPower failed to connect profile {profile_id}: {exc}"
                ) from exc
            raise

    async def _stop_profile_session(self, profile_id: str) -> None:
        session = self._sessions.pop(profile_id, None)
        if session is None:
            return

        try:
            await session.browser.close()
        except Exception as exc:
            logger.warning("AdsPower browser close failed for profile %s: %s", profile_id, exc)

        try:
            data = await self._request_profile_stop(profile_id)
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

    async def _request_profile_start(self, profile_id: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base_url}/api/v1/browser/start",
                params={"user_id": profile_id},
                headers=self._build_auth_headers(),
                timeout=30.0,
            )
            if resp.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"AdsPower start returned HTTP {resp.status_code}",
                    request=resp.request,
                    response=resp,
                )
            resp.raise_for_status()
            return resp.json()

    async def _request_profile_stop(self, profile_id: str) -> dict:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base_url}/api/v1/browser/stop",
                params={"user_id": profile_id},
                headers=self._build_auth_headers(),
                timeout=10.0,
            )
            if resp.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"AdsPower stop returned HTTP {resp.status_code}",
                    request=resp.request,
                    response=resp,
                )
            resp.raise_for_status()
            return resp.json()

    async def _best_effort_stop_profile(self, profile_id: str) -> None:
        try:
            await self._request_profile_stop(profile_id)
        except Exception as exc:
            logger.debug("AdsPower best-effort stop failed for profile %s: %s", profile_id, exc)

    def _is_transient_start_error(self, exc: Exception) -> bool:
        if isinstance(exc, _AdsPowerTransientStartError):
            return True
        if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError)):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            response = exc.response
            return response is not None and response.status_code >= 500

        message = str(exc).lower()
        if any(token in message for token in _NON_RETRYABLE_START_ERROR_TOKENS):
            return False
        return self._looks_like_transient_start_message(message)

    def _looks_like_transient_start_message(self, message: str) -> bool:
        normalized = message.lower()
        return any(token in normalized for token in _TRANSIENT_START_ERROR_TOKENS)
