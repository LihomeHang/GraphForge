"""构建管道 runner：解析 → 切块 → 本体 → 抽取 → 消歧 → 写入。

以 asyncio 后台任务执行；任务状态写 SQLite；块级抽取结果缓存于 SQLite（幂等重跑）。
"""
from __future__ import annotations

import asyncio
import logging
import uuid as _uuid
from dataclasses import dataclass
from datetime import datetime

from app.config import Config
from app.llm.client import LLMClient, emit_task_log, set_task_log_sink
from app.llm.embeddings import EmbeddingClient
from app.models.ontology import Ontology
from app.models.task import Task, TaskStatus
from app.pipeline import chunker, extractor, ontology as ontology_mod, parser, resolver, writer
from app.storage.neo4j_store import Neo4jStore
from app.storage.qdrant_store import QdrantStore
from app.storage.tasks import TaskStore

logger = logging.getLogger("graphforge.pipeline")


class TaskRegistry:
    """内存任务注册表：task_id -> asyncio.Task。"""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._graph_of: dict[str, str] = {}

    def register(self, task_id: str, coro, graph_id: str = "") -> None:
        self._tasks[task_id] = asyncio.create_task(coro, name=f"build-{task_id}")
        self._graph_of[task_id] = graph_id

    def get(self, task_id: str) -> asyncio.Task | None:
        return self._tasks.get(task_id)

    def is_running(self, task_id: str) -> bool:
        t = self._tasks.get(task_id)
        return t is not None and not t.done()

    def has_running(self) -> bool:
        """是否有未完成的构建任务（用于设置热更新互斥）。"""
        return any(not t.done() for t in self._tasks.values())

    def has_running_for(self, graph_id: str) -> bool:
        """指定图谱是否有未完成的构建任务（防止同图重复构建/重试）。"""
        return any(
            self._graph_of.get(tid) == graph_id and not t.done()
            for tid, t in self._tasks.items()
        )


@dataclass
class BuildParams:
    graph_id: str
    # 多文件支持：[(bytes, filename), ...]，解析后按上传顺序拼接
    files: list[tuple[bytes, str]]
    purpose: str = ""
    ontology: dict | None = None  # 内联本体（来自同步生成结果）
    chunk_size: int | None = None
    chunk_overlap: int | None = None


@dataclass
class Services:
    config: Config
    llm: LLMClient
    embeddings: EmbeddingClient
    neo4j: Neo4jStore
    qdrant: QdrantStore
    tasks: TaskStore
    registry: "TaskRegistry" = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.registry is None:
            self.registry = TaskRegistry()


async def run_build(task_id: str, params: BuildParams, svc: Services) -> None:
    """执行完整构建管道。异常时任务标记 failed。"""
    task = await svc.tasks.get_task(task_id)
    if task is None:
        task = Task(task_id=task_id, graph_id=params.graph_id)
        await svc.tasks.create_task(task)

    try:
        await _run(task, params, svc)
    except asyncio.CancelledError:
        task.status = TaskStatus.failed
        task.error = "任务被取消"
        emit_task_log(task.error)
        await svc.tasks.update_task(task)
        raise
    except Exception as e:
        logger.exception("构建任务 %s 失败", task_id)
        task.status = TaskStatus.failed
        task.error = str(e)
        emit_task_log(f"任务失败: {type(e).__name__}: {e}")
        await svc.tasks.update_task(task)


