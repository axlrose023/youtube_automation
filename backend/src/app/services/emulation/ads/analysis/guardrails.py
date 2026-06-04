from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from app.clients.gemini import GeminiClient

from .parser import parse_result
from .prompt import build_text_prompt

_TOKEN_RE = re.compile(r"[0-9a-zа-яіїєґ]+", re.IGNORECASE)

_STRONG_FINANCE_TOKENS = {
    "forex", "crypto", "bitcoin", "ethereum", "defi", "yield", "staking",
    "stake", "wallet", "exchange", "broker", "brokers", "cfd", "cfds",
    "trade", "trading", "trader", "traders",
    "mt4", "mt5", "dividend", "dividends", "reit", "ovdp", "bond", "bonds",
    "airdrop", "airdrops", "apy", "apr",
}
_STRONG_FINANCE_PHRASES = (
    "ai trading",
    "quantum ai",
    "quantum trading",
    "automated trading",
    "auto trading",
    "algorithmic trading",
    "robo trading",
    "robot trading",
    "trading bot",
    "trading bots",
    "yield farming",
    "copy trading",
    "trading signal",
    "trading signals",
    "funded account",
    "funded accounts",
    "prop firm",
    "prop firms",
    "passive income",
    "side income",
    "make money online",
    "earn money online",
    "financial freedom",
    "daily profit",
    "daily profits",
    "automated income",
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
_GENERIC_METADATA_TOKENS = {
    "sponsored",
    "advertiser",
    "visit",
    "site",
    "learn",
    "more",
    "watch",
    "now",
    "skip",
    "like",
    "ad",
    "share",
    "close",
    "panel",
    "my",
    "center",
}


class AdAnalysisGuardrails:
    def __init__(self, gemini: GeminiClient) -> None:
        self._gemini = gemini

    async def apply(
        self,
        *,
        capture,
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

        if not evidence.has_substantive_metadata:
            return result, data

        prompt = build_text_prompt(capture)
        if prompt is None:
            return result, data

        raw = await self._gemini.generate_from_text(prompt)
        text_result, text_data = parse_result(raw)
        if text_result == "relevant":
            return text_result, text_data
        if text_result == "unclear":
            return result, data

        reason = text_data.get("reason") or (
            "The ad text and advertiser metadata do not support a trading, investing, "
            "yield, or money-making offer."
        )
        return "not_relevant", {"result": "not_relevant", "reason": reason}

    def _collect_metadata_evidence(self, capture) -> "_MetadataEvidence":
        values = [
            self._capture_value(capture, "headline_text"),
            self._capture_value(capture, "sponsor_label"),
            self._capture_value(capture, "advertiser_domain"),
            self._capture_value(capture, "display_url"),
            self._capture_value(capture, "landing_url"),
            self._capture_value(capture, "landing_scrape_title"),
            self._capture_value(capture, "landing_scrape_url"),
            self._capture_value(capture, "cta_href"),
            self._capture_value(capture, "cta_text"),
            self._capture_value(capture, "full_visible_text"),
            self._capture_value(capture, "full_text"),
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
                self._extract_host(self._capture_value(capture, "advertiser_domain")),
                self._extract_host(self._capture_value(capture, "display_url")),
                self._extract_host(self._capture_value(capture, "landing_url")),
                self._extract_host(self._capture_value(capture, "landing_scrape_url")),
                self._extract_host(self._capture_value(capture, "cta_href")),
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

        substantive_tokens = {
            token
            for token in (tokens | host_tokens)
            if token not in _GENERIC_METADATA_TOKENS
        }

        return _MetadataEvidence(
            strong_finance=strong_tokens | phrase_hits,
            weak_finance=weak_tokens,
            negative=negative_tokens,
            has_game_tld=any(host.endswith(".game") for host in hosts),
            has_play_cta=bool(_NEGATIVE_CTA_TOKENS & tokens),
            has_substantive_metadata=bool(hosts or substantive_tokens),
        )

    @staticmethod
    def _normalize_text(value: str) -> str:
        return re.sub(r"[^0-9a-zа-яіїєґ]+", " ", value.casefold()).strip()

    @staticmethod
    def _capture_value(capture, name: str) -> str | None:
        value = getattr(capture, name, None)
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        return stripped or None

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
    has_substantive_metadata: bool

    @property
    def has_strong_finance(self) -> bool:
        return bool(self.strong_finance)

    @property
    def strongly_non_finance(self) -> bool:
        return self.has_game_tld or self.has_play_cta or bool(self.negative)
