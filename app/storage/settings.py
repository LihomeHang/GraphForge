"""SQLite Web 端设置存储（key-value，键为环境变量名风格）。"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SettingsStore:
    """Web 端运行时设置覆盖项（优先级高于环境变量/.env）。

    与 TaskStore 同风格：同步 sqlite3 + asyncio 锁串行化；独立 db 文件避免锁交叉。
    """

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

    async def load_all(self) -> dict[str, str]:
        """读取全部覆盖项（环境变量名 -> 值）。"""
        async with self._lock:
            cur = self._conn.execute("SELECT key, value FROM settings")
            return {row["key"]: row["value"] for row in cur.fetchall()}

    async def update(self, values: dict[str, str]) -> None:
        """合并写入：仅 upsert 提供的 key；空值 key 忽略（表示不修改）。"""
        values = {k: v for k, v in values.items() if v}
        if not values:
            return
        async with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                [(k, v, _now()) for k, v in values.items()],
            )
            self._conn.commit()
