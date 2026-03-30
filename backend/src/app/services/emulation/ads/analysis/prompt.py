from __future__ import annotations

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

ANALYSIS_PROMPT = f"""\
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

TEXT_ANALYSIS_PROMPT = f"""\
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


def build_text_prompt(capture) -> str | None:
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
    return f"{TEXT_ANALYSIS_PROMPT}\n\nAd metadata:\n" + "\n".join(lines)
