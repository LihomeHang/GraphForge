import pytest

from app.storage.tasks import TaskStore


@pytest.mark.asyncio
async def test_preview_results_survive_store_reopen(tmp_path):
    db_path = tmp_path / "tasks.db"
    store = TaskStore(db_path)
    await store.reset_preview("graph-1", "task-1")
    await store.put_preview_result(
        "graph-1",
        "task-1",
        1,
        {"entities": [{"name": "B", "type": "Person"}], "relations": []},
    )
    await store.put_preview_result(
        "graph-1",
        "task-1",
        0,
        {"entities": [{"name": "A", "type": "Person"}], "relations": []},
    )
    await store.close()

    reopened = TaskStore(db_path)
    results = await reopened.get_preview_results("graph-1")
    await reopened.close()

    assert [item["entities"][0]["name"] for item in results] == ["A", "B"]


@pytest.mark.asyncio
async def test_reset_preview_removes_previous_task_results(tmp_path):
    store = TaskStore(tmp_path / "tasks.db")
    await store.reset_preview("graph-1", "task-1")
    await store.put_preview_result(
        "graph-1", "task-1", 0, {"entities": [], "relations": []}
    )

    await store.reset_preview("graph-1", "task-2")
    results = await store.get_preview_results("graph-1")
    await store.close()

    assert results == []
