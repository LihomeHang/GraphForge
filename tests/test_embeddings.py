import httpx
import pytest

from app.config import Config
from app.llm.embeddings import (
    EmbeddingClient,
    FallbackEmbeddingClient,
    LocalHashEmbeddingClient,
    OpenAIEmbeddingClient,
    build_embedding_client,
)


class _Response:
    def __init__(self, texts):
        self._texts = texts

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "data": [
                {"index": index, "embedding": [float(text)]}
                for index, text in enumerate(self._texts)
            ]
        }


class _RecordingClient:
    def __init__(self, *, fail_once=False):
        self.calls = []
        self.fail_once = fail_once

    async def post(self, _path, json):
        texts = list(json["input"])
        self.calls.append(texts)
        if self.fail_once:
            self.fail_once = False
            raise httpx.ReadTimeout("slow upstream")
        return _Response(texts)

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_embedding_client_batches_large_inputs_in_order():
    config = Config(
        neo4j_password="x",
        embedding_batch_size=2,
        embedding_concurrency=2,
    )
    client = OpenAIEmbeddingClient(config)
    await client._client.aclose()
    transport = _RecordingClient()
    client._client = transport

    vectors = await client.embed(["0", "1", "2", "3", "4"])

    assert vectors == [[0.0], [1.0], [2.0], [3.0], [4.0]]
    assert sorted(transport.calls) == [["0", "1"], ["2", "3"], ["4"]]


@pytest.mark.asyncio
async def test_embedding_client_retries_transient_timeout(monkeypatch):
    config = Config(
        neo4j_password="x",
        embedding_batch_size=8,
        embedding_max_retries=1,
    )
    client = OpenAIEmbeddingClient(config)
    await client._client.aclose()
    transport = _RecordingClient(fail_once=True)
    client._client = transport

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.llm.embeddings.asyncio.sleep", _no_sleep)

    assert await client.embed(["7"]) == [[7.0]]
    assert transport.calls == [["7"], ["7"]]


def _cosine(a, b):
    return sum(x * y for x, y in zip(a, b))


@pytest.mark.asyncio
async def test_local_hash_embeddings_are_deterministic_and_lexical():
    client = LocalHashEmbeddingClient(dim=128)

    vectors = await client.embed(["Lambda 架构", "Lambda 架构", "Lambda架构设计", "数据库索引"])

    assert vectors[0] == vectors[1]
    assert len(vectors[0]) == 128
    assert _cosine(vectors[0], vectors[2]) > _cosine(vectors[0], vectors[3])


class _PrimaryEmbedding(EmbeddingClient):
    def __init__(self, *, fail=False, dim=3):
        self.fail = fail
        self.dimension = dim
        self.calls = 0

    async def embed(self, texts):
        self.calls += 1
        if self.fail:
            raise httpx.ReadTimeout("remote unavailable")
        return [[1.0] + [0.0] * (self.dimension - 1) for _ in texts]

    async def dim(self):
        if self.fail:
            raise httpx.ReadTimeout("remote unavailable")
        return self.dimension


@pytest.mark.asyncio
async def test_fallback_switches_permanently_after_remote_failure():
    primary = _PrimaryEmbedding(fail=True)
    client = FallbackEmbeddingClient(primary, LocalHashEmbeddingClient(dim=64))

    first = await client.embed(["第一次"])
    second = await client.embed(["第二次"])

    assert len(first[0]) == 64 and len(second[0]) == 64
    assert primary.calls == 1
    assert client.fallback_active is True


@pytest.mark.asyncio
async def test_fallback_keeps_remote_dimension_when_switching_later():
    primary = _PrimaryEmbedding(dim=3)
    client = FallbackEmbeddingClient(primary, LocalHashEmbeddingClient(dim=64))
    assert len((await client.embed(["remote"]))[0]) == 3

    primary.fail = True
    fallback_vectors = await client.embed(["local"])

    assert len(fallback_vectors[0]) == 3


@pytest.mark.asyncio
async def test_build_embedding_client_can_force_local_mode_without_http(monkeypatch):
    def _forbid_http(*_args, **_kwargs):
        raise AssertionError("local mode must not construct an HTTP client")

    monkeypatch.setattr("app.llm.embeddings.httpx.AsyncClient", _forbid_http)
    client = build_embedding_client(
        Config(
            llm_provider="openai",
            neo4j_password="x",
            embedding_provider="local",
            local_embedding_dim=256,
        )
    )
    assert isinstance(client, LocalHashEmbeddingClient)
    assert len((await client.embed(["offline"]))[0]) == 256
