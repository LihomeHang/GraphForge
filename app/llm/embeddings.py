"""Embedding 客户端（OpenAI 兼容 /embeddings），维度自动探测。"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
import unicodedata
from abc import ABC, abstractmethod

import httpx

from app.config import Config
from app.llm.client import emit_task_log

logger = logging.getLogger("graphforge.embeddings")


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
        self._semaphore = asyncio.Semaphore(config.embedding_concurrency)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        async def _embed_batch(batch: list[str]) -> list[list[float]]:
            for attempt in range(self.config.embedding_max_retries + 1):
                try:
                    resp = await self._client.post(
                        "/embeddings",
                        json={"model": self.config.embedding_model, "input": batch},
                    )
                    resp.raise_for_status()
                    items = sorted(resp.json()["data"], key=lambda d: d["index"])
                    if len(items) != len(batch):
                        raise ValueError(
                            f"Embedding 返回数量不匹配: 期望 {len(batch)}，实际 {len(items)}"
                        )
                    return [item["embedding"] for item in items]
                except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as exc:
                    retryable = not isinstance(exc, httpx.HTTPStatusError) or (
                        exc.response.status_code == 429 or exc.response.status_code >= 500
                    )
                    if not retryable or attempt >= self.config.embedding_max_retries:
                        raise
                    delay = min(2**attempt, 8)
                    logger.warning(
                        "Embedding 批次失败，%ss 后重试 (%s/%s): %s",
                        delay,
                        attempt + 1,
                        self.config.embedding_max_retries,
                        type(exc).__name__,
                    )
                    await asyncio.sleep(delay)
            raise RuntimeError("Embedding 重试循环异常退出")

        batch_size = self.config.embedding_batch_size
        batches = [texts[i : i + batch_size] for i in range(0, len(texts), batch_size)]

        async def _work(batch: list[str]) -> list[list[float]]:
            async with self._semaphore:
                return await _embed_batch(batch)

        batch_vectors = await asyncio.gather(*(_work(batch) for batch in batches))
        return [vector for vectors in batch_vectors for vector in vectors]

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


class LocalHashEmbeddingClient(EmbeddingClient):
    """纯本地特征哈希向量：字符 n-gram + 单词，无模型、无网络、结果可复现。"""

    _TOKEN_RE = re.compile(r"[a-z0-9_]+|[\u3400-\u9fff]+", re.IGNORECASE)

    def __init__(self, dim: int = 1024):
        if dim <= 0:
            raise ValueError("本地向量维度必须 > 0")
        self._dim = dim

    def set_dim(self, dim: int) -> None:
        if dim > 0:
            self._dim = dim

    @classmethod
    def _tokens(cls, text: str) -> list[str]:
        normalized = unicodedata.normalize("NFKC", text).casefold()
        tokens: list[str] = []
        for part in cls._TOKEN_RE.findall(normalized):
            if part.isascii():
                tokens.append(f"w:{part}")
                continue
            chars = list(part)
            for size in (1, 2, 3):
                tokens.extend(
                    f"c{size}:{''.join(chars[start:start + size])}"
                    for start in range(0, len(chars) - size + 1)
                )
        return tokens

    def _vec(self, text: str) -> list[float]:
        vector = [0.0] * self._dim
        for token in self._tokens(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self._dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(text) for text in texts]

    async def dim(self) -> int:
        return self._dim


class FallbackEmbeddingClient(EmbeddingClient):
    """远程失败后永久切换到本地，避免同一进程内混用两个向量空间。"""

    def __init__(self, primary: EmbeddingClient, fallback: LocalHashEmbeddingClient):
        self.primary = primary
        self.fallback = fallback
        self.fallback_active = False

    def _activate_fallback(self, exc: Exception) -> None:
        if self.fallback_active:
            return
        self.fallback_active = True
        detail = str(exc).strip() or type(exc).__name__
        message = f"远程 Embedding 不可用，已切换本地哈希向量: {detail}"
        logger.warning(message)
        emit_task_log(message)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self.fallback_active:
            return await self.fallback.embed(texts)
        try:
            vectors = await self.primary.embed(texts)
            if vectors:
                self.fallback.set_dim(len(vectors[0]))
            return vectors
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            self._activate_fallback(exc)
            return await self.fallback.embed(texts)

    async def dim(self) -> int:
        if self.fallback_active:
            return await self.fallback.dim()
        try:
            dimension = await self.primary.dim()
            self.fallback.set_dim(dimension)
            return dimension
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            self._activate_fallback(exc)
            return await self.fallback.dim()

    async def close(self) -> None:
        await self.primary.close()
        await self.fallback.close()


def build_embedding_client(config: Config) -> EmbeddingClient:
    if config.llm_provider == "mock":
        return MockEmbeddingClient(dim=config.embedding_dim or 32)
    local_dim = (
        config.embedding_dim
        if config.embedding_provider == "auto" and config.embedding_dim
        else config.local_embedding_dim
    )
    local = LocalHashEmbeddingClient(dim=local_dim)
    if config.embedding_provider == "local":
        return local
    remote = OpenAIEmbeddingClient(config)
    if config.embedding_provider == "remote":
        return remote
    return FallbackEmbeddingClient(remote, local)
