"""출처 인용 채점: 답에 적힌 "출처: 문서명 > 절" 이 실제 검색 결과의 절과 맞는지.

- extract_sources: 도구 결과(search_docs 의 {"hits": [...]}, search_manual 의 [...], read_manual 의 {"source": …})에서
  (doc, section, section_id) 를 모은다. 루프가 tool_calls 항목의 result_sources 에 남긴다.
- parse_citations: 답의 "문서 > 절" 꼴(괄호 안 포함)과 "출처:" 줄을 항목 단위로 모은다(쉼표·세미콜론으로 이어 쓴 절은 앞 문서명을 이어받음).
- citation_matches: 정규화(공백·괄호·따옴표 제거, 소문자)한 뒤 문서명 부분 일치 + 절 제목 부분 일치(또는 절 번호 일치).
"""

from __future__ import annotations

import re
from typing import Any

_LABEL = r"(?:출처|문서|근거|참고)"
_CITE_LINE = re.compile(  # "출처: …", "(문서: …)", "**출처**: …", 또는 "**출처**" 제목 줄 다음의 첫 항목
    _LABEL + r"\**\s*[:：]\**\s*([^\n]+)|\**출처\**\s*\n\s*[-•*]?\s*([^\n]+)"
)
_NOT_DOC = r"\n()（）【】\[\]“”\"',;·|>"
_PAIR = re.compile(  # "문서 > 절" 꼴(같은 줄에서만: 줄 머리의 '>' 는 블록 인용). 닫는 따옴표는 건너뛴다("“문서” > 절")
    rf"([^{_NOT_DOC}]{{2,40}})[”\"'’]?[ \t]*>[ \t]*([^{_NOT_DOC}]{{2,60}})"
)
_CODE = re.compile(r"```.*?```|`[^`\n]*`", re.S)  # 코드 조각의 '>' (SQL 비교 등)은 인용이 아니다
_SPLIT = re.compile(r"\s*(?:[;,·|]|\s/\s)\s*")
_QUOTES = "“”‘’【】「」『』'\"`*"
_MIN = 2  # 이보다 짧은 조각은 부분 일치로 인정하지 않는다("규" 하나가 모든 규정에 맞는 일을 막는다)


def extract_sources(result: Any) -> list[dict[str, str]]:
    """도구 결과에서 인용 가능한 절 목록. 검색 도구가 아니면 빈 목록."""
    items: list[Any] = []
    if isinstance(result, dict):
        if isinstance(result.get("hits"), list):
            items = result["hits"]
        elif isinstance(result.get("source"), str):
            items = [result]
    elif isinstance(result, list):
        items = result
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        doc, section, sid = it.get("doc"), it.get("section"), it.get("section_id") or ""
        if (not doc or not section) and isinstance(it.get("source"), str) and " > " in it["source"]:
            doc, section = it["source"].split(" > ", 1)
            m = re.match(r"^(\d+(?:\.\d+)*)\s+(.*)$", section)  # "11.3.1 AC Analysis (p.145)" → 절 번호와 제목
            if m:
                sid, section = sid or m.group(1), m.group(2)
            section = re.sub(r"\s*\(p\.\d+\)$", "", section)
        if not doc or not section:
            continue
        key = (str(doc), str(section), str(sid))
        if key in seen:
            continue
        seen.add(key)
        out.append({"doc": key[0], "section": key[1], "section_id": key[2]})
    return out


def normalize_ref(text: str) -> str:
    """공백·괄호·따옴표·마크다운 기호를 빼고 소문자로. 괄호 안 글자는 남긴다("(보상 비율 섹션)" 도 절 이름으로 읽히게). 비교 전용."""
    text = "".join(ch for ch in text if ch not in _QUOTES and ch not in "()（）[]")
    text = re.sub(r"^\s*" + _LABEL + r"\s*[:：]\s*", "", text)  # "출처: 문서" 의 꼬리표
    text = re.sub(r"\s+", "", text)
    return text.strip(" .:;,、。-–").lower()


def _clean(piece: str) -> str:
    piece = re.sub(r"^\s*" + _LABEL + r"\s*[:：]\s*", "", piece)
    return piece.strip(" .)]*" + _QUOTES)


def parse_citations(answer: str) -> list[tuple[str, str]]:
    """답에서 (문서 조각, 절 조각) 목록.
    1) "문서 > 절" 꼴은 "출처:" 꼬리표가 없어도(괄호 안 "(배송 지연 보상 규정 > 보상 비율)" 도) 인용으로 본다.
    2) "출처:" 줄에서 쉼표로 이어진 짧은 조각은 앞 항목의 문서명을 이어받는다. '>' 없는 줄은 ("", 전체) 로 두어 통째로 맞춘다."""
    text = _CODE.sub(" ", answer or "")
    out: list[tuple[str, str]] = []
    for m in _PAIR.finditer(text):
        doc, section = _clean(m.group(1)), _clean(m.group(2))
        if doc and section:
            out.append((doc, section))
    for m in _CITE_LINE.finditer(text):
        line = (m.group(1) or m.group(2) or "").strip()
        before = text[: m.start()].rstrip()
        if before.endswith("(") and ")" in line:  # "(출처: …)" 처럼 괄호 안이면 닫는 괄호까지만
            line = line[: line.index(")")]
        last_doc = ""
        for piece in _SPLIT.split(line):
            raw, piece = piece, _clean(piece)
            if not piece:
                continue
            if ">" in piece:
                doc, section = piece.split(">", 1)
                doc = _clean(doc)
                section = _clean(re.split(r"[”\"’:：]|\s[–-]\s", section)[0])  # 닫는 따옴표·설명 꼬리 앞까지
                last_doc = doc or last_doc
                if last_doc and section:
                    out.append((last_doc, section))
            elif last_doc and len(piece) <= 40 and raw.lstrip()[:1] not in _QUOTES:
                out.append((last_doc, piece))
            elif not last_doc:
                out.append(("", piece))
    seen: set[tuple[str, str]] = set()
    return [c for c in out if not (c in seen or seen.add(c))]


def _partial(a: str, b: str) -> bool:
    return len(a) >= _MIN and len(b) >= _MIN and (a in b or b in a)


def _section_ok(cited: str, section: str, section_id: str) -> bool:
    ns = normalize_ref(section)
    if _partial(cited, ns):
        return True
    if section_id and (
        cited == section_id or (cited.startswith(section_id) and not cited[len(section_id) :][:1].isdigit())
    ):
        return True
    return False


def citation_matches(citation: tuple[str, str], sources: list[dict[str, str]]) -> bool:
    doc_part, sec_part = citation
    nd, ns = normalize_ref(doc_part), normalize_ref(sec_part)
    for src in sources:
        sdoc, ssec, sid = normalize_ref(src["doc"]), src["section"], src.get("section_id") or ""
        if nd:
            if _partial(nd, sdoc) and _section_ok(ns, ssec, sid):
                return True
        elif sdoc in ns and (normalize_ref(ssec) in ns or (sid and sid in ns)):
            return True  # "출처: 배송 지연 보상 규정 보상 비율 절" 처럼 '>' 없이 한 줄
    return False


def citations_ok(answer: str, sources: list[dict[str, str]]) -> bool:
    """인용이 하나 이상 있고, 전부 검색 결과의 절과 맞아야 참. 검색 결과가 없으면 어떤 인용도 거짓."""
    cites = parse_citations(answer)
    return bool(cites) and bool(sources) and all(citation_matches(c, sources) for c in cites)


def sources_from_tool_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for c in tool_calls:
        if c.get("status") == "ok":
            out.extend(c.get("result_sources") or [])
    return out
