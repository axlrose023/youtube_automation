from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path

from app.api.modules.emulation.models import (
    ANALYSIS_TERMINAL_STATUSES,
    AdCapture,
    AnalysisStatus,
    PostProcessingStatus,
    VideoStatus,
)
from app.clients.gemini import GeminiClient
from app.database.uow import UnitOfWork
from app.services.emulation.media_storage import MediaStorage
from app.services.emulation.ads.analysis.guardrails import AdAnalysisGuardrails
from app.services.emulation.ads.analysis.parser import parse_result
from app.services.emulation.ads.analysis.prompt import ANALYSIS_PROMPT, build_text_prompt
from app.services.emulation.ads.analysis.sampler import AdAnalysisVideoSampler

logger = logging.getLogger(__name__)

_MAX_VIDEO_SIZE_MB = 20


class AdAnalysisService:
    def __init__(
        self, gemini: GeminiClient, uow: UnitOfWork, base_path: Path,
        storage: MediaStorage, video_sampler: AdAnalysisVideoSampler,
    ) -> None:
        self._gemini = gemini
        self._uow = uow
        self._base_path = base_path
        self._storage = storage
        self._video_sampler = video_sampler
        self._guardrails = AdAnalysisGuardrails(gemini)

    async def get_session_analysis_workload(self, session_id: str) -> int:
        captures = await self._uow.ad_captures.get_by_session(session_id)
        return len(
            [
                c
                for c in captures
                if c.analysis_status == AnalysisStatus.PENDING
                and self._is_capture_analyzable(c)
            ]
        )

    async def summarize_session_analysis(
        self,
        session_id: str,
    ) -> tuple[PostProcessingStatus | None, int, int]:
        captures = await self._uow.ad_captures.get_by_session(session_id)
        analyzable = [
            c
            for c in captures
            if self._is_capture_analyzable(c)
        ]
        total = len(analyzable)
        if total == 0:
            return None, 0, 0

        done = sum(
            1
            for capture in analyzable
            if str(capture.analysis_status or "").lower() in ANALYSIS_TERMINAL_STATUSES
        )
        failed = sum(
            1
            for capture in analyzable
            if str(capture.analysis_status or "").lower() == AnalysisStatus.FAILED
        )

        if done < total:
            status = PostProcessingStatus.RUNNING
        elif failed > 0:
            status = PostProcessingStatus.FAILED
        else:
            status = PostProcessingStatus.COMPLETED
        return status, done, total

    async def build_live_capture_analysis_state(
        self,
        session_id: str,
    ) -> dict[int, dict[str, object]]:
        captures = await self._uow.ad_captures.get_by_session(session_id)
        updates: dict[int, dict[str, object]] = {}
        for capture in captures:
            ad_position = int(capture.ad_position or 0)
            if ad_position <= 0:
                continue

            analysis_summary = None
            if capture.analysis_summary:
                try:
                    analysis_summary = json.loads(capture.analysis_summary)
                except (json.JSONDecodeError, TypeError):
                    analysis_summary = None

            updates[ad_position] = {
                "video_src_url": capture.video_src_url,
                "video_status": str(capture.video_status),
                "video_file": capture.video_file,
                "landing_url": capture.landing_url,
                "landing_status": str(capture.landing_status),
                "landing_dir": capture.landing_dir,
                "screenshot_paths": [
                    {"offset_ms": screenshot.offset_ms, "file_path": screenshot.file_path}
                    for screenshot in sorted(capture.screenshots, key=lambda item: item.offset_ms)
                ],
                "analysis_status": str(capture.analysis_status or AnalysisStatus.PENDING),
                "analysis_summary": analysis_summary,
            }
        return updates

    async def analyze_session_captures(
        self,
        session_id: str,
    ) -> tuple[PostProcessingStatus | None, int, int]:
        captures = await self._uow.ad_captures.get_by_session(session_id)
        pending = [
            c for c in captures
            if c.analysis_status == AnalysisStatus.PENDING
            and self._is_capture_analyzable(c)
        ]
        if not pending:
            return None, 0, 0

        video_refcounts = Counter(c.video_file for c in captures if c.video_file)
        dirs_to_cleanup: list[str] = []

        for capture in pending:
            cleanup_dir = await self._analyze_one(session_id, capture, video_refcounts)
            if cleanup_dir:
                dirs_to_cleanup.append(cleanup_dir)

        try:
            await self._uow.commit()
        except Exception:
            logger.exception("Session %s: failed to commit analysis results", session_id)
            await self._uow.rollback()
            return PostProcessingStatus.FAILED, 0, len(pending)

        for rel_dir in dirs_to_cleanup:
            await self._storage.remove_capture_dir(rel_dir)

        done = sum(
            1 for capture in pending if str(capture.analysis_status or "").lower() in ANALYSIS_TERMINAL_STATUSES
        )
        failed = sum(
            1 for capture in pending if str(capture.analysis_status or "").lower() == AnalysisStatus.FAILED
        )
        final_status = PostProcessingStatus.FAILED if failed > 0 else PostProcessingStatus.COMPLETED
        return final_status, done, len(pending)

    async def _analyze_one(
        self, session_id: str, capture: AdCapture, video_refcounts: Counter[str],
    ) -> str | None:
        if not self._requires_video_analysis(capture):
            return await self._analyze_from_text(
                session_id=session_id,
                capture=capture,
                video_refcounts=video_refcounts,
            )

        video_path = self._base_path / str(capture.video_file)
        prepared_video = None
        try:
            if not video_path.exists():
                logger.warning(
                    "Session %s: video missing for capture %s",
                    session_id, capture.id,
                )
                await self._uow.ad_captures.update_analysis(capture.id, AnalysisStatus.FAILED)
                return None

            prepared_video = await self._video_sampler.prepare(video_path)
            if prepared_video.sampled:
                logger.info(
                    "Session %s: capture %s using sampled analysis clip %.1fs -> %s",
                    session_id,
                    capture.id,
                    prepared_video.duration_seconds or 0.0,
                    prepared_video.path.name,
                )

            size_mb = prepared_video.path.stat().st_size / (1024 * 1024)
            if size_mb > _MAX_VIDEO_SIZE_MB:
                logger.warning(
                    "Session %s: capture %s video too large (%.1fMB), trying text fallback",
                    session_id, capture.id, size_mb,
                )
                return await self._analyze_from_text(
                    session_id=session_id,
                    capture=capture,
                    video_refcounts=video_refcounts,
                )

            raw = await self._gemini.generate_from_video(prepared_video.path, ANALYSIS_PROMPT)
            return await self._apply_analysis_result(
                session_id=session_id,
                capture=capture,
                raw_response=raw,
                video_refcounts=video_refcounts,
            )

        except Exception:
            logger.exception(
                "Session %s: failed to analyze capture %s",
                session_id, capture.id,
            )
            return await self._handle_analysis_failure(
                session_id=session_id,
                capture=capture,
                video_refcounts=video_refcounts,
            )
        finally:
            if prepared_video is not None:
                await prepared_video.cleanup()

    async def _handle_analysis_failure(
        self,
        *,
        session_id: str,
        capture: AdCapture,
        video_refcounts: Counter[str],
    ) -> str | None:
        try:
            return await self._analyze_from_text(
                session_id=session_id,
                capture=capture,
                video_refcounts=video_refcounts,
            )
        except Exception:
            logger.exception(
                "Session %s: text fallback analysis failed for capture %s",
                session_id,
                capture.id,
            )
            try:
                await self._uow.ad_captures.update_analysis(capture.id, AnalysisStatus.FAILED)
            except Exception:
                logger.exception(
                    "Session %s: failed to mark capture %s as failed",
                    session_id,
                    capture.id,
                )
            return None

    async def _analyze_from_text(
        self,
        *,
        session_id: str,
        capture: AdCapture,
        video_refcounts: Counter[str],
    ) -> str | None:
        prompt = build_text_prompt(capture)
        if prompt is None:
            await self._uow.ad_captures.update_analysis(capture.id, AnalysisStatus.SKIPPED)
            return None
        raw = await self._gemini.generate_from_text(prompt)
        return await self._apply_analysis_result(
            session_id=session_id,
            capture=capture,
            raw_response=raw,
            video_refcounts=video_refcounts,
        )

    async def _apply_analysis_result(
        self,
        *,
        session_id: str,
        capture: AdCapture,
        raw_response: str,
        video_refcounts: Counter[str],
    ) -> str | None:
        result, data = parse_result(raw_response)
        result, data = await self._guardrails.apply(
            capture=capture,
            result=result,
            data=data,
        )
        summary = json.dumps(data, ensure_ascii=False)

        if result == "relevant":
            await self._uow.ad_captures.update_analysis(
                capture.id,
                AnalysisStatus.COMPLETED,
                summary,
            )
            logger.info(
                "Session %s: capture %s RELEVANT — %s",
                session_id,
                capture.id,
                data.get("reason", ""),
            )
            return None
        if result == "not_relevant":
            await self._uow.ad_captures.update_analysis(
                capture.id,
                AnalysisStatus.NOT_RELEVANT,
                summary,
            )
            logger.info(
                "Session %s: capture %s NOT relevant — %s",
                session_id,
                capture.id,
                data.get("reason", ""),
            )
            return None

        await self._uow.ad_captures.update_analysis(
            capture.id,
            AnalysisStatus.SKIPPED,
            summary,
        )
        logger.warning(
            "Session %s: capture %s UNCLEAR — %s",
            session_id,
            capture.id,
            data.get("reason", ""),
        )
        return None

    def _resolve_cleanup_dir(
        self, video_file: str | None, video_refcounts: Counter[str],
    ) -> str | None:
        if not video_file:
            return None
        if video_refcounts.get(video_file, 0) > 1:
            logger.info("Skipping cleanup — %s is shared by %d captures", video_file, video_refcounts[video_file])
            return None
        video_path = self._base_path / video_file
        capture_dir = video_path.parent
        try:
            return str(capture_dir.relative_to(self._base_path))
        except ValueError:
            return str(capture_dir)

    @staticmethod
    def _requires_video_analysis(capture: AdCapture) -> bool:
        return (
            capture.video_status == VideoStatus.COMPLETED
            and bool(capture.video_file)
        )

    @staticmethod
    def _has_text_analysis_input(capture: AdCapture) -> bool:
        return build_text_prompt(capture) is not None

    def _is_capture_analyzable(self, capture: AdCapture) -> bool:
        return self._requires_video_analysis(capture) or self._has_text_analysis_input(capture)
