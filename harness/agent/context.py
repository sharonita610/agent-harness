"""컨텍스트 예산: 토큰 추정, 긴 툴 결과 자르기, 오래된 툴 결과 압축."""

from __future__ import annotations

import json
import math
from typing import Any


def estimate_tokens(text: str) -> int:
    # 한글은 글자당 토큰이 많다. 대략 3자 = 1토큰으로 보수적으로 잡는다.
    return math.ceil(len(text) / 3) + 1


def messages_tokens(messages: list[dict[str, Any]]) -> int:
    total = 0
    for m in messages:
        total += estimate_tokens(json.dumps(m, ensure_ascii=False, default=str)) + 4
    return total


def to_text(result: Any) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, default=str)


def truncate_result(result: Any, max_chars: int = 4000) -> str:
    text = to_text(result)
    if len(text) <= max_chars:
        return text
    return (
        text[:max_chars]
        + f"\n... [잘림: 전체 {len(text)}자 중 {max_chars}자만 표시. 조건이나 LIMIT 을 좁혀 다시 조회하세요]"
    )


def compact(messages: list[dict[str, Any]], budget_tokens: int, keep_last: int = 6) -> list[dict[str, Any]]:
    """예산을 넘으면 오래된 tool 결과를 짧은 자리표시자로 바꾼다. system 과 마지막 keep_last 개는 유지."""
    if messages_tokens(messages) <= budget_tokens:
        return messages
    out = list(messages)
    candidates = [i for i, m in enumerate(out) if m.get("role") == "tool"]
    protected = set(range(max(1, len(out) - keep_last), len(out)))
    for i in candidates:
        if i in protected:
            continue
        content = out[i].get("content") or ""
        if len(content) > 200:
            out[i] = {**out[i], "content": content[:160] + " … [이전 결과 압축됨]"}
        if messages_tokens(out) <= budget_tokens:
            break
    return out
