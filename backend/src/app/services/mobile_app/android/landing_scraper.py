from __future__ import annotations

"""Background Playwright-based landing page scraper for Android ad sessions.

After a CTA click extracts a landing URL on the emulator, this module opens
that URL in a headless Playwright browser on the host machine and captures:
  - A full-page screenshot
  - The final URL after all redirects
  - Page title and meta description
  - First-viewport HTML snippet (for quick text analysis)

The scraper works like AndroidAdAnalysisCoordinator: submit() fires an async
task, drain() waits for all pending tasks at session end.

Usage in runner:
    scraper = AndroidLandingPageScraper(config.storage, proxy_url=emulator_http_proxy)
    await scraper.start()
    ...
    # after ad_analysis.submit(built_ad):
    scraper.submit(built_ad)
    ...
    # at session teardown:
    await scraper.drain(timeout_seconds=60)
    await scraper.stop()
"""

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from app.api.modules.emulation.models import LandingStatus
from app.services.emulation.browser.ads.capture_utils import (
    ASSET_CONTENT_TYPES,
    IMAGE_PREFIX,
    asset_filename,
)
from app.services.emulation.config import (
    AD_CAPTURE_MAX_ASSET_SIZE_BYTES,
    AD_CAPTURE_MAX_TOTAL_ASSETS,
)
from app.settings import StorageConfig

_LANDING_SUBDIR = "android_landing"
_GOTO_TIMEOUT_MS = 30_000
_NETWORKIDLE_TIMEOUT_MS = 12_000
_SCREENSHOT_TIMEOUT_MS = 15_000
_META_DESC_JS = (
    "document.querySelector('meta[name=\"description\"]')?.content"
    " || document.querySelector('meta[property=\"og:description\"]')?.content"
    " || ''"
)
_VIEWPORT = {"width": 1440, "height": 900}
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_EXTRA_HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
}
_SKIP_LANDING_HOSTS = frozenset({
    # Only skip YouTube itself — for google/googleadservices/doubleclick redirects
    # we WANT Playwright to follow aclk to resolve the real advertiser destination.
    "www.youtube.com", "youtube.com", "m.youtube.com", "youtu.be",
    "consent.youtube.com", "consent.google.com", "accounts.google.com",
    "play.google.com", "support.google.com",
})


@dataclass
class LandingCaptureResult:
    ad_id: str
    original_url: str
    final_url: str | None = None
    title: str | None = None
    meta_description: str | None = None
    screenshot_path: str | None = None
    landing_dir: str | None = None
    assets_count: int = 0
    error: str | None = None


def _normalize_url(raw: str) -> str | None:
    """Ensure URL has a scheme. Returns None if unparseable."""
    raw = raw.strip()
    if not raw:
        return None
    if not re.match(r"^https?://", raw, re.IGNORECASE):
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
        if not parts.netloc:
            return None
        return urlunsplit(parts)
    except Exception:
        return None


