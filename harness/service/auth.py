"""인증·인가: API 키 사용자와 역할. 키는 해시로만 저장한다.

- 사용자 파일(data/users.json)이 있거나 HARNESS_AUTH=1 이면 인증 필수. 없으면 개발 모드(익명 'dev', 모든 역할) 로 열되 /me 에 표시한다.
- 역할: viewer(조회) < operator(질문·잡) < approver(결재) < admin(사용자 관리·감사 조회)
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from fastapi import Depends, HTTPException, Request

ROLES = ["viewer", "operator", "approver", "admin"]
RANK = {r: i for i, r in enumerate(ROLES)}
DEFAULT_USERS_PATH = Path(__file__).resolve().parents[2] / "data" / "users.json"


@dataclass
class User:
    name: str
    role: str
    anonymous: bool = False
    scope: dict | None = None   # 데이터 범위 (packs.enterprise.scope.Scope.parse 형식). None = 전체

    def has(self, role: str) -> bool:
        return RANK[self.role] >= RANK[role]


@dataclass
class UserStore:
    path: Path | None = None
    users: dict[str, dict] = field(default_factory=dict)  # key_hash -> {name, role}
    required: bool = False

    @classmethod
    def load(cls, path: Path | None = None, required: bool | None = None) -> "UserStore":
        path = path or Path(os.getenv("HARNESS_USERS_FILE", str(DEFAULT_USERS_PATH)))
        users: dict[str, dict] = {}
        if path.exists():
            for u in json.loads(path.read_text(encoding="utf-8")).get("users", []):
                users[u["key_hash"]] = {"name": u["name"], "role": u["role"], "scope": u.get("scope")}
        if required is None:
            required = bool(users) or os.getenv("HARNESS_AUTH") == "1"
        return cls(path=path, users=users, required=required)

    @staticmethod
    def hash_key(key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def add(self, name: str, role: str, scope: dict | None = None) -> str:
        """사용자를 추가하고 평문 키를 한 번만 돌려준다(파일엔 해시만). scope: {"customer_ids": [...], "region": "부산"}"""
        if role not in ROLES:
            raise ValueError(f"역할은 {ROLES} 중 하나")
        key = "hk_" + secrets.token_urlsafe(24)
        self.users[self.hash_key(key)] = {"name": name, "role": role, "scope": scope}
        self.required = True
        self.save()
        return key

    def remove(self, name: str) -> int:
        before = len(self.users)
        self.users = {h: u for h, u in self.users.items() if u["name"] != name}
        self.save()
        return before - len(self.users)

    def save(self) -> None:
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"users": [{"key_hash": h, **u} for h, u in self.users.items()]}, ensure_ascii=False, indent=2), encoding="utf-8")

    def authenticate(self, key: str | None) -> User | None:
        if key is None:
            return None
        u = self.users.get(self.hash_key(key))
        return User(u["name"], u["role"], scope=u.get("scope")) if u else None

    def list(self) -> list[dict]:
        return [{"name": u["name"], "role": u["role"], "scope": u.get("scope")} for u in self.users.values()]


def _extract_key(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key") or request.query_params.get("api_key")


def make_dependencies(user_store: UserStore) -> tuple[Callable, Callable[[str], Callable]]:
    """(current_user 의존성, require(role) 의존성 팩토리)"""

    def current_user(request: Request) -> User:
        key = _extract_key(request)
        user = user_store.authenticate(key)
        if user:
            return user
        if not user_store.required:
            return User("dev", "admin", anonymous=True)
        raise HTTPException(401, "API 키가 필요합니다. Authorization: Bearer <키> 로 보내세요.")

    def require(role: str) -> Callable:
        def dep(user: User = Depends(current_user)) -> User:
            if not user.has(role):
                raise HTTPException(403, f"'{role}' 역할이 필요합니다. 현재 역할: {user.role}")
            return user
        return dep

    return current_user, require
