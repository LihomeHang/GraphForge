"""API 层辅助模型：搜索结果、请求体。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class GraphCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    graph_id: str | None = Field(default=None, min_length=1, max_length=200)


class OntologyForm(BaseModel):
    purpose: str = Field(default="", description="分析目的")


class BuildRequest(BaseModel):
    ontology: dict | None = None
    ontology_mode: Literal["strict", "soft"] = "strict"
    replace_existing: bool = True
    documents_are_chunks: bool = False
    chunk_size: int | None = Field(default=None, ge=200, le=12000)
    chunk_overlap: int | None = Field(default=None, ge=0, le=4000)
    purpose: str = ""


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=10, ge=1, le=100)
    types: list[str] = Field(default_factory=lambda: ["node", "edge"])


class SettingsUpdateRequest(BaseModel):
    """Web 端设置更新：字段缺省（None）表示不修改；API Key 空串也视为不修改。"""

    llm_provider: str | None = Field(default=None, description='"openai" | "mock"')
    llm_base_url: str | None = None
    llm_api_key: str | None = Field(default=None, description="缺省/空串 = 保持现有 key")
    llm_model: str | None = None
    llm_temperature: float | None = Field(default=None, ge=0, le=2)
    embedding_base_url: str | None = None
    embedding_provider: str | None = Field(default=None, pattern="^(auto|remote|local)$")
    embedding_api_key: str | None = Field(default=None, description="缺省/空串 = 保持现有 key")
    embedding_model: str | None = None
    embedding_dim: int | None = Field(default=None, ge=1)
    local_embedding_dim: int | None = Field(default=None, ge=32, le=8192)
    chunk_size: int | None = Field(default=None, ge=200, le=12000)
    chunk_overlap: int | None = Field(default=None, ge=0, le=4000)
    llm_concurrency: int | None = Field(default=None, ge=1, le=32)
    extract_batch_size: int | None = Field(default=None, ge=1, le=32)
    resolve_candidate_k: int | None = Field(default=None, ge=1, le=100)


class SearchHit(BaseModel):
    type: str  # "node" | "edge"
    uuid: str
    score: float
    name: str = ""
    summary: str = ""
    fact: str = ""
    source_node_uuid: str = ""
    target_node_uuid: str = ""


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit] = Field(default_factory=list)
