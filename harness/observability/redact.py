"""기록 가리기: 트레이스·서비스 이벤트·평가 결과에 남는 도구 결과와 답에서 개인정보 꼴을 지운다.

비서는 답에서 연락처를 마스킹하지만(회사 팩 MASK_COLUMNS), 도구 결과 원문은 트레이스(JSONL)·서비스 DB(run_events)·
평가 결과(JSON, 레포에 커밋)에 그대로 남았다. 실제 데이터를 붙이는 순간 그 파일들이 개인정보 저장소가 된다.
여기서는 두 가지로 가린다: (1) 값의 꼴 — 전화번호·이메일·주민등록번호·카드번호 패턴, (2) 키 이름 — phone·전화·연락처·
email·주민·password·token 같은 키의 값은 통째로(단어 경계 기준이라 prompt_tokens 같은 수치는 건드리지 않는다). 채점은 가리기 전에 끝내므로 평가 점수에는 영향이 없다.
HARNESS_REDACT=0 으로 끈다. 보존 기간은 HARNESS_TRACE_RETENTION_DAYS(트레이스 폴더에서 오래된 파일 삭제).
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}\b"), "010-****-****"),  # 휴대전화
    (re.compile(r"\b0\d{1,2}[-.\s]\d{3,4}[-.\s]\d{4}\b"), "0**-****-****"),  # 지역번호 유선전화
    (re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), "***@***"),  # 이메일
    (re.compile(r"\b\d{6}-[1-4]\d{6}\b"), "******-*******"),  # 주민등록번호
    (re.compile(r"\b(?:\d{4}[-\s]){3}\d{4}\b"), "****-****-****-****"),  # 카드번호
]
# 민감한 키: 영문은 단어 경계로(prompt_tokens 의 "tokens" 는 아님), 한국어는 부분 일치
_SENSITIVE_WORD = re.compile(
    r"(^|[_\-\s.])(phone|mobile|tel|email|e-mail|ssn|resident|password|passwd|secret|token|api_?key|authorization|bearer)([_\-\s.]|$)",
    re.I,
)
_SENSITIVE_KO = ("전화", "연락처", "이메일", "주민", "비밀번호")
MASK = "***"


def enabled() -> bool:
    return os.getenv("HARNESS_REDACT", "1").strip().lower() not in ("0", "false", "no", "off")


def redact_text(text: str) -> str:
    if not text:
        return text
    for pat, rep in PATTERNS:
        text = pat.sub(rep, text)
    return text


def _sensitive_key(key: Any) -> bool:
    k = str(key)
    return bool(_SENSITIVE_WORD.search(k)) or any(h in k for h in _SENSITIVE_KO)


def redact_obj(obj: Any, key: Any = None) -> Any:
    """문자열은 패턴으로, 민감한 키의 값은 통째로 가린다. dict·list 는 재귀. 숫자·불리언은 그대로."""
    if obj is None or isinstance(obj, (bool, int, float)):
        return MASK if (key is not None and _sensitive_key(key) and obj is not None) else obj
    if isinstance(obj, str):
        if key is not None and _sensitive_key(key):
            return MASK
        return redact_text(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v, k) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v, key) for v in obj]
    return obj


def maybe_redact(obj: Any) -> Any:
    return redact_obj(obj) if enabled() else obj


def prune_old_files(folder: Path, days: float | None = None, pattern: str = "*.jsonl") -> int:
    """folder 아래에서 days 보다 오래된 파일을 지운다. days 가 None 이면 HARNESS_TRACE_RETENTION_DAYS, 그것도 없으면 아무것도 안 한다."""
    if days is None:
        raw = os.getenv("HARNESS_TRACE_RETENTION_DAYS", "").strip()
        if not raw:
            return 0
        days = float(raw)
    cutoff = time.time() - days * 86400
    n = 0
    for p in Path(folder).rglob(pattern):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                n += 1
        except OSError:
            continue
    return n
