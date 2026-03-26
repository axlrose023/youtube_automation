from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from app.api.modules.ad_captures.models import AdCapture, AnalysisStatus, VideoStatus
from app.clients.gemini import GeminiClient
from app.database.uow import UnitOfWork
from app.services.emulation.media_storage import MediaStorage
from app.services.emulation.video_sampling import AdAnalysisVideoSampler

logger = logging.getLogger(__name__)

_MAX_VIDEO_SIZE_MB = 20
_TOKEN_RE = re.compile(r"[0-9a-zа-яіїєґ]+", re.IGNORECASE)

_STRONG_FINANCE_TOKENS = {
    "forex", "crypto", "bitcoin", "ethereum", "defi", "yield", "staking",
    "stake", "wallet", "exchange", "broker", "brokers", "cfd", "cfds",
    "mt4", "mt5", "dividend", "dividends", "reit", "ovdp", "bond", "bonds",
    "airdrop", "airdrops", "apy", "apr",
}
_STRONG_FINANCE_PHRASES = (
    "yield farming",
    "copy trading",
    "trading signal",
    "trading signals",
    "funded account",
    "funded accounts",
    "prop firm",
    "prop firms",
)
_WEAK_FINANCE_TOKENS = {
    "invest", "investing", "investment", "investments", "earn", "earning",
    "earnings", "income", "profit", "profits", "return", "returns",
    "passive", "side",
}
_NEGATIVE_TOKENS = {
    "game", "games", "play", "hero", "heroes", "survival", "battle", "war",
    "resources", "kingdom", "pet", "pets", "food", "chocolate", "music",
    "telecom", "tabletki", "vitamins", "cosmetics",
}
_NEGATIVE_CTA_TOKENS = {"play", "играть"}

_RELEVANCE_SCOPE = """\
Classify relevance for a narrow acquisition scope.

Mark the ad as RELEVANT only if it directly promotes one of these end-user outcomes:
- forex trading, CFDs, brokers, MT4/MT5, prop firms, funded accounts, copy trading, trading signals
- crypto trading, crypto exchanges, wallets, staking, yield, DeFi earning products, "earn with crypto"
- investing or trading products aimed at retail users to grow money, generate returns, receive yield, or make profit
- money-making offers tied to investing, trading, passive income, side income, or financial speculation

Mark the ad as NOT RELEVANT if it is mainly about any of these:
- generic fintech or B2B software
- AI tools, competitor tracking, market intelligence, analytics, CRM, productivity, project management
- payment infrastructure, banking APIs, card issuing, treasury tools, accounting, invoicing, ERP, tax software
- business services for finance companies rather than ads asking the viewer to trade, invest, or earn
- general finance content that does not clearly sell a trading, investing, yield, or money-making product
- ordinary banking, loans, insurance, or payments unless the ad explicitly pitches returns, trading, or profit

Important:
- Do NOT mark an ad as relevant just because it mentions finance companies, market analysis, competitor monitoring, pricing changes, or financial terminology.
- Be conservative. If the connection to trading, investing, yield, or making money is weak or indirect, return "not_relevant".
- Relevance must come from what the viewer is being asked to do, not just the industry the advertiser serves.
"""

_ANALYSIS_RESPONSE_FORMAT = """\
Respond with ONLY a JSON object.

If the ad IS relevant, include these fields:
- "result": "relevant"
- "reason": short explanation (1 sentence)
- "advertiser": brand or company name
- "product": what is being advertised
- "category": one of "crypto", "forex", "trading", "broker", "prop_firm", "investing", "yield", "make_money", "other_relevant"
- "cta": call to action if present (e.g. "Sign up now", "Download the app")
- "language": detected language of the ad

If the ad is NOT relevant, include only:
- "result": "not_relevant"
- "reason": short explanation (1 sentence)
"""

_ANALYSIS_PROMPT = f"""\
Watch this advertisement video (both visual and audio).

{_RELEVANCE_SCOPE}

{_ANALYSIS_RESPONSE_FORMAT}

If the video is corrupted, silent with no visuals, or completely unrecognizable:
- "result": "unclear"
- "reason": short explanation

Examples:
{{"result": "relevant", "reason": "Binance promotes crypto trading for users who want to buy and trade digital assets", "advertiser": "Binance", "product": "Crypto exchange", "category": "crypto", "cta": "Trade now", "language": "English"}}
{{"result": "relevant", "reason": "Broker ad invites viewers to start forex trading on MT5", "advertiser": "Exness", "product": "Forex trading platform", "category": "forex", "cta": "Open account", "language": "English"}}
{{"result": "not_relevant", "reason": "AI competitor monitoring tool for businesses, not a trading or investing offer"}}
{{"result": "unclear", "reason": "Video is black screen with no audio"}}
"""

