from __future__ import annotations

import json


def parse_result(text: str) -> tuple[str, dict]:
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