class AndroidLandingPageScraper:
    """Fire-and-forget landing page scraper backed by a single Playwright browser."""

    def __init__(
        self,
        storage_config: StorageConfig,
        *,
        proxy_url: str | None = None,
        on_result: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self._base_dir = storage_config.base_path / _LANDING_SUBDIR
        # Caller passes a host-reachable HTTP proxy URL (bridge exposes pproxy on 127.0.0.1).
        # As a safety, still remap emulator alias 10.0.2.2 -> 127.0.0.1 for host-side use.
        self._proxy_url = _remap_emulator_proxy(proxy_url)
        self._on_result = on_result
        self._browser: Any = None
        self._playwright: Any = None
        self._tasks: set[asyncio.Task[LandingCaptureResult]] = set()
        self._results: list[LandingCaptureResult] = []

    async def start(self) -> None:
        """Launch headless Playwright browser. Call once before submit()."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            print("[landing-scraper] playwright not installed — scraper disabled", flush=True)
            return
        try:
            self._playwright = await async_playwright().start()
            launch_kwargs: dict[str, Any] = {"headless": True}
            if self._proxy_url:
                launch_kwargs["proxy"] = {"server": self._proxy_url}
            self._browser = await self._playwright.chromium.launch(**launch_kwargs)
            print(f"[landing-scraper] started (proxy={self._proxy_url})", flush=True)
        except Exception as exc:
            print(f"[landing-scraper] start failed: {type(exc).__name__}: {exc}", flush=True)
            self._browser = None

    async def stop(self) -> None:
        """Close browser and Playwright instance."""
        if self._browser is not None:
            with contextlib.suppress(Exception):
                await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            with contextlib.suppress(Exception):
                await self._playwright.stop()
            self._playwright = None

    def submit(self, ad_record: dict[str, Any]) -> None:
        """Queue a landing page scrape for the given ad record (mutates it in place)."""
        if self._browser is None:
            print("[landing-scraper] submit skipped: browser not started", flush=True)
            return
        landing_url = ad_record.get("landing_url")
        if not landing_url:
            cap = ad_record.get("capture")
            if isinstance(cap, dict):
                landing_url = cap.get("landing_url")
        if not landing_url:
            return
        normalized = _normalize_url(str(landing_url))
        if not normalized:
            return
        try:
            host = urlsplit(normalized).netloc.lower()
        except Exception:
            host = ""
        if host in _SKIP_LANDING_HOSTS:
            print(f"[landing-scraper] skip host: {host}", flush=True)
            return
        print(f"[landing-scraper] submit: {host}", flush=True)
        ad_id = str(ad_record.get("capture_id") or id(ad_record))
        task = asyncio.create_task(self._scrape(ad_id=ad_id, url=normalized, ad_record=ad_record))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self, timeout_seconds: float = 60) -> list[LandingCaptureResult]:
        """Wait for all pending scrape tasks to finish. Returns collected results."""
        if not self._tasks:
            return list(self._results)
        pending = set(self._tasks)
        done, _ = await asyncio.wait(pending, timeout=timeout_seconds)
        for task in done:
            with contextlib.suppress(Exception):
                result = await task
                self._results.append(result)
        return list(self._results)

    async def _scrape(
        self,
        *,
        ad_id: str,
        url: str,
        ad_record: dict[str, Any],
    ) -> LandingCaptureResult:
        result = LandingCaptureResult(ad_id=ad_id, original_url=url)
        capture_dir = self._base_dir / ad_id
        assets_dir = capture_dir / "assets"
        capture_dir.mkdir(parents=True, exist_ok=True)
        assets_dir.mkdir(exist_ok=True)

        collected: list[tuple[str, bytes]] = []
        asset_count = 0

        async def on_response(response: Any) -> None:
            nonlocal asset_count
            if asset_count >= AD_CAPTURE_MAX_TOTAL_ASSETS:
                return
            content_type = response.headers.get("content-type", "")
            ct_lower = content_type.split(";")[0].strip().lower()
            if ct_lower not in ASSET_CONTENT_TYPES and not ct_lower.startswith(IMAGE_PREFIX):
                return
            try:
                content_length = int(response.headers.get("content-length", "0") or 0)
                if content_length > AD_CAPTURE_MAX_ASSET_SIZE_BYTES:
                    return
                body = await response.body()
                if len(body) > AD_CAPTURE_MAX_ASSET_SIZE_BYTES:
                    return
                filename = asset_filename(response.url, ct_lower)
                collected.append((filename, body))
                asset_count += 1
            except Exception:
                pass

        try:
            context = await self._browser.new_context(
                viewport=_VIEWPORT,
                user_agent=_USER_AGENT,
                locale="en-US",
                extra_http_headers=_EXTRA_HEADERS,
                ignore_https_errors=True,
            )
            try:
                page = await context.new_page()
                page.on("response", on_response)
                response = None
                for wait_until in ("networkidle", "domcontentloaded", "load", "commit"):
                    try:
                        response = await page.goto(
                            url, timeout=_GOTO_TIMEOUT_MS, wait_until=wait_until,
                        )
                        break
                    except Exception:
                        continue
                with contextlib.suppress(Exception):
                    await page.wait_for_load_state("networkidle", timeout=_NETWORKIDLE_TIMEOUT_MS)

                result.final_url = page.url
                result.title = await page.title()
                result.meta_description = await page.evaluate(_META_DESC_JS)

                html = await page.content()
                (capture_dir / "index.html").write_text(html, encoding="utf-8")

                for filename, body in collected:
                    with contextlib.suppress(Exception):
                        (assets_dir / filename).write_bytes(body)
                result.assets_count = len(collected)

                screenshot_path = capture_dir / "screenshot.png"
                await page.screenshot(
                    path=str(screenshot_path),
                    full_page=True,
                    timeout=_SCREENSHOT_TIMEOUT_MS,
                )
                try:
                    result.screenshot_path = str(screenshot_path.relative_to(self._base_dir.parent))
                except ValueError:
                    result.screenshot_path = str(screenshot_path)
                try:
                    result.landing_dir = str(capture_dir.relative_to(self._base_dir.parent))
                except ValueError:
                    result.landing_dir = str(capture_dir)

                meta = {
                    "ad_id": ad_id,
                    "original_url": url,
                    "final_url": result.final_url,
                    "title": result.title,
                    "meta_description": result.meta_description,
                    "http_status": response.status if response else None,
                    "assets_count": result.assets_count,
                }
                (capture_dir / "meta.json").write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
                )

                _patch_ad_record(ad_record, result)
                if self._on_result is not None:
                    with contextlib.suppress(Exception):
                        await self._on_result(ad_record)

            finally:
                with contextlib.suppress(Exception):
                    await context.close()

        except Exception as exc:
            result.error = str(exc)
            _patch_ad_record(ad_record, result)
            if self._on_result is not None:
                with contextlib.suppress(Exception):
                    await self._on_result(ad_record)

        return result


def _patch_ad_record(ad_record: dict[str, Any], result: LandingCaptureResult) -> None:
    """Write scrape results back into the ad record dict (mutates in place)."""
    cap = ad_record.get("capture")
    target = cap if isinstance(cap, dict) else ad_record
    resolved_url = result.final_url or result.original_url
    target["landing_scrape_url"] = resolved_url
    target["landing_scrape_title"] = result.title
    target["landing_scrape_screenshot"] = result.screenshot_path
    target["landing_scrape_dir"] = result.landing_dir
    target["landing_assets_count"] = result.assets_count
    if resolved_url:
        target["landing_url"] = resolved_url
        if not ad_record.get("landing_url"):
            ad_record["landing_url"] = resolved_url
    if result.landing_dir:
        target["landing_dir"] = result.landing_dir
        target["landing_status"] = LandingStatus.COMPLETED
        if not ad_record.get("landing_dir"):
            ad_record["landing_dir"] = result.landing_dir
    elif result.error:
        target["landing_status"] = LandingStatus.FAILED
    if result.error:
        target["landing_scrape_error"] = result.error


def _remap_emulator_proxy(proxy_url: str | None) -> str | None:
    """Replace Android emulator host alias 10.0.2.2 with 127.0.0.1.

    The emulator uses 10.0.2.2 to reach the host machine, but Playwright
    runs on the host itself where the proxy listens on localhost.
    """
    if not proxy_url:
        return None
    return proxy_url.replace("10.0.2.2", "127.0.0.1")