_TEXT_ANALYSIS_PROMPT = f"""\
Analyze this advertisement metadata and visible text.

{_RELEVANCE_SCOPE}

{_ANALYSIS_RESPONSE_FORMAT}

If the metadata is insufficient or ambiguous:
- "result": "unclear"
- "reason": short explanation

Examples:
{{"result": "relevant", "reason": "The ad promotes a crypto app where users can stake tokens and earn yield", "advertiser": "Bybit", "product": "Crypto earning app", "category": "yield", "cta": "Start earning", "language": "English"}}
{{"result": "not_relevant", "reason": "The ad is for business intelligence software serving fintech companies, not for trading or earning money"}}
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
        storage: MediaStorage, video_sampler: AdAnalysisVideoSampler,
    ) -> None:
        self._gemini = gemini
        self._uow = uow
        self._base_path = base_path
        self._storage = storage
        self._video_sampler = video_sampler

    async def get_session_analysis_workload(self, session_id: str) -> int:
        captures = await self._uow.ad_captures.get_by_session(session_id)
        return len(
            [
                c
                for c in captures
                if c.video_status == VideoStatus.COMPLETED
                and c.analysis_status == AnalysisStatus.PENDING
                and c.video_file
            ]
        )

    async def analyze_session_captures(self, session_id: str) -> tuple[str | None, int, int]:
        captures = await self._uow.ad_captures.get_by_session(session_id)
        pending = [
            c for c in captures
            if c.video_status == VideoStatus.COMPLETED
            and c.analysis_status == AnalysisStatus.PENDING
            and c.video_file
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
            return "failed", 0, len(pending)

        for rel_dir in dirs_to_cleanup:
            await self._storage.remove_capture_dir(rel_dir)

        terminal_statuses = {"completed", "not_relevant", "failed", "skipped"}
        done = sum(
            1 for capture in pending if str(capture.analysis_status or "").lower() in terminal_statuses
        )
        failed = sum(
            1 for capture in pending if str(capture.analysis_status or "").lower() == "failed"
        )
        final_status = "failed" if failed > 0 else "completed"
        return final_status, done, len(pending)

    async def _analyze_one(
        self, session_id: str, capture: AdCapture, video_refcounts: Counter[str],
    ) -> str | None:
        video_path = self._base_path / capture.video_file
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

            raw = await self._gemini.generate_from_video(prepared_video.path, _ANALYSIS_PROMPT)
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
        result, data = await self._apply_relevance_guardrails(
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

    async def _apply_relevance_guardrails(
        self,
        *,
        capture: AdCapture,
        result: str,
        data: dict,
    ) -> tuple[str, dict]:
        if result != "relevant":
            return result, data

        evidence = self._collect_metadata_evidence(capture)
        if evidence.strongly_non_finance and not evidence.has_strong_finance:
            return (
                "not_relevant",
                {
                    "result": "not_relevant",
                    "reason": (
                        "The ad metadata and advertiser domain indicate a non-financial "
                        "consumer/game ad, so the video-only finance signal was rejected."
                    ),
                },
            )

        if evidence.has_strong_finance:
            return result, data

        prompt = self._build_text_prompt(capture)
        if prompt is None:
            return (
                "not_relevant",
                {
                    "result": "not_relevant",
                    "reason": (
                        "The ad metadata does not corroborate a trading, investing, "
                        "yield, or money-making offer."
                    ),
                },
            )

        raw = await self._gemini.generate_from_text(prompt)
        text_result, text_data = _parse_result(raw)
        if text_result == "relevant":
            return text_result, text_data

        reason = text_data.get("reason") or (
            "The ad text and advertiser metadata do not support a trading, investing, "
            "yield, or money-making offer."
        )
        return "not_relevant", {"result": "not_relevant", "reason": reason}

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

    def _collect_metadata_evidence(self, capture: AdCapture) -> "_MetadataEvidence":
        values = [
            capture.headline_text,
            capture.advertiser_domain,
            capture.display_url,
            capture.landing_url,
            capture.cta_href,
        ]
        normalized_parts = [self._normalize_text(value) for value in values if isinstance(value, str) and value.strip()]
        normalized = " ".join(part for part in normalized_parts if part)
        tokens = set(_TOKEN_RE.findall(normalized))

        strong_tokens = {token for token in _STRONG_FINANCE_TOKENS if token in tokens}
        weak_tokens = {token for token in _WEAK_FINANCE_TOKENS if token in tokens}
        negative_tokens = {token for token in _NEGATIVE_TOKENS if token in tokens}
        phrase_hits = {phrase for phrase in _STRONG_FINANCE_PHRASES if phrase in normalized}

        hosts = {
            host
            for host in (
                self._extract_host(capture.advertiser_domain),
                self._extract_host(capture.display_url),
                self._extract_host(capture.landing_url),
                self._extract_host(capture.cta_href),
            )
            if host
        }
        host_tokens = {
            token
            for host in hosts
            for token in _TOKEN_RE.findall(host.replace(".", " "))
        }
        strong_tokens.update(token for token in _STRONG_FINANCE_TOKENS if token in host_tokens)
        negative_tokens.update(token for token in _NEGATIVE_TOKENS if token in host_tokens)

        cta_tokens = set()
        if isinstance(capture.cta_href, str):
            cta_tokens.update(_TOKEN_RE.findall(self._normalize_text(capture.cta_href)))
        if isinstance(capture.headline_text, str):
            cta_tokens.update(_TOKEN_RE.findall(self._normalize_text(capture.headline_text)))

        return _MetadataEvidence(
            strong_finance=strong_tokens | phrase_hits,
            weak_finance=weak_tokens,
            negative=negative_tokens,
            has_game_tld=any(host.endswith(".game") for host in hosts),
            has_play_cta=bool(_NEGATIVE_CTA_TOKENS & tokens),
        )

    @staticmethod
    def _normalize_text(value: str) -> str:
        return re.sub(r"[^0-9a-zа-яіїєґ]+", " ", value.casefold()).strip()

    @staticmethod
    def _extract_host(value: str | None) -> str | None:
        if not value:
            return None
        candidate = value.strip()
        if not candidate:
            return None
        if "://" not in candidate:
            candidate = f"https://{candidate}"
        parsed = urlparse(candidate)
        host = parsed.netloc or parsed.path
        host = host.lower().strip().strip("/")
        return host or None


@dataclass(frozen=True)
class _MetadataEvidence:
    strong_finance: set[str]
    weak_finance: set[str]
    negative: set[str]
    has_game_tld: bool
    has_play_cta: bool

    @property
    def has_strong_finance(self) -> bool:
        return bool(self.strong_finance)

    @property
    def strongly_non_finance(self) -> bool:
        return self.has_game_tld or self.has_play_cta or bool(self.negative)
