"""오프라인(에어갭) 모드: HARNESS_OFFLINE=1 이면 루프백·사설망 밖으로 나가는 주소를 코드가 거부한다.

은행·병원·공장처럼 데이터를 밖으로 내보낼 수 없는 곳에서는 "설정을 잘못 했을 때도 안 나간다" 가 중요하다.
모델 서버·임베딩 서버·트레이스 내보내기 주소를 만들 때마다 check_url 을 거치고, 외부 공급자(openai:, anthropic:)는
아예 만들지 않는다. 허용: localhost·127/8·::1, 10/8·172.16/12·192.168/16, 169.254/16, fc00::/7, 점 없는 사내 호스트명,
HARNESS_OFFLINE_ALLOW 에 적은 호스트(쉼표, 접미사는 '.corp.local' 처럼 점으로 시작).
"""

from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlparse


class OfflineViolation(RuntimeError):
    """오프라인 모드에서 밖으로 나가는 주소를 쓰려 했다."""


def is_offline() -> bool:
    return os.getenv("HARNESS_OFFLINE", "").strip().lower() in ("1", "true", "yes", "on")


def _allow_list() -> list[str]:
    return [h.strip().lower() for h in os.getenv("HARNESS_OFFLINE_ALLOW", "").split(",") if h.strip()]


def allowed_host(host: str) -> bool:
    """루프백·사설망·사내 호스트명만 허용한다. 공개 IP 와 점이 있는 바깥 도메인은 거부."""
    h = (host or "").strip().lower().strip("[]")
    if not h:
        return False
    if h in ("localhost", "localhost.localdomain"):
        return True
    for entry in _allow_list():
        if h == entry or (entry.startswith(".") and h.endswith(entry)):
            return True
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return "." not in h  # 'llm-box' 같은 사내 짧은 이름은 허용, 'api.openai.com' 은 거부
    return ip.is_loopback or ip.is_private or ip.is_link_local


def check_url(url: str | None, what: str) -> str | None:
    """오프라인 모드가 아니면 그대로 돌려준다. 오프라인 모드에서 바깥 주소면 OfflineViolation."""
    if not is_offline() or not url:
        return url
    host = urlparse(url if "://" in url else "http://" + url).hostname or ""
    if not allowed_host(host):
        raise OfflineViolation(
            f"오프라인 모드(HARNESS_OFFLINE=1)에서는 {what} 주소 {url!r} 를 쓸 수 없습니다. "
            "루프백·사설망·사내 호스트만 허용합니다(HARNESS_OFFLINE_ALLOW 로 예외 추가)."
        )
    return url


def check_provider_kind(kind: str, spec: str) -> None:
    if is_offline() and kind in ("openai", "anthropic"):
        raise OfflineViolation(
            f"오프라인 모드(HARNESS_OFFLINE=1)에서는 외부 모델 공급자 {spec!r} 를 쓸 수 없습니다. local / compat(사내 주소) / route:local->local:… 만 됩니다."
        )
