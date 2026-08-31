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


@pytest.mark.asyncio
async def test_extract_chunks_reads_and_migrates_legacy_graph_cache(ontology, task_store):
    """部署缓存键升级后，旧 graph_id 前缀缓存仍应命中并迁移到新键。"""
    config = Config(llm_provider="mock", neo4j_password="x")
    llm = MockLLMClient()
    chunk = "旧缓存块"
    ontology_json = "ont-json"
    key = extractor.chunk_hash("g1", ontology_json, chunk)
    legacy_key = f"g1:{key}"
    cached = {
        "entities": [{"name": "旧缓存实体", "type": "Person"}],
        "relations": [],
    }
    await task_store.put_extract_cache(legacy_key, cached)

    results, warnings = await extractor.extract_chunks(
        llm, [chunk], ontology, ontology_json, "g1", config, task_store
    )

    assert not warnings
    assert results[0].entities[0].name == "旧缓存实体"
    assert llm.calls == []
    migrated = await task_store.get_extract_cache(key)
    assert migrated is not None
    assert migrated["entities"][0]["name"] == "旧缓存实体"


def test_compact_ontology_prompt_is_smaller(ontology):
    compact = extractor._ontology_compact(ontology)
    assert len(compact) < len(ontology.model_dump_json())
    assert "Person" in compact and "WORKS_AT" in compact


def test_soft_ontology_prompt_includes_generic_fallbacks(ontology):
    compact = extractor._ontology_compact(ontology, ontology_mode="soft")

    assert '"name":"Entity"' in compact
    assert '"name":"RELATED_TO"' in compact


def test_soft_ontology_preserves_unknown_entities_and_relations(ontology):
    result = extractor._validate_extraction(
        {
            "entities": [
                {"name": "TCP/IP", "type": "TechnicalConcept", "summary": "网络协议族"},
                {"name": "分层架构", "type": "ArchitecturePattern", "summary": "架构风格"},
            ],
            "relations": [
                {
                    "source": "分层架构",
                    "target": "TCP/IP",
                    "type": "USES_TECHNOLOGY",
                    "fact": "分层架构可以使用 TCP/IP 进行网络通信",
                }
            ],
        },
        ontology,
        ontology_mode="soft",
    )

    assert [(entity.name, entity.type) for entity in result.entities] == [
        ("TCP/IP", "Entity"),
        ("分层架构", "Entity"),
    ]
    assert len(result.relations) == 1
    assert result.relations[0].type == "RELATED_TO"


def test_soft_ontology_materializes_relation_endpoints_missing_from_entities(ontology):
    result = extractor._validate_extraction(
        {
            "entities": [],
            "relations": [
                {
                    "source": "质量属性",
                    "target": "可用性",
                    "type": "HAS_ATTRIBUTE",
                    "fact": "可用性是一项软件质量属性",
                }
            ],
        },
        ontology,
        ontology_mode="soft",
    )

    assert [(entity.name, entity.type) for entity in result.entities] == [
        ("质量属性", "Entity"),
        ("可用性", "Entity"),
    ]
    assert result.relations[0].type == "RELATED_TO"


def test_cache_key_separates_strict_and_soft_ontology_modes():
    strict = extractor.chunk_hash("g1", "ontology", "chunk", ontology_mode="strict")
    soft = extractor.chunk_hash("g1", "ontology", "chunk", ontology_mode="soft")

    assert strict != soft


@pytest.mark.asyncio
async def test_extract_chunks_batches_requests(ontology):
    """多个未缓存块应合并为一次请求，并保持结果按块序返回。"""
    config = Config(llm_provider="mock", neo4j_password="x", extract_batch_size=2)
    llm = MockLLMClient()
    llm.enqueue_json(
        {
            "results": [
                {"index": 0, "entities": [{"name": "A", "type": "Person"}], "relations": []},
                {"index": 1, "entities": [{"name": "B", "type": "Person"}], "relations": []},
            ]
        }
    )
    results, warnings = await extractor.extract_chunks(
        llm, ["块A", "块B"], ontology, "ont-json", "g1", config
    )
    assert not warnings
    assert [r.entities[0].name for r in results] == ["A", "B"]
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_extract_chunks_awaits_async_preview_callback(ontology):
    config = Config(llm_provider="mock", neo4j_password="x")
    llm = MockLLMClient()
    llm.enqueue_json({"entities": [{"name": "A", "type": "Person"}], "relations": []})
    seen = []

    async def on_result(index, result):
        seen.append((index, result.entities[0].name))

    await extractor.extract_chunks(
        llm, ["块A"], ontology, "ont-json", "g1", config,
        chunk_result_cb=on_result,
    )

    assert seen == [(0, "A")]
