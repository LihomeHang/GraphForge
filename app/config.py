"""GraphForge 环境变量配置。

风格对齐 MiroFish `Config.validate`：启动时校验关键配置缺失即拒绝启动。
所有配置项均可通过环境变量覆盖，默认值面向本地开发 / docker-compose。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """配置缺失或非法。"""


@dataclass(frozen=True)
class Config:
    # LLM (OpenAI 兼容)
    llm_provider: str = "openai"  # "openai" | "mock"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.0

    # Embedding (OpenAI 兼容)
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int | None = None  # None = 自动探测

    # Neo4j
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""

    # 管道参数
    chunk_size: int = 500
    chunk_overlap: int = 50
    llm_concurrency: int = 4
    resolve_sim_threshold: float = 0.85
    extract_max_retry: int = 2
    entity_type_limit: int = 16
    edge_type_limit: int = 24
    neo4j_batch_size: int = 500
    qdrant_batch_size: int = 256

    # 存储
    data_dir: str = "./data"

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).resolve()

    def validate(self) -> None:
        """校验关键配置；缺失即拒绝启动（mock 模式免 LLM key）。"""
        missing: list[str] = []
        if self.llm_provider != "mock" and not self.llm_api_key:
            missing.append("LLM_API_KEY")
        if not self.neo4j_uri:
            missing.append("NEO4J_URI")
        if not self.neo4j_password:
            missing.append("NEO4J_PASSWORD")
        if not self.qdrant_url:
            missing.append("QDRANT_URL")
        if self.chunk_size <= 0 or self.chunk_overlap < 0 or self.chunk_overlap >= self.chunk_size:
            raise ConfigError("CHUNK_SIZE 必须 > 0 且 CHUNK_OVERLAP < CHUNK_SIZE")
        if missing:
            raise ConfigError(
                f"缺少必需配置: {', '.join(missing)}。请设置对应环境变量后再启动。"
            )

    def with_overrides(self, raw: dict[str, str]) -> "Config":
        """应用环境变量名风格的覆盖项（Web 端设置，值来自 SQLite）。空值跳过。"""
        data: dict = {}
        for key, value in raw.items():
            if value is None or str(value).strip() == "":
                continue
            k, v = key.upper(), str(value).strip()
            if k == "LLM_PROVIDER":
                data["llm_provider"] = v.lower()
            elif k == "LLM_BASE_URL":
                data["llm_base_url"] = v
            elif k == "LLM_API_KEY":
                data["llm_api_key"] = v
            elif k == "LLM_MODEL":
                data["llm_model"] = v
            elif k == "LLM_TEMPERATURE":
                data["llm_temperature"] = float(v)
            elif k == "EMBEDDING_BASE_URL":
                data["embedding_base_url"] = v
            elif k == "EMBEDDING_API_KEY":
                data["embedding_api_key"] = v
            elif k == "EMBEDDING_MODEL":
                data["embedding_model"] = v
            elif k == "EMBEDDING_DIM":
                data["embedding_dim"] = int(v)
            # 未知 key 忽略（向前兼容）
        return replace(self, **data) if data else self

    @classmethod
    def from_env(cls) -> "Config":
        def _int(name: str, default: int) -> int:
            raw = os.environ.get(name)
            return int(raw) if raw is not None and raw != "" else default

        def _float(name: str, default: float) -> float:
            raw = os.environ.get(name)
            return float(raw) if raw is not None and raw != "" else default

        def _str(name: str, default: str = "") -> str:
            raw = os.environ.get(name)
            return raw if raw is not None else default

        embedding_dim_raw = os.environ.get("EMBEDDING_DIM")
        embedding_dim = int(embedding_dim_raw) if embedding_dim_raw and embedding_dim_raw != "" else None

        return cls(
            llm_provider=_str("LLM_PROVIDER", "openai").lower(),
            llm_base_url=_str("LLM_BASE_URL", "https://api.openai.com/v1"),
            llm_api_key=_str("LLM_API_KEY"),
            llm_model=_str("LLM_MODEL", "gpt-4o-mini"),
            llm_temperature=_float("LLM_TEMPERATURE", 0.0),
            embedding_base_url=_str("EMBEDDING_BASE_URL"),
            embedding_api_key=_str("EMBEDDING_API_KEY"),
            embedding_model=_str("EMBEDDING_MODEL", "text-embedding-3-small"),
            embedding_dim=embedding_dim,
            neo4j_uri=_str("NEO4J_URI", "bolt://localhost:7687"),
            neo4j_user=_str("NEO4J_USER", "neo4j"),
            neo4j_password=_str("NEO4J_PASSWORD"),
            qdrant_url=_str("QDRANT_URL", "http://localhost:6333"),
            qdrant_api_key=_str("QDRANT_API_KEY"),
            chunk_size=_int("CHUNK_SIZE", 500),
            chunk_overlap=_int("CHUNK_OVERLAP", 50),
            llm_concurrency=_int("LLM_CONCURRENCY", 4),
            resolve_sim_threshold=_float("RESOLVE_SIM_THRESHOLD", 0.85),
            extract_max_retry=_int("EXTRACT_MAX_RETRY", 2),
            entity_type_limit=_int("ENTITY_TYPE_LIMIT", 16),
            edge_type_limit=_int("EDGE_TYPE_LIMIT", 24),
            neo4j_batch_size=_int("NEO4J_BATCH_SIZE", 500),
            qdrant_batch_size=_int("QDRANT_BATCH_SIZE", 256),
            data_dir=_str("DATA_DIR", "./data"),
        )

    # 便于复用的单例入口
    _instance: "Config | None" = field(default=None, init=False, repr=False, compare=False)

    @classmethod
    def load(cls) -> "Config":
        """读取环境变量并校验（进程启动时调用一次）。

        先加载项目根目录的 `.env`（不覆盖已存在的环境变量，
        docker-compose 注入的变量优先），再读取进程环境。
        """
        load_dotenv(dotenv_path=Path(".env"), override=False)
        cfg = cls.from_env()
        cfg.validate()
        return cfg
