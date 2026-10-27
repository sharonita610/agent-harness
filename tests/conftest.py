import os

import pytest

# 테스트는 환경(임베딩 서버 유무)에 흔들리지 않게 BM25 만 쓴다. 하이브리드는 가짜 임베더로 따로 검증.
os.environ.setdefault("HARNESS_HYBRID", "0")
# 이 머신에 등록된 실제 사용자(data/users.json)가 테스트에 섞이지 않게: 없는 파일을 가리켜 개발 모드로 둔다.
os.environ.setdefault("HARNESS_USERS_FILE", "/nonexistent/harness-test-users.json")
os.environ.pop("HARNESS_AUTH", None)
# 회로 팩 테스트는 요약 문서 색인만 쓴다. 매뉴얼 지식 베이스는 test_kb 가 작은 PDF 로 따로 검증한다.
os.environ.setdefault("HARNESS_SPICE_KB", "/nonexistent/harness-test-kb.sqlite")
import tempfile as _tempfile
os.environ.setdefault("HARNESS_SPICE_GRAPH", os.path.join(_tempfile.mkdtemp(prefix="harness-graph-"), "experiments.sqlite"))  # 실험 그래프도 이 머신 기록과 분리

_factory_tmp = _tempfile.mkdtemp(prefix="harness-factory-")
os.environ.setdefault("HARNESS_FACTORY_DB", os.path.join(_factory_tmp, "factory.db"))   # 공정 팩 DB 도 이 머신 데이터와 분리
os.environ.setdefault("HARNESS_FACTORY_RUNS", os.path.join(_factory_tmp, "runs"))

from harness.packs.enterprise.seed import build


@pytest.fixture(scope="session")
def seeded_db(tmp_path_factory):
    db = tmp_path_factory.mktemp("data") / "enterprise.db"
    info = build(db)
    return db, info


@pytest.fixture(scope="session")
def factory_db():
    from harness.packs.factory.seed import DEFAULT_DB, build as build_factory
    info = build_factory(DEFAULT_DB)
    return DEFAULT_DB, info
