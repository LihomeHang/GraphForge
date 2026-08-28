"""本体生成与规范化 —— 逐字段对齐 MiroFish `OntologyGenerator`。

规范化规则（同 MiroFish）：
- 实体类型名 → PascalCase；关系类型名 → SCREAMING_SNAKE_CASE
- 实体/关系类型数上限（默认 16/24，超出截断）
- 属性名白名单校验（字母/数字/下划线，且不以数字开头）
- Person / Organization 兜底补充
"""
from __future__ import annotations

import json
import re

from app.config import Config
from app.llm.client import LLMClient, LLMError
from app.models.ontology import AttributeSpec, EdgeType, EntityType, Ontology, SourceTarget

_ONTOLOGY_SYSTEM_PROMPT = """你是一位知识图谱本体设计专家。根据用户提供的文本和分析目的，设计一个知识图谱本体（ontology）。

要求：
1. 实体类型（entity_types）：5-16 个，每个包含 name（英文 PascalCase）、description、attributes（属性列表，每个属性含 name/type/description，type 取 string|number|integer|boolean|date，属性不超过 5 个）、examples（2-3 个示例值）。
2. 关系类型（edge_types）：5-24 个，每个包含 name（英文 SCREAMING_SNAKE_CASE）、description、source_targets（合法的实体类型对列表，形如 [{"source": "Person", "target": "Organization"}]）、attributes（可为空）、examples。
3. analysis_summary：一段话说明本体设计思路。
4. 必须包含 Person 和 Organization 两个基础实体类型（若文本涉及人或组织）。
5. 严格输出 JSON 对象，不要输出任何其他文本：
{"entity_types": [...], "edge_types": [...], "analysis_summary": "..."}

文本中提到的信息可能不完整，基于文本合理推断即可。
"""

_MAX_TEXT_FOR_PROMPT = 24000  # 全文超限时按块采样


def normalize_entity_name(name: str) -> str:
    """实体类型名 → PascalCase。"""
    words = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]", str(name or ""))
    if not words:
        return "Thing"
    out: list[str] = []
    for w in words:
        if re.match(r"[\u4e00-\u9fff]", w):
            out.append(w)  # 中文词直接保留
        else:
            out.append(w[0].upper() + w[1:])
    return "".join(out)


def normalize_edge_name(name: str) -> str:
    """关系类型名 → SCREAMING_SNAKE_CASE。"""
    words = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+", str(name or ""))
    if not words:
        return "RELATED_TO"
    out: list[str] = []
    for w in words:
        if re.match(r"[\u4e00-\u9fff]", w):
            out.append(w.upper())
        else:
            out.append(w.upper())
    return "_".join(out)


_ATTR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ALLOWED_ATTR_TYPES = {"string", "number", "integer", "boolean", "date"}


def _clean_attributes(raw: list) -> list[AttributeSpec]:
    out: list[AttributeSpec] = []
    for a in raw or []:
        if not isinstance(a, dict):
            continue
        name = str(a.get("name", "")).strip()
        if not _ATTR_NAME_RE.match(name):
            continue  # 白名单校验失败则丢弃该属性
        atype = str(a.get("type", "string")).lower()
        if atype not in _ALLOWED_ATTR_TYPES:
            atype = "string"
        out.append(
            AttributeSpec(
                name=name,
                type=atype,
                description=str(a.get("description", "")),
            )
        )
    return out


