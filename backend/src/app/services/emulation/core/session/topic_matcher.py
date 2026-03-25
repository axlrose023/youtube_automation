from __future__ import annotations

import re


_TOPIC_ALIASES: dict[str, tuple[str, ...]] = {
    "finance": ("finance", "financial", "financial literacy", "personal finance"),
    "financial markets": ("financial markets", "financial market", "capital markets", "capital market"),
    "investments": ("investments", "investment", "investing", "portfolio investing"),
    "investment": ("investment", "investments", "investing", "portfolio investing"),
    "investing": ("investing", "investment", "investments", "portfolio investing"),
    "stocks": ("stocks", "stock market", "equities", "equity market"),
    "stock market": ("stock market", "stocks", "equities", "equity market"),
    "crypto": ("crypto", "cryptocurrency", "digital assets"),
    "crypto investments": ("crypto investing", "crypto investment", "invest in crypto", "crypto portfolio"),
    "passive income": (
        "passive income",
        "investment income",
        "dividend income",
        "staking income",
        "yield income",
    ),
    "side income": (
        "side income",
        "passive income",
        "investment income",
        "dividend income",
        "crypto income",
    ),
    "crypto earnings": (
        "crypto earnings",
        "earn with crypto",
        "crypto passive income",
        "crypto yield",
        "crypto staking",
        "staking rewards",
        "defi yield",
    ),
    "crypto earning": (
        "crypto earning",
        "earn with crypto",
        "crypto passive income",
        "crypto yield",
        "crypto staking",
    ),
    "forex trading": ("forex trading", "fx trading", "cfd trading", "mt4", "mt5", "forex broker"),
    "bitcoin": ("bitcoin", "btc"),
    "ethereum": ("ethereum", "eth"),
}

_CRYPTO_TOKENS = {
    "crypto",
    "cryptocurrency",
    "bitcoin",
    "btc",
    "ethereum",
    "eth",
    "defi",
    "blockchain",
}
_CRYPTO_EARN_TOKENS = {
    "earn",
    "earning",
    "earnings",
    "money",
    "profit",
    "profits",
    "yield",
    "staking",
    "stake",
    "passive",
    "income",
    "apy",
    "apr",
    "rewards",
}
_CRYPTO_INVEST_TOKENS = {
    "invest",
    "investment",
    "investing",
    "portfolio",
}
_FOREX_TOKENS = {
    "forex",
    "fx",
    "cfd",
    "cfd trading",
    "mt4",
    "mt5",
    "broker",
}


def normalize_text(value: str) -> str:
    return " ".join(value.lower().split())


def _extract_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[\wа-яА-ЯёЁ]+", value.lower())
        if len(token) >= 3
    }


def _topic_variants(topic: str) -> tuple[str, ...]:
    normalized = normalize_text(topic)
    variants: list[str] = [normalized]

    aliases = _TOPIC_ALIASES.get(normalized)
    if aliases:
        variants.extend(aliases)

    topic_tokens = _extract_tokens(normalized)
    if topic_tokens.intersection(_FOREX_TOKENS):
        variants.extend(_TOPIC_ALIASES["forex trading"])

    has_crypto_family = bool(topic_tokens.intersection(_CRYPTO_TOKENS))
    if has_crypto_family:
        variants.extend(_TOPIC_ALIASES["crypto"])

        if topic_tokens.intersection(_CRYPTO_EARN_TOKENS):
            variants.extend(_TOPIC_ALIASES["crypto earnings"])

        if topic_tokens.intersection(_CRYPTO_INVEST_TOKENS):
            variants.extend(_TOPIC_ALIASES["crypto investments"])

    seen: list[str] = []
    for variant in variants:
        cleaned = normalize_text(variant)
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return tuple(seen)


def build_topic_tokens(topics: list[str]) -> set[str]:
    tokens: set[str] = set()
    for topic in topics:
        for variant in _topic_variants(topic):
            tokens.update(_extract_tokens(variant))
    return tokens


def is_title_on_topic(
    title: str | None,
    topics: list[str],
    topic_tokens: set[str],
) -> bool:
    if not title:
        return False
    normalized = normalize_text(title)

    if any(topic and normalize_text(topic) in normalized for topic in topics):
        return True

    if not topic_tokens:
        return not topics
    return any(token in normalized for token in topic_tokens)


def is_title_on_specific_topic(title: str | None, topic: str | None) -> bool:
    if not title or not topic:
        return False
    normalized_title = normalize_text(title)
    normalized_topic = normalize_text(topic)
    if normalized_topic and normalized_topic in normalized_title:
        return True

    variants = _topic_variants(normalized_topic)
    if any(variant and variant in normalized_title for variant in variants):
        return True

    title_tokens = _extract_tokens(normalized_title)
    for variant in variants:
        variant_tokens = _extract_tokens(variant)
        variant_words = [word for word in variant.split() if word]
        if not variant_tokens:
            continue
        if (
            len(variant_tokens) == 1
            and len(variant_words) == 1
            and title_tokens.intersection(variant_tokens)
        ):
            return True
        if len(variant_tokens) > 1 and variant_tokens.issubset(title_tokens):
            return True
    return False


def matched_topics_for_title(title: str | None, topics: list[str]) -> list[str]:
    if not title:
        return []

    normalized_title = normalize_text(title)
    matched: list[str] = []

    for topic in topics:
        normalized_topic = normalize_text(topic)
        if normalized_topic and normalized_topic in normalized_title:
            matched.append(topic)

    if not matched:
        for topic in topics:
            if is_title_on_specific_topic(title, topic):
                matched.append(topic)

    seen: list[str] = []
    for topic in matched:
        if topic not in seen:
            seen.append(topic)
    return seen
