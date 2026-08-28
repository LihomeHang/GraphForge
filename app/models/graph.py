"""图谱节点/边模型与抽取中间结果。

- `Entity` / `Relation`：内部领域模型（写入 Neo4j / Qdrant）。
- `EntityNode` / `EdgeNode`：MiroFish 兼容读取结构（对齐 §6）。
- `ExtractEntity` / `ExtractRelation` / `ExtractionResult`：逐块抽取的 LLM 输出。
"""
from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field


def new_uuid() -> str:
    return str(_uuid.uuid4())


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Entity(BaseModel):
    uuid: str = Field(default_factory=new_uuid)
    name: str
    type: str  # 本体实体类型（PascalCase）
    summary: str = ""
    attributes: dict = Field(default_factory=dict)
    graph_id: str = ""
    created_at: str = Field(default_factory=utc_now)


class Relation(BaseModel):
    uuid: str = Field(default_factory=new_uuid)
    name: str  # 本体边名（SCREAMING_SNAKE_CASE）
    fact: str
    source: str = ""  # 源实体 name（消歧前）
    target: str = ""  # 目标实体 name（消歧前）
    source_node_uuid: str = ""
    target_node_uuid: str = ""
    attributes: dict = Field(default_factory=dict)
    graph_id: str = ""
    created_at: str = Field(default_factory=utc_now)


class ExtractEntity(BaseModel):
    name: str
    type: str
    summary: str = ""
    attributes: dict = Field(default_factory=dict)


class ExtractRelation(BaseModel):
    source: str
    target: str
    type: str
    fact: str
    attributes: dict = Field(default_factory=dict)


class ExtractionResult(BaseModel):
    entities: list[ExtractEntity] = Field(default_factory=list)
    relations: list[ExtractRelation] = Field(default_factory=list)


# ---- MiroFish 兼容读取结构（对齐 EntityNode.to_dict() 与 get_all_edges）----


class EntityNode(BaseModel):
    """对齐 MiroFish `EntityNode.to_dict()`：5 字段。"""

    uuid: str
    name: str
    labels: list[str] = Field(default_factory=lambda: ["Entity"])
    summary: str = ""
    attributes: dict = Field(default_factory=dict)


class EdgeNode(BaseModel):
    """对齐 MiroFish `get_all_edges` 字典结构：6 字段。"""

    uuid: str
    name: str
    fact: str
    source_node_uuid: str
    target_node_uuid: str
    attributes: dict = Field(default_factory=dict)
