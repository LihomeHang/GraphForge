"""resolver 消歧合并单元测试（mock LLM + mock embeddings）。"""
import pytest

from app.config import Config
from app.llm.client import MockLLMClient
from app.models.graph import ExtractionResult, ExtractEntity, ExtractRelation
from app.pipeline import resolver


def _mk(name: str, summary: str = "", type_: str = "Person") -> ExtractEntity:
    return ExtractEntity(name=name, type=type_, summary=summary)


class _IdenticalEmbeddings:
    """测试 stub：所有文本都返回同一向量（相似度恒为 1，保证候选对产生）。"""

    async def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]

    async def embed_one(self, text):
        return [1.0, 0.0]

    async def dim(self):
        return 2

    async def close(self):
        return None


def _identical_embeddings() -> _IdenticalEmbeddings:
    return _IdenticalEmbeddings()


@pytest.mark.asyncio
async def test_casefold_exact_merge(mock_llm, mock_embeddings):
    """阶段一：casefold 精确匹配自动合并。"""
    config = Config(llm_provider="mock", neo4j_password="x")
    results = [
        ExtractionResult(entities=[_mk("张三", "工程师")], relations=[]),
        ExtractionResult(entities=[_mk(" 张三 ", "爱钓鱼")], relations=[]),
    ]
    out = await resolver.resolve(results, mock_llm, mock_embeddings, config)
    assert len(out.entities) == 1
    assert out.entities[0].name == "张三"
    # summary 拼接
    assert "工程师" in out.entities[0].summary and "爱钓鱼" in out.entities[0].summary


@pytest.mark.asyncio
async def test_llm_judged_merge(mock_llm):
    """阶段二：嵌入相似 + LLM 判定为同一 → 合并。"""
    config = Config(llm_provider="mock", neo4j_password="x", resolve_sim_threshold=0.5)
    mock_llm.enqueue_json({"same": True, "summary": "张三，工程师，爱钓鱼"})
    results = [
        ExtractionResult(entities=[_mk("张三", "工程师")], relations=[]),
        ExtractionResult(entities=[_mk("张三三", "爱钓鱼")], relations=[]),
    ]
    out = await resolver.resolve(results, mock_llm, _identical_embeddings(), config)
    assert len(out.entities) == 1
    assert out.entities[0].summary == "张三，工程师，爱钓鱼"


@pytest.mark.asyncio
async def test_llm_judged_not_merge(mock_llm):
    """LLM 判定为不同 → 保持两个实体。"""
    config = Config(llm_provider="mock", neo4j_password="x", resolve_sim_threshold=0.5)
    mock_llm.enqueue_json({"same": False})
    results = [
        ExtractionResult(entities=[_mk("张三", "工程师")], relations=[]),
        ExtractionResult(entities=[_mk("张三三", "爱钓鱼")], relations=[]),
    ]
    out = await resolver.resolve(results, mock_llm, _identical_embeddings(), config)
    assert len(out.entities) == 2


@pytest.mark.asyncio
async def test_relations_remapped_after_merge(mock_llm, mock_embeddings):
    """合并后关系 source/target 的 uuid 应指向代表实体。"""
    config = Config(llm_provider="mock", neo4j_password="x")
    results = [
        ExtractionResult(
            entities=[_mk("张三", "工程师"), _mk("ACME", "公司", "Company")],
            relations=[
                ExtractRelation(source="张三", target="ACME", type="WORKS_AT", fact="张三入职ACME")
            ],
        ),
        ExtractionResult(entities=[_mk(" 张三", "别名")], relations=[]),
    ]
    out = await resolver.resolve(results, mock_llm, mock_embeddings, config)
    persons = [e for e in out.entities if e.type == "Person"]
    assert len(persons) == 1
    person = persons[0]
    assert len(out.relations) == 1
    assert out.relations[0].source_node_uuid == person.uuid
