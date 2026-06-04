from __future__ import annotations

from app.services.emulation.ads.analysis.prompt import ANALYSIS_PROMPT, TEXT_ANALYSIS_PROMPT


def test_analysis_prompt_mentions_ai_trading_and_make_money_scope() -> None:
    lower = ANALYSIS_PROMPT.casefold()

    assert "quantum ai" in lower
    assert "ai trading" in lower
    assert "make money online" in lower
    assert "trilon ai trading" in lower


def test_text_analysis_prompt_mentions_shady_consumer_earning_offers() -> None:
    lower = TEXT_ANALYSIS_PROMPT.casefold()

    assert "financial freedom" in lower
    assert "automated trading" in lower
    assert "scammy-looking wealth ads are still relevant" in lower
