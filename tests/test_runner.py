import httpx
import pytest

from app.config import Config
from app.llm.embeddings import MockEmbeddingClient
from app.models.task import Task, TaskStatus
from app.pipeline import runner
from app.pipeline.runner import BuildParams, Services
from app.storage.tasks import TaskStore


class _GraphStore:
    def __init__(self):
        self.statuses = []

    async def set_graph_status(self, graph_id, status, ontology_json=None):
        self.statuses.append((graph_id, status))


@pytest.mark.asyncio
async def test_run_build_records_timeout_type_and_failed_graph_status(tmp_path, monkeypatch):
    task_store = TaskStore(tmp_path / "tasks.db")
    graph_store = _GraphStore()
    task = Task(task_id="task-1", graph_id="graph-1")
    await task_store.create_task(task)

    async def _timeout(*_args, **_kwargs):
        raise httpx.ReadTimeout("")

    monkeypatch.setattr(runner, "_run", _timeout)
    services = Services(
        config=Config(llm_provider="mock", neo4j_password="x"),
        llm=None,
        embeddings=MockEmbeddingClient(),
        neo4j=graph_store,
        qdrant=None,
        tasks=task_store,
    )

    await runner.run_build(
        task.task_id,
        BuildParams(graph_id=task.graph_id, files=[]),
        services,
    )

    saved = await task_store.get_task(task.task_id)
    await task_store.close()
    assert saved is not None
    assert saved.status is TaskStatus.failed
    assert saved.error == "ReadTimeout"
    assert graph_store.statuses == [(task.graph_id, "failed")]


def test_prechunked_documents_remain_one_chunk_per_file():
    params = BuildParams(
        graph_id="graph-1",
        files=[(b"first chunk", "000001.txt"), (b"second chunk", "000002.txt")],
        documents_are_chunks=True,
    )

    text, chunks = runner._parse_and_chunk_documents(
        params,
        Config(neo4j_password="x", chunk_size=200, chunk_overlap=50),
    )

    assert text == "first chunk\n\nsecond chunk"
    assert chunks == ["first chunk", "second chunk"]
