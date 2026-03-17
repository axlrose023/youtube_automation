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
    "bitcoin": ("bitcoin", "btc"),
    "ethereum": ("ethereum", "eth"),
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
    aliases = _TOPIC_ALIASES.get(normalized)
    if aliases:
        return aliases
    return (normalized,)


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
        if not variant_tokens:
            continue
        if len(variant_tokens) == 1 and title_tokens.intersection(variant_tokens):
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
