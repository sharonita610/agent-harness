"""임베딩 클라이언트(OpenAI 호환 /v1/embeddings) 와 간단한 벡터 유틸. numpy 없이 수십~수백 청크 규모를 다룬다."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Callable, Sequence

DEFAULT_EMBED_BASE_URL = "http://127.0.0.1:8082/v1"
DEFAULT_EMBED_MODEL = "local-embed"

Embedder = Callable[[Sequence[str]], list[list[float]]]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(y * y for y in b)) or 1e-9
    return dot / (na * nb)


class OpenAIEmbedder:
    """llama-server(--embeddings), OpenAI, 기타 호환 서버."""

    def __init__(
        self, base_url: str | None = None, model: str | None = None, api_key: str | None = None, timeout: float = 60
    ):
        from openai import OpenAI

        self.base_url = base_url or os.getenv("HARNESS_EMBED_BASE_URL", DEFAULT_EMBED_BASE_URL)
        self.model = model or os.getenv("HARNESS_EMBED_MODEL", DEFAULT_EMBED_MODEL)
        self._client = OpenAI(
            base_url=self.base_url,
            api_key=api_key or os.getenv("OPENAI_API_KEY") or "no-key",
            timeout=timeout,
            max_retries=0,
        )

    def __call__(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 16):  # 작은 배치로
            resp = self._client.embeddings.create(model=self.model, input=list(texts[i : i + 16]))
            out.extend([d.embedding for d in sorted(resp.data, key=lambda d: d.index)])
        return out


def embedder_available(base_url: str | None = None, timeout: float = 2.0) -> bool:
    import urllib.request

    base = (base_url or os.getenv("HARNESS_EMBED_BASE_URL", DEFAULT_EMBED_BASE_URL)).rstrip("/").removesuffix("/v1")
    if not any(h in base for h in ("127.0.0.1", "localhost", "0.0.0.0", "embed:")):
        return bool(
            os.getenv("OPENAI_API_KEY")
        )  # 호스팅 API(OpenAI 등)는 /health 가 없다. 키가 있으면 쓸 수 있다고 본다
    try:
        with urllib.request.urlopen(base + "/health", timeout=timeout) as r:
            return json.load(r).get("status") == "ok"
    except Exception:
        return False


class EmbeddingCache:
    """청크 텍스트 해시 → 벡터. 문서가 안 바뀌면 재계산하지 않는다."""

    def __init__(self, path: Path | None):
        self.path = path
        self.data: dict[str, list[float]] = {}
        if path and path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                self.data = {}

    @staticmethod
    def cache_key(model: str, text: str) -> str:
        return hashlib.sha1((model + "\n" + text).encode("utf-8")).hexdigest()

    def get_many(self, model: str, texts: Sequence[str], embed: Embedder) -> list[list[float]]:
        keys = [self.cache_key(model, t) for t in texts]
        missing = [i for i, k in enumerate(keys) if k not in self.data]
        if missing:
            vecs = embed([texts[i] for i in missing])
            for i, v in zip(missing, vecs):
                self.data[keys[i]] = v
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self.data), encoding="utf-8")
        return [self.data[k] for k in keys]