def normalize_ontology(raw: dict, config: Config) -> Ontology:
    """规范化 LLM 输出的原始本体 dict。规则同 MiroFish。"""
    entity_types: list[EntityType] = []
    seen_entities: set[str] = set()
    for et in (raw.get("entity_types") or [])[: config.entity_type_limit]:
        if not isinstance(et, dict):
            continue
        name = normalize_entity_name(et.get("name", ""))
        if not name or name in seen_entities:
            continue
        seen_entities.add(name)
        src = et.get("source_targets")
        entity_types.append(
            EntityType(
                name=name,
                description=str(et.get("description", "")),
                attributes=_clean_attributes(et.get("attributes") or []),
                examples=[str(x) for x in (et.get("examples") or [])][:5],
            )
        )

    # Person / Organization 兜底
    for fallback, desc in (
        ("Person", "人物实体（自动补充）"),
        ("Organization", "组织机构实体（自动补充）"),
    ):
        if fallback not in seen_entities:
            seen_entities.add(fallback)
            entity_types.append(EntityType(name=fallback, description=desc))
    if len(entity_types) > config.entity_type_limit:
        # 保底类型必须保留
        keep = entity_types[: config.entity_type_limit - 2]
        tails = [e for e in entity_types if e.name in ("Person", "Organization")]
        entity_types = [*keep, *tails]

    # 合法实体名集合，用于过滤 source_targets
    valid_names = {et.name for et in entity_types}

    edge_types: list[EdgeType] = []
    seen_edges: set[str] = set()
    for et in (raw.get("edge_types") or [])[: config.edge_type_limit]:
        if not isinstance(et, dict):
            continue
        name = normalize_edge_name(et.get("name", ""))
        if not name or name in seen_edges:
            continue
        seen_edges.add(name)
        pairs: list[SourceTarget] = []
        for st in et.get("source_targets") or []:
            if isinstance(st, dict) and st.get("source") and st.get("target"):
                src = normalize_entity_name(st["source"])
                tgt = normalize_entity_name(st["target"])
                if src in valid_names and tgt in valid_names:
                    pairs.append(SourceTarget(source=src, target=tgt))
        edge_types.append(
            EdgeType(
                name=name,
                description=str(et.get("description", "")),
                source_targets=pairs,
                attributes=_clean_attributes(et.get("attributes") or []),
                examples=[str(x) for x in (et.get("examples") or [])][:5],
            )
        )

    return Ontology(
        entity_types=entity_types,
        edge_types=edge_types,
        analysis_summary=str(raw.get("analysis_summary", "")),
    )


def build_ontology_prompt(text: str, purpose: str) -> list[dict[str, str]]:
    sample = _sample_text(text)
    user = f"分析目的：{purpose or '通用知识图谱构建'}\n\n文本：\n{sample}"
    return [
        {"role": "system", "content": _ONTOLOGY_SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _sample_text(text: str) -> str:
    if len(text) <= _MAX_TEXT_FOR_PROMPT:
        return text
    # 均匀采样头部/中部/尾部
    head = text[: _MAX_TEXT_FOR_PROMPT // 3]
    mid_start = len(text) // 2 - _MAX_TEXT_FOR_PROMPT // 6
    mid = text[mid_start : mid_start + _MAX_TEXT_FOR_PROMPT // 3]
    tail = text[-_MAX_TEXT_FOR_PROMPT // 3 :]
    return f"{head}\n\n[...中间部分已省略...]\n\n{mid}\n\n[...后续部分已省略...]\n\n{tail}"


async def generate_ontology(
    llm: LLMClient, text: str, purpose: str, config: Config
) -> Ontology:
    """LLM 生成本体并规范化。

    mock provider 无真实模型：MockLLMClient 队列耗尽时启发式兜底——
    从文本中抽取高频大写词/中文名词作为实体类型占位，保证演示链路可用。
    """
    messages = build_ontology_prompt(text, purpose)
    try:
        raw = await llm.complete_json(messages)
    except LLMError:
        if config.llm_provider != "mock":
            raise
        raw = _mock_ontology_fallback(text)
    return normalize_ontology(raw, config)


def _mock_ontology_fallback(text: str) -> dict:
    """从文本词频生成演示用本体（仅 mock provider）。"""
    import re

    words = re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", text)
    freq: dict[str, int] = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    names = [w for w, c in sorted(freq.items(), key=lambda x: -x[1])[:4] if c >= 1]
    entity_types = [{"name": n, "description": f"高频概念 {n}"} for n in names] or [
        {"name": "Concept", "description": "通用概念"}
    ]
    return {
        "entity_types": entity_types,
        "edge_types": [
            {"name": "related to", "source_targets": [{"source": names[0], "target": names[1]}] if len(names) > 1 else []}
        ],
        "analysis_summary": "mock 兜底本体（基于词频启发式）",
    }


def ontology_to_json(ontology: Ontology) -> str:
    return json.dumps(ontology.model_dump(), ensure_ascii=False, indent=2)