"""Embedding 客户端（OpenAI 兼容 /embeddings），维度自动探测。"""
from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod

import httpx

from app.config import Config


class EmbeddingClient(ABC):
    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        ...

    async def embed_one(self, text: str) -> list[float]:
        vecs = await self.embed([text])
        return vecs[0]

    @abstractmethod
    async def dim(self) -> int:
        """返回向量维度（首次调用可触发自动探测并缓存）。"""

    async def close(self) -> None:  # noqa: B027
        return None


class OpenAIEmbeddingClient(EmbeddingClient):
    def __init__(self, config: Config):
        self.config = config
        base_url = (config.embedding_base_url or config.llm_base_url).rstrip("/")
        api_key = config.embedding_api_key or config.llm_api_key
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(120.0, connect=10.0),
        )
        self._dim: int | None = config.embedding_dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        resp = await self._client.post(
            "/embeddings",
            json={"model": self.config.embedding_model, "input": texts},
        )
        resp.raise_for_status()
        data = resp.json()
        items = sorted(data["data"], key=lambda d: d["index"])
        return [item["embedding"] for item in items]

    async def dim(self) -> int:
        if self._dim is None:
            vec = await self.embed_one("dimension probe")
            self._dim = len(vec)
        return self._dim

    async def close(self) -> None:
        await self._client.aclose()


class MockEmbeddingClient(EmbeddingClient):
    """测试用确定性向量：文本 hash 映射到固定维度单位向量（余弦可比较）。"""

    def __init__(self, dim: int = 32):
        self._dim = dim

    @staticmethod
    def _hash(text: str) -> int:
        return int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16)

    def _vec(self, text: str) -> list[float]:
        h = self._hash(text)
        vec: list[float] = []
        for i in range(self._dim):
            # 确定性伪随机位 → [-1, 1]
            v = ((h >> (i % 60)) & 1) * 2 - 1
            v += (((h >> ((i * 7) % 60)) & 1) * 2 - 1) * 0.5
            vec.append(v / 1.5)
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    async def dim(self) -> int:
        return self._dim


def build_embedding_client(config: Config) -> EmbeddingClient:
    if config.llm_provider == "mock":
        return MockEmbeddingClient(dim=config.embedding_dim or 32)
    return OpenAIEmbeddingClient(config)
