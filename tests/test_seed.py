from harness.packs.enterprise.seed import build


def test_seed_is_deterministic(tmp_path):
    a = build(tmp_path / "a.db")
    b = build(tmp_path / "b.db")
    assert a["sha256_16"] == b["sha256_16"]
    assert a["counts"]["orders"] == 3000
    assert a["counts"]["customers"] == 50
    assert a["counts"]["inventory"] == 400
