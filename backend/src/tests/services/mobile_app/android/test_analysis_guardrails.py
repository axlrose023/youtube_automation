from __future__ import annotations

import asyncio

from app.services.emulation.ads.analysis.guardrails import AdAnalysisGuardrails
from app.services.emulation.ads.analysis.prompt import build_text_prompt


class _FakeGemini:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def generate_from_text(self, prompt: str) -> str:
        self.calls.append(prompt)
        return '{"result":"not_relevant","reason":"text fallback rejected"}'


class _Capture:
    headline_text = None
    sponsor_label = None
    advertiser_domain = None
    display_url = None
    landing_url = None
    landing_scrape_title = None
    landing_scrape_url = None
    cta_href = None
    cta_text = "Visit advertiser"
    full_visible_text = "Sponsored\nVisit advertiser\nSkip ad"
    full_text = full_visible_text


def test_guardrails_keep_video_relevant_when_metadata_is_sparse() -> None:
    gemini = _FakeGemini()
    guardrails = AdAnalysisGuardrails(gemini)

    result, data = asyncio.run(
        guardrails.apply(
            capture=_Capture(),
            result="relevant",
            data={"result": "relevant", "reason": "Video shows a forex trading app offer."},
        )
    )

    assert result == "relevant"
    assert data["result"] == "relevant"
    assert gemini.calls == []


class _RichCapture(_Capture):
    display_url = "example-crm.com"
    full_visible_text = "AI CRM platform for sales teams"
    full_text = full_visible_text


def test_guardrails_can_override_video_relevant_when_metadata_is_substantive() -> None:
    gemini = _FakeGemini()
    guardrails = AdAnalysisGuardrails(gemini)

    result, data = asyncio.run(
        guardrails.apply(
            capture=_RichCapture(),
            result="relevant",
            data={"result": "relevant", "reason": "Video looked finance-related."},
        )
    )

    assert result == "not_relevant"
    assert data["result"] == "not_relevant"
    assert len(gemini.calls) == 1


class _AiTradingCapture(_Capture):
    headline_text = "Trilon AI Trading - The Future"
    display_url = "www3.trilon-ai.info"
    landing_scrape_title = "Trilon AI Trading - The Future"
    landing_scrape_url = "https://www3.trilon-ai.info/cara27"
    cta_text = "Start now"
    full_visible_text = "AI trading platform. Make money online with automated trading."
    full_text = full_visible_text


def test_guardrails_keep_ai_trading_offer_relevant() -> None:
    gemini = _FakeGemini()
    guardrails = AdAnalysisGuardrails(gemini)

    result, data = asyncio.run(
        guardrails.apply(
            capture=_AiTradingCapture(),
            result="relevant",
            data={"result": "relevant", "reason": "Video shows an AI trading signup flow."},
        )
    )

    assert result == "relevant"
    assert data["result"] == "relevant"
    assert gemini.calls == []


def test_build_text_prompt_includes_landing_scrape_metadata() -> None:
    prompt = build_text_prompt(_AiTradingCapture())

    assert prompt is not None
    assert "landing_scrape_title: Trilon AI Trading - The Future" in prompt
    assert "landing_scrape_url: https://www3.trilon-ai.info/cara27" in prompt
