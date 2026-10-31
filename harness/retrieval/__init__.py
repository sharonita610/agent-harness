"""도메인 중립 문서 검색(BM25 + 임베딩 하이브리드). 어떤 팩이든 마크다운 폴더만 주면 쓴다."""

from .embeddings import EmbeddingCache, OpenAIEmbedder, cosine, embedder_available
from .search import Chunk, DocIndex, chunk_markdown, rrf_fuse, split_long

__all__ = [
    "Chunk",
    "DocIndex",
    "EmbeddingCache",
    "OpenAIEmbedder",
    "chunk_markdown",
    "cosine",
    "embedder_available",
    "rrf_fuse",
    "split_long",
]