def _attach_log_sink(task: Task, svc: Services) -> None:
    """把 LLM 重试等运行期事件写入任务日志（contextvars 隔离并行任务）。"""

    def _sink(message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        task.logs.append(f"[{stamp}] {message}")
        del task.logs[:-200]  # 上限 200 条，防异常场景刷屏
        asyncio.get_running_loop().create_task(svc.tasks.update_task(task))

    set_task_log_sink(_sink)


async def _run(task: Task, params: BuildParams, svc: Services) -> None:
    cfg = svc.config
    _attach_log_sink(task, svc)

    # ① 解析（多文件按上传顺序拼接）
    task.status = TaskStatus.parsing
    task.stage = "parsing"
    task.progress = 0.05
    task.message = f"解析 {len(params.files)} 个文档"
    emit_task_log(task.message)
    await svc.tasks.update_task(task)
    texts = []
    for file_bytes, filename in params.files:
        texts.append(parser.parse_bytes(file_bytes, filename))
    text = "\n\n".join(t for t in texts if t.strip())
    if not text.strip():
        raise ValueError("文档内容为空，无法构建图谱")

    # ② 切块
    task.status = TaskStatus.chunking
    task.stage = "chunking"
    task.progress = 0.10
    task.message = "切块"
    await svc.tasks.update_task(task)
    chunk_size = params.chunk_size or cfg.chunk_size
    chunk_overlap = params.chunk_overlap if params.chunk_overlap is not None else cfg.chunk_overlap
    chunks = chunker.chunk_text(text, chunk_size, chunk_overlap)
    if not chunks:
        raise ValueError("文档内容为空，无法构建图谱")
    emit_task_log(f"切块: {len(chunks)} 块")

    # ③ 本体
    task.status = TaskStatus.ontology
    task.stage = "ontology"
    task.progress = 0.15
    task.message = "生成本体"
    emit_task_log("开始生成本体" if not params.ontology else "使用预览本体")
    await svc.tasks.update_task(task)
    if params.ontology:
        onto = ontology_mod.normalize_ontology(params.ontology, cfg)
        # 用户自带本体也走规范化（大小写/上限/白名单）
        if not onto.entity_types:
            raise ValueError("提供的 ontology 缺少 entity_types")
    else:
        onto = await ontology_mod.generate_ontology(svc.llm, text, params.purpose, cfg)
    onto_json = ontology_mod.ontology_to_json(onto)
    await svc.neo4j.set_graph_status(params.graph_id, "building", ontology_json=onto_json)

    # ④ 逐块抽取
    task.status = TaskStatus.extracting
    task.stage = "extracting"
    task.progress = 0.20
    task.message = f"抽取 {len(chunks)} 块"

    async def _extract_progress(done: int, total: int) -> None:
        task.progress = 0.20 + 0.55 * (done / total)
        task.message = f"抽取进度 {done}/{total}"
        await svc.tasks.update_task(task)

    results, warnings = await extractor.extract_chunks(
        svc.llm, chunks, onto, onto_json, params.graph_id, cfg, svc.tasks, _extract_progress
    )
    task.message = f"抽取完成{'（' + str(len(warnings)) + ' 块失败跳过）' if warnings else ''}"
    emit_task_log(task.message)
    await svc.tasks.update_task(task)

    # ⑤ 消歧合并
    task.status = TaskStatus.resolving
    task.stage = "resolving"
    task.progress = 0.80
    task.message = "实体消歧合并"
    emit_task_log(task.message)
    await svc.tasks.update_task(task)
    resolution = await resolver.resolve(results, svc.llm, svc.embeddings, cfg)

    # ⑥ 写入
    task.status = TaskStatus.writing
    task.stage = "writing"
    task.progress = 0.90
    task.message = "写入 Neo4j / Qdrant"
    emit_task_log(task.message)
    await svc.tasks.update_task(task)
    node_count, edge_count = await writer.write_all(
        resolution, params.graph_id, svc.neo4j, svc.qdrant, svc.embeddings, cfg
    )

    task.status = TaskStatus.completed
    task.stage = "completed"
    task.progress = 1.0
    task.message = f"构建完成: {node_count} 节点 / {edge_count} 边"
    emit_task_log(task.message)
    await svc.tasks.update_task(task)
    await svc.neo4j.set_graph_status(params.graph_id, "ready", ontology_json=onto_json)
    # 暂存文件保留（磁盘持久化）：支持重复构建/换本体重跑；删除图谱时才清理


def new_task_id() -> str:
    return str(_uuid.uuid4())
