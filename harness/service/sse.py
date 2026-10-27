"""Server-Sent Events 포맷."""
from __future__ import annotations

import json
from typing import Any


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def ping() -> str:
    return ": ping\n\n"
