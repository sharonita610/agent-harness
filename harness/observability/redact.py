"""기록 가리기: 트레이스·서비스 이벤트·평가 결과에 남는 도구 결과와 답에서 개인정보 꼴을 지운다.

비서는 답에서 연락처를 마스킹하지만(회사 팩 MASK_COLUMNS), 도구 결과 원문은 트레이스(JSONL)·서비스 DB(run_events)·
평가 결과(JSON, 레포에 커밋)에 그대로 남았다. 실제 데이터를 붙이는 순간 그 파일들이 개인정보 저장소가 된다.
여기서는 두 가지로 가린다: (1) 값의 꼴 — 전화번호·이메일·주민등록번호·카드번호 패턴, (2) 키 이름 — phone·전화·연락처·
email·주민·password·token 같은 키의 값은 통째로(단어 경계 기준이라 prompt_tokens 같은 수치는 건드리지 않는다). 채점은 가리기 전에 끝내므로 평가 점수에는 영향이 없다.
HARNESS_REDACT=0 으로 끈다. 보존 기간은 HARNESS_TRACE_RETENTION_DAYS(트레이스 폴더에서 오래된 파일 삭제).

경계는 영숫자 기준이다(ADR-033). 정규식 \b 는 한글을 단어 글자로 보아 "010-1234-5678로", "900101-1234567입니다" 처럼
조사·서술어가 바로 붙은 번호(한국어 문장의 보통 꼴)에 경계가 생기지 않아 그대로 남겼다. 지금은 번호 앞뒤가 영숫자가 아니면 경계로 본다.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

_L = r"(?<![0-9A-Za-z])"  # 앞 경계: 영숫자가 아니면 된다(한글 조사·서술어가 바로 붙어도 경계)
_R = r"(?![0-9A-Za-z])"  # 뒤 경계: 같다. "5678로", "4567입니다" 가 걸린다
PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(_L + r"01[016789][-.\s]?\d{3,4}[-.\s]?\d{4}" + _R), "010-****-****"),  # 휴대전화
    (re.compile(_L + r"0\d{1,2}[-.\s]\d{3,4}[-.\s]\d{4}" + _R), "0**-****-****"),  # 지역번호 유선전화
    # 이메일: 아이디·도메인은 영숫자만. 뒤에 붙은 조사("…com으로")는 주소가 아니므로 남긴다
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+" + _R), "***@***"),
    (re.compile(_L + r"\d{6}-[1-4]\d{6}" + _R), "******-*******"),  # 주민등록번호
    (re.compile(_L + r"(?:\d{4}[-\s]){3}\d{4}" + _R), "****-****-****-****"),  # 카드번호
    # 계좌번호: 은행별 3단 형식(3~6 - 2~6 - 4~8). 날짜(4-2-2)·실행 id(8-6)·범위(3-3)는 묶음 수나 끝 묶음 길이가 달라 안 걸린다.
    # 사업자등록번호(3-2-5)는 같은 꼴이라 함께 가려진다(과가림 쪽을 택함)
    (re.compile(_L + r"\d{3,6}-\d{2,6}-\d{4,8}" + _R), "***-***-******"),
]
# 민감한 키: 영문은 단어 경계로(prompt_tokens 의 "tokens" 는 아님), 한국어는 부분 일치
_SENSITIVE_WORD = re.compile(
    r"(^|[_\-\s.])(phone|mobile|tel|email|e-mail|ssn|resident|password|passwd|secret|token|api_?key|authorization|bearer"
    r"|account_?(?:no|num|number)|bank_?account|iban)([_\-\s.]|$)",
    re.I,
)
_SENSITIVE_KO = ("전화", "연락처", "이메일", "주민", "비밀번호", "계좌")
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
