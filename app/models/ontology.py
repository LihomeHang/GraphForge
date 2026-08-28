"""本体模型 —— 逐字段对齐 MiroFish `OntologyGenerator` 输出格式。

本体结构：
{
  "entity_types": [{"name", "description", "attributes": [{"name", "type", "description"}], "examples"}],
  "edge_types":   [{"name", "description", "source_targets": [{"source", "target"}], "attributes": [...], "examples"}],
  "analysis_summary": "..."
}
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class AttributeSpec(BaseModel):
    name: str
    type: str = "string"
    description: str = ""


class EntityType(BaseModel):
    name: str
    description: str = ""
    attributes: list[AttributeSpec] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)


class SourceTarget(BaseModel):
    source: str
    target: str


class EdgeType(BaseModel):
    name: str
    description: str = ""
    source_targets: list[SourceTarget] = Field(default_factory=list)
    attributes: list[AttributeSpec] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)


class Ontology(BaseModel):
    entity_types: list[EntityType] = Field(default_factory=list)
    edge_types: list[EdgeType] = Field(default_factory=list)
    analysis_summary: str = ""

    def entity_names(self) -> list[str]:
        return [et.name for et in self.entity_types]

    def edge_names(self) -> list[str]:
        return [et.name for et in self.edge_types]

    def model_dump_json_compat(self) -> dict:
        """导出为 MiroFish `project.ontology` 同构的普通 dict。"""
        return self.model_dump(mode="python")
