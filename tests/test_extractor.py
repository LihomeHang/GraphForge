"""extractor 校验逻辑单元测试（mock LLM，不触网）。"""
import pytest

from app.config import Config
from app.llm.client import MockLLMClient, parse_json_loose
from app.models.ontology import AttributeSpec, EdgeType, EntityType, Ontology, SourceTarget
from app.pipeline import extractor


@pytest.fixture
def ontology() -> Ontology:
    return Ontology(
        entity_types=[
            EntityType(name="Person", description="人物"),
            EntityType(name="Company", description="公司"),
        ],
        edge_types=[
            EdgeType(
                name="WORKS_AT",
                source_targets=[SourceTarget(source="Person", target="Company")],
            ),
        ],
    )


@pytest.mark.asyncio
async def test_extract_valid_chunk(ontology):
    config = Config(llm_provider="mock", neo4j_password="x")
    llm = MockLLMClient()
    llm.enqueue_json(
        {
            "entities": [
                {"name": "张三", "type": "Person", "summary": "工程师"},
                {"name": "ACME", "type": "Company", "summary": "科技公司"},
            ],
            "relations": [
                {"source": "张三", "target": "ACME", "type": "WORKS_AT", "fact": "张三在ACME工作"}
            ],
        }
    )
    result, error = await extractor._extract_one(llm, "文本", ontology, "ont-json", config, None)
    assert error is None
    assert result is not None
    assert len(result.entities) == 2
    assert result.relations[0].fact == "张三在ACME工作"


@pytest.mark.asyncio
async def test_extract_filters_invalid_types(ontology):
    """引用本体外的类型/未列出的实体应被过滤。"""
    config = Config(llm_provider="mock", neo4j_password="x")
    llm = MockLLMClient()
    llm.enqueue_json(
        {
            "entities": [
                {"name": "张三", "type": "Person"},
                {"name": "幽灵", "type": "Ghost"},  # 非法类型
            ],
            "relations": [
                {"source": "张三", "target": "幽灵", "type": "WORKS_AT", "fact": "x"},  # target 未列出
                {"source": "张三", "target": "张三", "type": "LOVES", "fact": "x"},  # 非法关系类型
            ],
        }
    )
    result, _ = await extractor._extract_one(llm, "文本", ontology, "ont-json", config, None)
    assert result is not None
    assert [e.name for e in result.entities] == ["张三"]
    assert result.relations == []


@pytest.mark.asyncio
async def test_extract_json_repair_retry(ontology):
    """坏 JSON → 修复重试成功。"""
    config = Config(llm_provider="mock", neo4j_password="x", extract_max_retry=2)
    llm = MockLLMClient()
    llm.enqueue("{坏 JSON")  # 第一次失败
    llm.enqueue_json({"entities": [{"name": "张三", "type": "Person"}], "relations": []})
    result, error = await extractor._extract_one(llm, "文本", ontology, "ont-json", config, None)
    assert error is None
    assert result is not None and len(result.entities) == 1
    assert len(llm.calls) == 2  # 调了两次（原始 + 修复）


@pytest.mark.asyncio
async def test_extract_exhausted_retries_isolated(ontology):
    """重试耗尽 → 返回 None（块级失败隔离，不抛异常）。"""
    config = Config(llm_provider="mock", neo4j_password="x", extract_max_retry=1)
    llm = MockLLMClient()
    llm.enqueue("{bad1")
    llm.enqueue("{bad2")
    result, error = await extractor._extract_one(llm, "文本", ontology, "ont-json", config, None)
    assert result is None
    assert error is not None


@pytest.mark.asyncio
async def test_extract_chunks_with_cache(ontology, task_store):
    """块级缓存：同一块第二次不调 LLM。"""
    config = Config(llm_provider="mock", neo4j_password="x")
    llm = MockLLMClient()
    llm.enqueue_json({"entities": [{"name": "张三", "type": "Person"}], "relations": []})
    chunks = ["块内容A"]
    results, warnings = await extractor.extract_chunks(
        llm, chunks, ontology, "ont-json", "g1", config, task_store
    )
    assert len(results) == 1 and not warnings
    assert len(llm.calls) == 1
    # 第二次：命中缓存，LLM 不再被调用
    results2, _ = await extractor.extract_chunks(
        llm, chunks, ontology, "ont-json", "g1", config, task_store
    )
    assert len(results2) == 1
    assert len(llm.calls) == 1  # 没有新增调用
