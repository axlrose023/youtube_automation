from __future__ import annotations

import asyncio
import contextlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.api.modules.emulation.models import AnalysisStatus
from app.services.emulation.ads.analysis.prompt import ANALYSIS_PROMPT
from app.services.emulation.ads.analysis.parser import parse_result
from app.services.emulation.ads.analysis.prompt import build_text_prompt
from app.services.emulation.ads.analysis.sampler import AdAnalysisVideoSampler
from app.settings import GeminiConfig, StorageConfig
from app.services.mobile_app.android.tooling import require_tool_path


@dataclass(frozen=True)
class AndroidAdAnalysisResult:
    status: str
    summary: dict[str, Any] | None = None
    raw_response: str | None = None
    error: str | None = None
    error_stage: str | None = None


@dataclass(frozen=True)
class AndroidAdAnalysisSnapshot:
    submitted: int
    completed: int
    terminal: int
    pending: int


class _AdRecordAdapter:
    def __init__(self, record: dict[str, Any]) -> None:
        capture = record.get("capture")
        self.headline_text = record.get("headline_text")
        self.sponsor_label = record.get("sponsor_label")
        self.advertiser_domain = record.get("advertiser_domain")
        self.display_url = record.get("display_url")
        self.landing_url = None
        self.landing_scrape_title = None
        self.landing_scrape_url = None
        self.cta_href = record.get("cta_href")
        self.cta_text = record.get("cta_text")
        self.full_visible_text = record.get("full_visible_text")
        self.full_text = record.get("full_text")
        self.watched_seconds = _coerce_float(record.get("watched_seconds"))
        self.ad_duration_seconds = _coerce_float(record.get("ad_duration_seconds"))
        self.first_ad_offset_seconds = _coerce_float(record.get("first_ad_offset_seconds"))
        self.last_ad_offset_seconds = _coerce_float(record.get("last_ad_offset_seconds"))
        if isinstance(capture, dict):
            self.landing_scrape_title = capture.get("landing_scrape_title")
            self.landing_scrape_url = capture.get("landing_scrape_url")
            self.landing_url = capture.get("landing_url") or self.landing_scrape_url
            self.cta_href = self.cta_href or capture.get("cta_href")
            self.video_file = capture.get("video_file")
            self.recorded_video_duration_seconds = _coerce_float(
                capture.get("recorded_video_duration_seconds")
            )
        else:
            self.video_file = None
            self.recorded_video_duration_seconds = None


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _build_ad_video_focus_window(
    *,
    first_ad_offset_seconds: float | None,
    watched_seconds: float | None,
    ad_duration_seconds: float | None,
    recorded_video_duration_seconds: float | None,
) -> tuple[float, float] | None:
    if first_ad_offset_seconds is None:
        return None

    # Keep a compact window for analysis/UI. The raw source recording remains
    # available for debugging; the focused clip should not include minutes of
    # regular video or neighbouring ads.
    if first_ad_offset_seconds >= 5.0:
        start_seconds = first_ad_offset_seconds
    else:
        start_seconds = max(0.0, first_ad_offset_seconds - 2.0)

    if ad_duration_seconds and ad_duration_seconds > 0:
        window_seconds = min(ad_duration_seconds + 4.0, 30.0)
    else:
        observed_seconds = max(watched_seconds or 0.0, 12.0)
        window_seconds = min(observed_seconds + 6.0, 30.0)

    if recorded_video_duration_seconds is not None:
        remaining_seconds = max(0.0, recorded_video_duration_seconds - start_seconds)
        if remaining_seconds <= 0.0:
            return None
        window_seconds = min(window_seconds, remaining_seconds)

    if window_seconds <= 0.5:
        return None
    return start_seconds, window_seconds


