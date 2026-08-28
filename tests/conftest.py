"""pytest 公共 fixture：mock LLM / mock embeddings / 临时 SQLite / mock config。"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.config import Config
from app.llm.client import MockLLMClient
from app.llm.embeddings import MockEmbeddingClient
from app.storage.tasks import TaskStore


@pytest.fixture
def config() -> Config:
    return Config(
        llm_provider="mock",
        neo4j_password="test",
        data_dir=tempfile.mkdtemp(prefix="graphforge-test-"),
    )


@pytest.fixture
def mock_llm() -> MockLLMClient:
    return MockLLMClient()


@pytest.fixture
def mock_embeddings() -> MockEmbeddingClient:
    return MockEmbeddingClient(dim=32)


@pytest.fixture
async def task_store(config) -> TaskStore:
    store = TaskStore(Path(config.data_dir) / "tasks.db")
    yield store
    await store.close()
