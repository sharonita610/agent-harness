import pytest

from harness.packs.enterprise.seed import build


@pytest.fixture(scope="session")
def seeded_db(tmp_path_factory):
    db = tmp_path_factory.mktemp("data") / "enterprise.db"
    info = build(db)
    return db, info
