"""FastAPI 入口：托管 API 与构建后的 Web UI。"""
from __future__ import annotations

import logging
import shutil
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
from app.storage.settings import SettingsStore
from app.storage.tasks import TaskStore

logger = structlog.get_logger("graphforge")

_services: Services | None = None
_settings_store: SettingsStore | None = None

# 暂存文件持久化目录（磁盘 backed，容器重建/重启后不丢；由 lifespan 初始化）
_upload_root: Path | None = None


def configure_upload_root(root: Path) -> None:
    global _upload_root
    _upload_root = root


def _upload_dir(graph_id: str) -> Path:
    return (_upload_root or Path("./data/uploads")) / graph_id


def _safe_name(filename: str) -> str:
    """文件系统安全文件名：去路径分隔符/控制字符，保留中文名（幂等）。"""
    name = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = "".join(c for c in name if c.isprintable() and c not in "/\\")
    return name or "unnamed"


def get_services() -> Services:
    assert _services is not None, "服务未初始化"
    return _services


def get_settings_store() -> SettingsStore:
    assert _settings_store is not None, "设置存储未初始化"
    return _settings_store


def store_uploaded_file(graph_id: str, content: bytes, filename: str) -> None:
    d = _upload_dir(graph_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / _safe_name(filename)).write_bytes(content)


def get_uploaded_files(graph_id: str) -> list[tuple[bytes, str]]:
    """返回已暂存文件列表 (bytes, filename)，按写入时间排序（保持上传顺序）。"""
    d = _upload_dir(graph_id)
    if not d.is_dir():
        return []
    return [
        (p.read_bytes(), p.name)
        for p in sorted(d.iterdir(), key=lambda x: x.stat().st_mtime)
        if p.is_file()
    ]


def list_uploaded_filenames(graph_id: str) -> list[str]:
    return [name for _, name in get_uploaded_files(graph_id)]


def remove_uploaded_file(graph_id: str, filename: str) -> bool:
    p = _upload_dir(graph_id) / _safe_name(filename)
    if p.is_file():
        p.unlink()
        return True
    return False


def clear_uploaded_files(graph_id: str) -> None:
    shutil.rmtree(_upload_dir(graph_id), ignore_errors=True)


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
    global _services, _settings_store
    configure_logging()
    config = Config.load()
    configure_upload_root(config.data_path / "uploads")
    # Web 端设置覆盖项（SQLite 持久化，优先级高于环境变量/.env）
    _settings_store = SettingsStore(config.data_path / "settings.db")
    web_overrides = await _settings_store.load_all()
    if web_overrides:
        config = config.with_overrides(web_overrides)
        logger.info("startup", msg=f"已应用 Web 端设置覆盖: {', '.join(sorted(web_overrides))}")
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
    if _settings_store is not None:
        await _settings_store.close()


async def rebuild_llm_services(new_config: Config) -> None:
    """用新配置热重建 LLM/embeddings 客户端（立即生效）。

    Neo4j / Qdrant 连接与任务存储不受影响。调用方需保证无运行中的构建任务
    （TaskRegistry.has_running），否则运行中任务会拿到已关闭的旧客户端。
    """
    assert _services is not None, "服务未初始化"
    old_llm, old_embeddings = _services.llm, _services.embeddings
    _services.config = new_config
    _services.llm = build_llm_client(new_config)
    _services.embeddings = build_embedding_client(new_config)
    await old_llm.close()
    await old_embeddings.close()


app = FastAPI(title="GraphForge", version="0.1.0", lifespan=lifespan)


from app.api.graphs import router as graphs_router, tasks_router  # noqa: E402
from app.api.documents import router as documents_router  # noqa: E402
from app.api.search import router as search_router  # noqa: E402
from app.api.read import router as read_router  # noqa: E402
from app.api.export import router as export_router  # noqa: E402
from app.api.settings import router as settings_router  # noqa: E402

app.include_router(graphs_router)
app.include_router(tasks_router)
app.include_router(documents_router)
app.include_router(search_router)
app.include_router(read_router)
app.include_router(export_router)
app.include_router(settings_router)


@app.get("/api/health")
async def health():
    return {"success": True, "data": {"status": "ok"}}


# 构建后的 Web UI 静态托管（存在才挂载）
_web_dist = Path(__file__).resolve().parent.parent / "web" / "dist"
if _web_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_web_dist), html=True), name="web")
