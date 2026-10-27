import os

import pytest

# 테스트는 환경(임베딩 서버 유무)에 흔들리지 않게 BM25 만 쓴다. 하이브리드는 가짜 임베더로 따로 검증.
os.environ.setdefault("HARNESS_HYBRID", "0")
# 이 머신에 등록된 실제 사용자(data/users.json)가 테스트에 섞이지 않게: 없는 파일을 가리켜 개발 모드로 둔다.
os.environ.setdefault("HARNESS_USERS_FILE", "/nonexistent/harness-test-users.json")
os.environ.pop("HARNESS_AUTH", None)
# 회로 팩 테스트는 요약 문서 색인만 쓴다. 매뉴얼 지식 베이스는 test_kb 가 작은 PDF 로 따로 검증한다.
os.environ.setdefault("HARNESS_SPICE_KB", "/nonexistent/harness-test-kb.sqlite")

from harness.packs.enterprise.seed import build


@pytest.fixture(scope="session")
def seeded_db(tmp_path_factory):
    db = tmp_path_factory.mktemp("data") / "enterprise.db"
    info = build(db)
    return db, info
