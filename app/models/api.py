"""API 层辅助模型：搜索结果、请求体。"""
from __future__ import annotations

from pydantic import BaseModel, Field


class GraphCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class OntologyForm(BaseModel):
    purpose: str = Field(default="", description="分析目的")


class BuildRequest(BaseModel):
    ontology: dict | None = None
    chunk_size: int | None = None
    chunk_overlap: int | None = None
    purpose: str = ""


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=10, ge=1, le=100)
    types: list[str] = Field(default_factory=lambda: ["node", "edge"])


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
