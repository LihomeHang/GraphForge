"""SQLite 任务状态存储（asyncio 后台任务持久化）。"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models.task import Task, TaskStatus


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    graph_id TEXT NOT NULL,
    status TEXT NOT NULL,
    stage TEXT DEFAULT '',
    progress REAL DEFAULT 0,
    message TEXT DEFAULT '',
    error TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_graph ON tasks(graph_id);

CREATE TABLE IF NOT EXISTS extract_cache (
    chunk_hash TEXT PRIMARY KEY,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS extract_runs (
    task_id TEXT PRIMARY KEY,
    graph_id TEXT NOT NULL,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class TaskStore:
    """SQLite 任务状态 + 块级抽取缓存。同步 sqlite3 + asyncio 锁串行化。"""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    async def close(self) -> None:
        async with self._lock:
            self._conn.close()

    # ---- 任务 CRUD ----

    async def create_task(self, task: Task) -> None:
        async with self._lock:
            self._conn.execute(
                "INSERT INTO tasks (task_id, graph_id, status, stage, progress, message, error, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task.task_id,
                    task.graph_id,
                    task.status.value,
                    task.stage,
                    task.progress,
                    task.message,
                    task.error,
                    task.created_at,
                    task.updated_at,
                ),
            )
            self._conn.commit()

    def _row_to_task(self, row: sqlite3.Row) -> Task:
        return Task(
            task_id=row["task_id"],
            graph_id=row["graph_id"],
            status=TaskStatus(row["status"]),
            stage=row["stage"],
            progress=row["progress"],
            message=row["message"],
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def get_task(self, task_id: str) -> Task | None:
        async with self._lock:
            cur = self._conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
            row = cur.fetchone()
            if row is None:
                return None
            return self._row_to_task(row)

    async def get_tasks_for_graph(self, graph_id: str) -> list[Task]:
        async with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM tasks WHERE graph_id = ? ORDER BY created_at DESC", (graph_id,)
            )
            return [self._row_to_task(r) for r in cur.fetchall()]

    async def update_task(self, task: Task) -> None:

        task.updated_at = _now()
        async with self._lock:
            self._conn.execute(
                "UPDATE tasks SET status = ?, stage = ?, progress = ?, message = ?, error = ?, updated_at = ? "
                "WHERE task_id = ?",
                (
                    task.status.value,
                    task.stage,
                    task.progress,
                    task.message,
                    task.error,
                    task.updated_at,
                    task.task_id,
                ),
            )
            self._conn.commit()

    async def mark_interrupted_on_startup(self) -> int:
        """启动时把非终态任务标记为 failed（服务重启导致中断）。返回数量。"""
        async with self._lock:
            cur = self._conn.execute(
                "UPDATE tasks SET status = ?, error = ?, updated_at = ? "
                "WHERE status NOT IN ('completed', 'failed')",
                (TaskStatus.failed.value, "服务重启导致任务中断", _now()),
            )
            self._conn.commit()
            return cur.rowcount

    # ---- 块级抽取缓存（幂等重跑）----

    async def get_extract_cache(self, chunk_hash: str) -> dict[str, Any] | None:
        async with self._lock:
            cur = self._conn.execute(
                "SELECT result_json FROM extract_cache WHERE chunk_hash = ?", (chunk_hash,)
            )
            row = cur.fetchone()
            if row is None:
                return None
            return json.loads(row[0])

    async def put_extract_cache(self, chunk_hash: str, result: dict[str, Any]) -> None:

        async with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO extract_cache (chunk_hash, result_json, created_at) VALUES (?, ?, ?)",
                (chunk_hash, json.dumps(result, ensure_ascii=False), _now()),
            )
            self._conn.commit()

    # ---- 管道中间状态（重启后续跑用）----

    async def save_run_state(self, task_id: str, graph_id: str, state: dict[str, Any]) -> None:

        async with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO extract_runs (task_id, graph_id, state_json, updated_at) VALUES (?, ?, ?, ?)",
                (task_id, graph_id, json.dumps(state, ensure_ascii=False), _now()),
            )
            self._conn.commit()

    async def load_run_state(self, task_id: str) -> dict[str, Any] | None:
        async with self._lock:
            cur = self._conn.execute(
                "SELECT state_json FROM extract_runs WHERE task_id = ?", (task_id,)
            )
            row = cur.fetchone()
            if row is None:
                return None
            return json.loads(row[0])

    async def delete_run_state(self, task_id: str) -> None:
        async with self._lock:
            self._conn.execute("DELETE FROM extract_runs WHERE task_id = ?", (task_id,))
            self._conn.commit()
