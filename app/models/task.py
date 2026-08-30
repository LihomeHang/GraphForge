"""任务状态模型。状态机: pending → parsing → chunking → ontology → extracting → resolving → writing → completed | failed。"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    pending = "pending"
    parsing = "parsing"
    chunking = "chunking"
    ontology = "ontology"
    extracting = "extracting"
    resolving = "resolving"
    writing = "writing"
    completed = "completed"
    failed = "failed"


STAGES: list[str] = [
    "parsing",
    "chunking",
    "ontology",
    "extracting",
    "resolving",
    "writing",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Task(BaseModel):
    task_id: str
    graph_id: str
    status: TaskStatus = TaskStatus.pending
    stage: str = ""
    progress: float = 0.0
    message: str = ""
    error: str = ""
    logs: list[str] = Field(default_factory=list)  # 运行期事件日志（LLM 重试/阶段切换等）
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
