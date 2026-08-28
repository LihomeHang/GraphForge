"""FastAPI 入口：托管 API 与构建后的 Web UI。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import Config
from app.llm.client import build_llm_client
from app.llm.embeddings import build_embedding_client
from app.pipeline.runner import Services, TaskRegistry
from app.storage.neo4j_store import Neo4jStore
from app.storage.qdrant_store import QdrantStore
from app.storage.tasks import TaskStore

logger = structlog.get_logger("graphforge")

_services: Services | None = None
# graph_id -> [(bytes, filename), ...]；多文件构建同一图谱，按上传顺序拼接
_uploads: dict[str, list[tuple[bytes, str]]] = {}


def get_services() -> Services:
    assert _services is not None, "服务未初始化"
    return _services


def store_uploaded_file(graph_id: str, content: bytes, filename: str) -> None:
    _uploads.setdefault(graph_id, []).append((content, filename))


def get_uploaded_files(graph_id: str) -> list[tuple[bytes, str]]:
    """返回已暂存文件列表（filename, bytes 按上传顺序）。"""
    return _uploads.get(graph_id, [])


def list_uploaded_filenames(graph_id: str) -> list[str]:
    return [name for _, name in _uploads.get(graph_id, [])]


def remove_uploaded_file(graph_id: str, filename: str) -> bool:
    files = _uploads.get(graph_id, [])
    for i, (_, name) in enumerate(files):
        if name == filename:
            del files[i]
            return True
    return False


def clear_uploaded_files(graph_id: str) -> None:
    _uploads.pop(graph_id, None)


def configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ]
    )
    logging.basicConfig(level=logging.INFO)


def _build_services(config: Config) -> Services:
    """构建服务集（可被测试 monkeypatch）。"""
    llm = build_llm_client(config)
    embeddings = build_embedding_client(config)
    neo4j = Neo4jStore(config)
    qdrant = QdrantStore(config)
    return Services(
        config=config, llm=llm, embeddings=embeddings, neo4j=neo4j, qdrant=qdrant,
        tasks=TaskStore(config.data_path / "tasks.db"), registry=TaskRegistry(),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _services
    configure_logging()
    config = Config.load()
    _services = _build_services(config)
    # 启动时探活（mock 存储无 verify 也跳过——探活由 store 自身决定）
    if hasattr(_services.neo4j, "verify"):
        try:
            await _services.neo4j.verify()
        except Exception as e:
            logger.error("startup", msg=f"Neo4j 连接失败: {e}")
            raise
    if hasattr(_services.qdrant, "verify"):
        try:
            await _services.qdrant.verify()
        except Exception as e:
            logger.error("startup", msg=f"Qdrant 连接失败: {e}")
            raise
    interrupted = await _services.tasks.mark_interrupted_on_startup()
    if interrupted:
        logger.info("startup", msg=f"标记 {interrupted} 个中断任务为 failed")
    logger.info("startup", msg="GraphForge 启动完成")
    yield
    await _services.llm.close()
    await _services.embeddings.close()
    await _services.neo4j.close()
    await _services.qdrant.close()
    await _services.tasks.close()


app = FastAPI(title="GraphForge", version="0.1.0", lifespan=lifespan)


from app.api.graphs import router as graphs_router, tasks_router  # noqa: E402
from app.api.documents import router as documents_router  # noqa: E402
from app.api.search import router as search_router  # noqa: E402
from app.api.read import router as read_router  # noqa: E402
from app.api.export import router as export_router  # noqa: E402

app.include_router(graphs_router)
app.include_router(tasks_router)
app.include_router(documents_router)
app.include_router(search_router)
app.include_router(read_router)
app.include_router(export_router)


@app.get("/api/health")
async def health():
    return {"success": True, "data": {"status": "ok"}}


# 构建后的 Web UI 静态托管（存在才挂载）
_web_dist = Path(__file__).resolve().parent.parent / "web" / "dist"
if _web_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_web_dist), html=True), name="web")