class AndroidAdAnalyzer:
    def __init__(self, gemini_config: GeminiConfig, storage_config: StorageConfig) -> None:
        self._enabled = bool(gemini_config.api_key)
        self._storage_base = storage_config.base_path
        self._video_sampler = AdAnalysisVideoSampler()
        self._gemini = None
        self._guardrails = None
        if self._enabled:
            try:
                from app.clients.gemini import GeminiClient
                from app.services.emulation.ads.analysis.guardrails import AdAnalysisGuardrails
            except ModuleNotFoundError:
                self._enabled = False
            else:
                self._gemini = GeminiClient(
                    api_key=gemini_config.api_key,
                    model=gemini_config.model,
                )
                self._guardrails = AdAnalysisGuardrails(self._gemini)

    async def analyze(self, record: dict[str, Any]) -> AndroidAdAnalysisResult:
        if not self._enabled or self._gemini is None or self._guardrails is None:
            return AndroidAdAnalysisResult(status=AnalysisStatus.PENDING, error="gemini_disabled")

        capture = _AdRecordAdapter(record)
        try:
            video_error = None
            video_unclear_result: AndroidAdAnalysisResult | None = None
            try:
                video_result = await self._analyze_from_video(capture)
            except Exception as exc:
                video_result = None
                video_error = str(exc)
            if video_result is not None:
                if video_result.status == AnalysisStatus.COMPLETED:
                    return self._with_error(video_result, video_error)
                video_unclear_result = video_result

            prompt = build_text_prompt(capture)
            if prompt is None:
                if video_unclear_result is not None:
                    return self._with_error(video_unclear_result, video_error)
                return self._resolve_unclear_as_not_relevant(
                    primary=video_unclear_result,
                    fallback_reason=(
                        "The ad could not be classified from the video and there is no "
                        "usable text metadata, so it was conservatively marked not relevant."
                    ),
                    error=video_error,
                )
            raw = await self._gemini.generate_from_text(prompt)
            result, data = parse_result(raw)
            result, data = await self._guardrails.apply(
                capture=capture,
                result=result,
                data=data,
            )
        except Exception as exc:
            return AndroidAdAnalysisResult(
                status=AnalysisStatus.FAILED,
                error=str(exc),
                error_stage="text_fallback_or_guardrails",
            )

        if result == "relevant":
            return AndroidAdAnalysisResult(
                status=AnalysisStatus.COMPLETED,
                summary=data,
                raw_response=raw,
                error=video_error,
            )
        if result == "not_relevant":
            return AndroidAdAnalysisResult(
                status=AnalysisStatus.NOT_RELEVANT,
                summary=data,
                raw_response=raw,
                error=video_error,
            )
        return self._resolve_unclear_as_not_relevant(
            primary=AndroidAdAnalysisResult(
                status=AnalysisStatus.SKIPPED,
                summary=data,
                raw_response=raw,
                error=video_error,
            ),
            fallback_reason=(
                "The model returned an unclear classification after text fallback, "
                "so the ad was conservatively marked not relevant."
            ),
            raw_response=raw,
        )

    async def _analyze_from_video(self, capture: _AdRecordAdapter) -> AndroidAdAnalysisResult | None:
        if not capture.video_file:
            return None
        video_path = self._storage_base / str(capture.video_file)
        if not video_path.exists():
            return None
        if not self._is_playable_video(video_path):
            raise RuntimeError(f"Android ad video is not a valid playable file: {video_path}")

        prepared_video = await self._prepare_video_for_analysis(capture, video_path)
        try:
            raw = await self._gemini.generate_from_video(prepared_video.path, ANALYSIS_PROMPT)
            result, data = parse_result(raw)
            result, data = await self._guardrails.apply(
                capture=capture,
                result=result,
                data=data,
            )
            if result == "relevant":
                return AndroidAdAnalysisResult(
                    status=AnalysisStatus.COMPLETED,
                    summary=data,
                    raw_response=raw,
                )
            if result == "not_relevant":
                return AndroidAdAnalysisResult(
                    status=AnalysisStatus.NOT_RELEVANT,
                    summary=data,
                    raw_response=raw,
                )
            return AndroidAdAnalysisResult(
                status=AnalysisStatus.SKIPPED,
                summary=data,
                raw_response=raw,
            )
        finally:
            await prepared_video.cleanup()

    async def _prepare_video_for_analysis(
        self,
        capture: _AdRecordAdapter,
        video_path: Path,
    ):
        focus_window = _build_ad_video_focus_window(
            first_ad_offset_seconds=capture.first_ad_offset_seconds,
            watched_seconds=capture.watched_seconds,
            ad_duration_seconds=capture.ad_duration_seconds,
            recorded_video_duration_seconds=capture.recorded_video_duration_seconds,
        )
        if focus_window is not None:
            start_seconds, window_seconds = focus_window
            return await self._video_sampler.prepare_focus_window(
                video_path,
                start_seconds=start_seconds,
                max_duration_seconds=window_seconds,
            )
        return await self._video_sampler.prepare(video_path)

    @staticmethod
    def _with_error(
        result: AndroidAdAnalysisResult,
        error: str | None,
    ) -> AndroidAdAnalysisResult:
        if not error:
            return result
        if result.error:
            return result
        return AndroidAdAnalysisResult(
            status=result.status,
            summary=result.summary,
            raw_response=result.raw_response,
            error=error,
            error_stage="video" if error else result.error_stage,
        )

    @staticmethod
    def _resolve_unclear_as_not_relevant(
        *,
        primary: AndroidAdAnalysisResult | None,
        fallback_reason: str,
        error: str | None = None,
        raw_response: str | None = None,
    ) -> AndroidAdAnalysisResult:
        primary_summary = primary.summary if primary is not None else None
        original_reason = None
        if isinstance(primary_summary, dict):
            original_reason = primary_summary.get("reason")
        summary = {
            "result": "not_relevant",
            "reason": fallback_reason,
            "fallback_policy": "conservative_unclear_to_not_relevant",
        }
        if original_reason:
            summary["original_reason"] = str(original_reason)
        return AndroidAdAnalysisResult(
            status=AnalysisStatus.NOT_RELEVANT,
            summary=summary,
            raw_response=raw_response or (primary.raw_response if primary is not None else None),
            error=error or (primary.error if primary is not None else None),
            error_stage=(
                "video"
                if error
                else (primary.error_stage if primary is not None else None)
            ),
        )

    @staticmethod
    def _is_playable_video(video_path: Path) -> bool:
        ffprobe_bin = require_tool_path("ffprobe")
        completed = subprocess.run(
            [
                ffprobe_bin,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nk=1:nw=1",
                str(video_path),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if completed.returncode != 0:
            return False
        try:
            return float((completed.stdout or "").strip()) > 0
        except ValueError:
            return False

    @staticmethod
    def summary_json(result: AndroidAdAnalysisResult) -> str | None:
        if result.summary is None:
            return None
        return json.dumps(result.summary, ensure_ascii=False)


class AndroidAdAnalysisCoordinator:
    def __init__(self, gemini_config: GeminiConfig, storage_config: StorageConfig) -> None:
        self._analyzer = AndroidAdAnalyzer(gemini_config, storage_config)
        self._tasks: set[asyncio.Task[None]] = set()
        self._submitted = 0
        self._completed = 0
        self._terminal = 0

    def submit(self, record: dict[str, Any]) -> None:
        self._submitted += 1
        task = asyncio.create_task(self._run(record))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self, timeout_seconds: float | None = None) -> AndroidAdAnalysisSnapshot:
        if not self._tasks:
            return self.snapshot()
        pending = set(self._tasks)
        done, pending = await asyncio.wait(pending, timeout=timeout_seconds)
        for task in done:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        return AndroidAdAnalysisSnapshot(
            submitted=self._submitted,
            completed=self._completed,
            terminal=self._terminal,
            pending=len(pending),
        )

    def snapshot(self) -> AndroidAdAnalysisSnapshot:
        return AndroidAdAnalysisSnapshot(
            submitted=self._submitted,
            completed=self._completed,
            terminal=self._terminal,
            pending=len(self._tasks),
        )

    async def _run(self, record: dict[str, Any]) -> None:
        analysis_result = await self._analyzer.analyze(record)
        capture_payload = record.get("capture")
        if isinstance(capture_payload, dict):
            capture_payload["analysis_status"] = analysis_result.status
            capture_payload["analysis_summary"] = analysis_result.summary
            if analysis_result.raw_response:
                capture_payload["analysis_raw_response"] = analysis_result.raw_response
            if analysis_result.error:
                capture_payload["analysis_error"] = analysis_result.error
            if analysis_result.error_stage:
                capture_payload["analysis_error_stage"] = analysis_result.error_stage
        self._completed += 1
        if analysis_result.status in {
            AnalysisStatus.COMPLETED,
            AnalysisStatus.NOT_RELEVANT,
            AnalysisStatus.SKIPPED,
            AnalysisStatus.FAILED,
        }:
            self._terminal += 1
