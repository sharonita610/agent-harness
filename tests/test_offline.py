"""오프라인(에어갭) 모드, ADR-027: 바깥 주소 거부 + 소켓을 막은 채 평가 한 바퀴."""

from __future__ import annotations

import socket

import pytest
import yaml

from harness.agent.types import LLMResponse, ToolCall
from harness.evals import runner
from harness.offline import OfflineViolation, allowed_host, check_url, is_offline
from tests.test_loop import FakeProvider


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setenv("HARNESS_OFFLINE", "1")
    monkeypatch.delenv("HARNESS_OFFLINE_ALLOW", raising=False)
    assert is_offline()


def test_allowed_hosts_are_loopback_private_and_intranet_names():
    for h in [
        "localhost",
        "127.0.0.1",
        "::1",
        "10.0.0.5",
        "172.16.3.9",
        "192.168.1.20",
        "169.254.1.1",
        "llm-box",
        "fd00::1",
    ]:
        assert allowed_host(h), h
    for h in ["api.openai.com", "cdn.jsdelivr.net", "8.8.8.8", "1.1.1.1", "93.184.216.34", "2606:4700::1", ""]:
        assert not allowed_host(h), h


def test_check_url_passes_through_when_not_offline(monkeypatch):
    monkeypatch.delenv("HARNESS_OFFLINE", raising=False)
    assert check_url("https://api.openai.com/v1", "x") == "https://api.openai.com/v1"


def test_offline_rejects_outside_urls_and_allows_exceptions(offline, monkeypatch):
    assert check_url("http://127.0.0.1:8080/v1", "모델") == "http://127.0.0.1:8080/v1"
    assert check_url("http://llm-box:8080/v1", "모델") == "http://llm-box:8080/v1"
    with pytest.raises(OfflineViolation, match="모델 서버"):
        check_url("https://api.openai.com/v1", "모델 서버")
    monkeypatch.setenv("HARNESS_OFFLINE_ALLOW", "models.corp.local, .lab.internal")
    assert check_url("http://models.corp.local/v1", "x") and check_url("http://gpu7.lab.internal:8080/v1", "x")
    with pytest.raises(OfflineViolation):
        check_url("http://evil.lab.internal.example.com/v1", "x")


def test_offline_refuses_external_providers_but_allows_local_routing(offline, monkeypatch):
    from harness.agent.providers import RoutingProvider, make_provider

    monkeypatch.setenv("OPENAI_API_KEY", "x")
    for spec in [
        "openai:gpt-5-mini",
        "anthropic:claude-sonnet-4-5",
        "compat:https://api.example.com/v1|m",
        "route:local->openai:gpt-5-mini",
    ]:
        with pytest.raises(OfflineViolation):
            make_provider(spec)
    assert make_provider("local").name == "local"
    assert isinstance(make_provider("route:local->compat:http://llm-big:8080/v1|big"), RoutingProvider)


def test_offline_embedder_and_otlp(offline, monkeypatch):
    from harness.retrieval.embeddings import OpenAIEmbedder, embedder_available

    assert embedder_available("https://api.openai.com/v1") is False  # 탐색조차 하지 않는다
    with pytest.raises(OfflineViolation, match="임베딩"):
        OpenAIEmbedder(base_url="https://api.openai.com/v1")
    from harness.observability.tracer import OTelExporter

    with pytest.raises(OfflineViolation, match="트레이스"):
        OTelExporter(endpoint="https://otlp.vendor.example/v1/traces")


class _Blocked(RuntimeError):
    pass


@pytest.fixture
def socket_guard(monkeypatch):
    """루프백이 아닌 곳으로 연결하면 즉시 실패시키고 시도를 기록한다. 바깥으로 나가는 호출이 없다는 증명."""
    attempts: list[str] = []
    real_connect = socket.socket.connect

    def guarded(self, address, *a, **k):
        host = address[0] if isinstance(address, tuple) else str(address)
        if host not in ("127.0.0.1", "::1", "localhost") and not str(host).startswith("/"):
            attempts.append(str(host))
            raise _Blocked(f"바깥 연결 시도: {host}")
        return real_connect(self, address, *a, **k)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    monkeypatch.setattr(socket, "create_connection", lambda addr, *a, **k: (_ for _ in ()).throw(_Blocked(str(addr))))
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: (_ for _ in ()).throw(_Blocked(f"DNS: {host}")))
    return attempts


def test_eval_round_trip_makes_no_outbound_connection(offline, socket_guard, seeded_db, tmp_path, monkeypatch):
    """회사 팩 평가(도구·DB·문서 검색·채점·결과 저장)가 소켓 가드 아래에서 통과한다. 모델은 대본(로컬 서버 대역)."""
    db, _ = seeded_db
    tasks = [
        {
            "id": "q01",
            "type": "query",
            "question": "지연 몇 건?",
            "truth_sql": "SELECT COUNT(*) FROM orders WHERE status='지연'",
            "expect_tools_any": ["run_sql"],
        },
        {
            "id": "d01",
            "type": "doc",
            "question": "7일 지연 보상?",
            "answer_contains_any": ["5%"],
            "expect_tools_any": ["search_docs"],
        },
    ]
    tp = tmp_path / "t.yaml"
    tp.write_text(yaml.safe_dump(tasks, allow_unicode=True), encoding="utf-8")
    truth = (
        __import__("sqlite3").connect(str(db)).execute("SELECT COUNT(*) FROM orders WHERE status='지연'").fetchone()[0]
    )
    fake = FakeProvider(
        [
            LLMResponse(
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="run_sql",
                        arguments={"sql": "SELECT COUNT(*) AS n FROM orders WHERE status='지연'"},
                    )
                ]
            ),
            LLMResponse(text=f"지연 주문은 {truth}건입니다"),
            LLMResponse(tool_calls=[ToolCall(id="c2", name="search_docs", arguments={"query": "지연 보상 비율"})]),
            LLMResponse(text="6~10일 지연은 5% 입니다"),
        ]
    )
    monkeypatch.setattr(runner, "make_provider", lambda spec, **kw: fake)
    monkeypatch.setattr(runner, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setenv("HARNESS_HYBRID", "0")
    out = runner.run_eval(provider="local", n=1, tasks_path=tp, db_path=db, tag="offline")
    assert out["summary"]["pass_rate"] == 1.0 and socket_guard == []
