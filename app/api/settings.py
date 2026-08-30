"""Web 端 LLM / Embedding 运行时设置：查看 / 更新（热生效）/ 连通性测试。

优先级：Web 设置（SQLite 持久化）> 环境变量 / .env。
更新后热重建 LLM/embeddings 客户端，立即生效，无需重启容器；
Neo4j / Qdrant 连接与任务存储不受影响。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from app.config import Config, ConfigError
from app.llm.client import OpenAILLMClient
from app.llm.embeddings import OpenAIEmbeddingClient
from app.models.api import SettingsUpdateRequest
from app.pipeline.runner import Services
from app.storage.settings import SettingsStore

router = APIRouter(prefix="/api/settings", tags=["settings"])

# 允许通过 Web 修改的配置项（环境变量名风格，与 Config.with_overrides 对应）
ALLOWED_KEYS = [
    "LLM_PROVIDER", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "LLM_TEMPERATURE",
    "EMBEDDING_BASE_URL", "EMBEDDING_API_KEY", "EMBEDDING_MODEL", "EMBEDDING_DIM",
]

_TEST_TIMEOUT = 20.0  # 连通性测试单次超时（秒）


def _services() -> Services:
    from app.main import get_services

    return get_services()


def _settings_store() -> SettingsStore:
    from app.main import get_settings_store

    return get_settings_store()


def _ok(data=None):
    return {"success": True, "data": data}


def _mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "***"
    return f"{key[:3]}***{key[-4:]}"


def _payload(svc: Services) -> dict:
    """当前生效配置概览（API Key 只回掩码，不回原文）。"""
    cfg = svc.config
    return {
        "llm_provider": cfg.llm_provider,
        "llm_base_url": cfg.llm_base_url,
        "llm_api_key_set": bool(cfg.llm_api_key),
        "llm_api_key_masked": _mask(cfg.llm_api_key),
        "llm_model": cfg.llm_model,
        "llm_temperature": cfg.llm_temperature,
        "embedding_base_url": cfg.embedding_base_url,
        "embedding_api_key_set": bool(cfg.embedding_api_key or cfg.llm_api_key),
        "embedding_model": cfg.embedding_model,
        "embedding_dim": cfg.embedding_dim,
        "embedding_uses_llm_config": not (cfg.embedding_base_url or cfg.embedding_api_key),
        "active_builds": svc.registry.has_running(),
    }


def _updates_to_overrides(req: SettingsUpdateRequest) -> dict[str, str]:
    """请求体 → 环境变量名风格覆盖项。显式传 None 的字段不动；api_key 空串视为不修改。"""
    updates = req.model_dump(exclude_unset=True)
    out: dict[str, str] = {}
    if updates.get("llm_provider") is not None:
        out["LLM_PROVIDER"] = updates["llm_provider"]
    if updates.get("llm_base_url") is not None:
        out["LLM_BASE_URL"] = updates["llm_base_url"]
    if updates.get("llm_api_key"):
        out["LLM_API_KEY"] = updates["llm_api_key"]
    if updates.get("llm_model") is not None:
        out["LLM_MODEL"] = updates["llm_model"]
    if updates.get("llm_temperature") is not None:
        out["LLM_TEMPERATURE"] = str(updates["llm_temperature"])
    if updates.get("embedding_base_url") is not None:
        out["EMBEDDING_BASE_URL"] = updates["embedding_base_url"]
    if updates.get("embedding_api_key"):
        out["EMBEDDING_API_KEY"] = updates["embedding_api_key"]
    if updates.get("embedding_model") is not None:
        out["EMBEDDING_MODEL"] = updates["embedding_model"]
    if updates.get("embedding_dim") is not None:
        out["EMBEDDING_DIM"] = str(updates["embedding_dim"])
    return {k: v for k, v in out.items() if k in ALLOWED_KEYS}


@router.get("")
async def get_settings():
    """当前生效配置 + Web 端覆盖项列表。"""
    svc = _services()
    overrides = await _settings_store().load_all()
    payload = _payload(svc)
    payload["web_overrides"] = sorted(k for k, v in overrides.items() if v)
    return _ok(payload)


@router.put("")
async def update_settings(req: SettingsUpdateRequest):
    """更新设置：校验 → 持久化到 SQLite → 热重建 LLM/embeddings 客户端（立即生效）。"""
    svc = _services()
    if svc.registry.has_running():
        raise HTTPException(status_code=409, detail="有构建任务运行中，请等待完成后再修改设置")

    updates = _updates_to_overrides(req)
    if not updates:
        return _ok(_payload(svc))

    # 合并 db 现有覆盖项后派生新配置并校验（失败则不保存、不重建）
    merged = {**await _settings_store().load_all(), **updates}
    new_config = svc.config.with_overrides(merged)
    try:
        new_config.validate()
    except ConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await _settings_store().update(updates)
    from app.main import rebuild_llm_services

    await rebuild_llm_services(new_config)
    return _ok(_payload(svc))


@router.post("/test")
async def test_settings(req: SettingsUpdateRequest):
    """用提交的配置做连通性测试（不保存）；api_key 缺省沿用现有配置。"""
    svc = _services()
    merged = {**await _settings_store().load_all(), **_updates_to_overrides(req)}
    cfg = svc.config.with_overrides(merged)
    try:
        cfg.validate()
    except ConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))

    result: dict = {}

    if cfg.llm_provider == "mock":
        result["llm"] = {"ok": True, "message": "mock 模式（离线演示，无需连接）"}
    else:
        client = OpenAILLMClient(cfg, max_retries=1)
        try:
            await asyncio.wait_for(
                client.complete([{"role": "user", "content": "ping"}]), timeout=_TEST_TIMEOUT
            )
            result["llm"] = {"ok": True, "message": f"模型 {cfg.llm_model} 连接成功"}
        except asyncio.TimeoutError:
            result["llm"] = {"ok": False, "message": f"连接超时（>{_TEST_TIMEOUT:.0f}s）"}
        except Exception as e:
            result["llm"] = {"ok": False, "message": f"连接失败: {e}"[:400]}
        finally:
            await client.close()

    if cfg.llm_provider == "openai":
        emb = OpenAIEmbeddingClient(cfg)
        try:
            dim = await asyncio.wait_for(emb.dim(), timeout=_TEST_TIMEOUT)
            result["embedding"] = {
                "ok": True,
                "message": f"嵌入模型 {cfg.embedding_model} 可用（维度 {dim}）",
                "dim": dim,
            }
        except asyncio.TimeoutError:
            result["embedding"] = {"ok": False, "message": f"连接超时（>{_TEST_TIMEOUT:.0f}s）"}
        except Exception as e:
            result["embedding"] = {"ok": False, "message": f"嵌入连接失败: {e}"[:400]}
        finally:
            await emb.close()

    return _ok(result)
