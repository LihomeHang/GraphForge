"""本体规范化单元测试：大小写、上限、白名单、兜底。"""
from app.config import Config
from app.models.ontology import SourceTarget
from app.pipeline.ontology import (
    normalize_edge_name,
    normalize_entity_name,
    normalize_ontology,
)


def test_entity_name_pascal_case():
    assert normalize_entity_name("person") == "Person"
    assert normalize_entity_name("my company") == "MyCompany"
    assert normalize_entity_name("software_engineer") == "SoftwareEngineer"
    assert normalize_entity_name(" 人物 ") == "人物"
    assert normalize_entity_name("") == "Thing"


def test_edge_name_screaming_snake():
    assert normalize_edge_name("works for") == "WORKS_FOR"
    assert normalize_edge_name("knows") == "KNOWS"
    assert normalize_edge_name("part Of") == "PART_OF"
    assert normalize_edge_name("") == "RELATED_TO"


def test_normalize_full_ontology():
    config = Config(neo4j_password="x")
    raw = {
        "entity_types": [
            {"name": "person", "description": "人物", "attributes": [{"name": "age", "type": "integer"}, {"name": "9bad", "type": "string"}]},
            {"name": "company", "description": "公司"},
            {"name": "person", "description": "重复项应被忽略"},
        ],
        "edge_types": [
            {"name": "works at", "source_targets": [{"source": "person", "target": "company"}, {"source": "person", "target": "Unknown"}]},
            {"name": "knows", "source_targets": []},
        ],
        "analysis_summary": "测试",
    }
    onto = normalize_ontology(raw, config)
    names = onto.entity_names()
    assert names.count("Person") == 1  # 去重
    assert "Company" in names
    # 属性白名单：9bad 被丢弃
    person = next(e for e in onto.entity_types if e.name == "Person")
    assert all(a.name != "9bad" for a in person.attributes)
    assert any(a.name == "age" for a in person.attributes)
    # source_targets 里 Unknown 被过滤，合法对保留
    works_at = next(e for e in onto.edge_types if e.name == "WORKS_AT")
    assert works_at.source_targets == [SourceTarget(source="Person", target="Company")]


def test_fallback_person_organization():
    config = Config(neo4j_password="x")
    onto = normalize_ontology({"entity_types": [], "edge_types": []}, config)
    assert "Person" in onto.entity_names()
    assert "Organization" in onto.entity_names()


def test_type_limit():
    config = Config(neo4j_password="x", entity_type_limit=4)
    raw = {"entity_types": [{"name": f"Type{i}"} for i in range(10)]}
    onto = normalize_ontology(raw, config)
    assert len(onto.entity_types) <= 4
