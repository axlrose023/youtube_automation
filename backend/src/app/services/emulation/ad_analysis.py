from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path

from app.api.modules.ad_captures.models import AdCapture, AnalysisStatus, VideoStatus
from app.clients.gemini import GeminiClient
from app.database.uow import UnitOfWork
from app.services.emulation.media_storage import MediaStorage

logger = logging.getLogger(__name__)

_MAX_VIDEO_SIZE_MB = 20

_ANALYSIS_PROMPT = """\
Watch this advertisement video (both visual and audio).

Is this ad related to ANY of these topics: cryptocurrency, crypto, forex, trading, finance, investments, fintech, blockchain, DeFi, NFT, stock market, banking, loans, insurance?

Respond with ONLY a JSON object.

If the ad IS relevant, include these fields:
- "result": "relevant"
- "reason": short explanation (1 sentence)
- "advertiser": brand or company name
- "product": what is being advertised
- "category": one of "crypto", "forex", "trading", "banking", "insurance", "investments", "fintech", "other_finance"
- "cta": call to action if present (e.g. "Sign up now", "Download the app")
- "language": detected language of the ad

If the ad is NOT relevant, include only:
- "result": "not_relevant"
- "reason": short explanation (1 sentence)

If the video is corrupted, silent with no visuals, or completely unrecognizable:
- "result": "unclear"
- "reason": short explanation

Examples:
{"result": "relevant", "reason": "Binance crypto exchange promoting zero-fee trading", "advertiser": "Binance", "product": "Crypto trading platform", "category": "crypto", "cta": "Trade now with zero fees", "language": "English"}
{"result": "not_relevant", "reason": "Nike ad for running shoes"}
{"result": "unclear", "reason": "Video is black screen with no audio"}
"""

_TEXT_ANALYSIS_PROMPT = """\
Analyze this advertisement metadata and visible text.

Is this ad related to ANY of these topics: cryptocurrency, crypto, forex, trading, finance, investments, fintech, blockchain, DeFi, NFT, stock market, banking, loans, insurance?

Respond with ONLY a JSON object.

If the ad IS relevant, include these fields:
- "result": "relevant"
- "reason": short explanation (1 sentence)
- "advertiser": brand or company name
- "product": what is being advertised
- "category": one of "crypto", "forex", "trading", "banking", "insurance", "investments", "fintech", "other_finance"
- "cta": call to action if present (e.g. "Sign up now", "Download the app")
- "language": detected language of the ad

If the ad is NOT relevant, include only:
- "result": "not_relevant"
- "reason": short explanation (1 sentence)

If the metadata is insufficient or ambiguous:
- "result": "unclear"
- "reason": short explanation
"""


def _parse_result(text: str) -> tuple[str, dict]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    try:
        data = json.loads(text)
        result = str(data.get("result", "unclear")).lower()
        if result not in ("relevant", "not_relevant", "unclear"):
            result = "unclear"
        data["result"] = result
        return result, data
    except (json.JSONDecodeError, AttributeError):
        return "unclear", {"result": "unclear", "reason": text[:200]}


class AdAnalysisService:
    def __init__(
        self, gemini: GeminiClient, uow: UnitOfWork, base_path: Path,
        storage: MediaStorage,
    ) -> None:
        self._gemini = gemini
        self._uow = uow
        self._base_path = base_path
        self._storage = storage

    async def analyze_session_captures(self, session_id: str) -> None:
        captures = await self._uow.ad_captures.get_by_session(session_id)
        pending = [
            c for c in captures
            if c.video_status == VideoStatus.COMPLETED
            and c.analysis_status == AnalysisStatus.PENDING
            and c.video_file
        ]
        if not pending:
            return

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
            return

        for rel_dir in dirs_to_cleanup:
            await self._storage.remove_capture_dir(rel_dir)

    async def _analyze_one(
        self, session_id: str, capture: AdCapture, video_refcounts: Counter[str],
    ) -> str | None:
        video_path = self._base_path / capture.video_file
        try:
            if not video_path.exists():
                logger.warning(
                    "Session %s: video missing for capture %s",
                    session_id, capture.id,
                )
                await self._uow.ad_captures.update_analysis(capture.id, AnalysisStatus.FAILED)
                return None

            size_mb = video_path.stat().st_size / (1024 * 1024)
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

            raw = await self._gemini.generate_from_video(video_path, _ANALYSIS_PROMPT)
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
        prompt = self._build_text_prompt(capture)
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
        result, data = _parse_result(raw_response)
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
            return self._resolve_cleanup_dir(capture.video_file, video_refcounts)

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

    def _build_text_prompt(self, capture: AdCapture) -> str | None:
        fields = {
            "headline": capture.headline_text,
            "advertiser_domain": capture.advertiser_domain,
            "display_url": capture.display_url,
            "landing_url": capture.landing_url,
            "cta_href": capture.cta_href,
        }
        lines = [f"{key}: {value}" for key, value in fields.items() if value]
        if not lines:
            return None
        return f"{_TEXT_ANALYSIS_PROMPT}\n\nAd metadata:\n" + "\n".join(lines)

    def _resolve_cleanup_dir(
        self, video_file: str, video_refcounts: Counter[str],
    ) -> str | None:
        if video_refcounts.get(video_file, 0) > 1:
            logger.info("Skipping cleanup — %s is shared by %d captures", video_file, video_refcounts[video_file])
            return None
        video_path = self._base_path / video_file
        capture_dir = video_path.parent
        try:
            return str(capture_dir.relative_to(self._base_path))
        except ValueError:
            return str(capture_dir)
