"""实体消歧与合并（两阶段）+ summary 聚合。

阶段一：name.strip().casefold() 精确匹配自动合并，summary 拼接。
阶段二：同类型实体两两做嵌入相似度（> 阈值），候选对交 LLM 判定"是否指同一实体"，
        判定为同则合并（summary 由 LLM 融合成一段）。
"""
from __future__ import annotations

import asyncio
import logging

from app.config import Config
from app.llm.client import LLMClient, LLMError
from app.llm.embeddings import EmbeddingClient
from app.models.graph import Entity, ExtractionResult, ExtractEntity, ExtractRelation, Relation

logger = logging.getLogger("graphforge.resolver")

_MERGE_SYSTEM_PROMPT = """你是知识图谱实体消歧专家。判断两个实体描述是否指同一现实世界对象。

规则：
- 名称相似但指代不同对象（如同名不同人、简称指向不同机构）→ 不同实体。
- 名称不同但明显是同一对象（全称/简称、别名、译名）→ 同一实体。
- 严格输出 JSON：{"same": true|false, "summary": "合并后的单段描述（仅 same=true 时需要）"}
"""


async def _llm_same_entity(
    llm: LLMClient, name_a: str, summary_a: str, name_b: str, summary_b: str
) -> tuple[bool, str]:
    """LLM 判定两个实体是否同一；返回 (是否同一, 合并 summary)。"""
    messages = [
        {"role": "system", "content": _MERGE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"实体 A：\n名称：{name_a}\n描述：{summary_a or '（无）'}\n\n"
                f"实体 B：\n名称：{name_b}\n描述：{summary_b or '（无）'}\n\n"
                f"它们是否指同一实体？"
            ),
        },
    ]
    try:
        obj = await llm.complete_json(messages)
    except LLMError:
        return False, ""  # 判定失败宁可保守不合并
    same = bool(obj.get("same", False))
    summary = str(obj.get("summary", "")) if same else ""
    return same, summary


async def _merge_summaries_llm(llm: LLMClient, name: str, summaries: list[str]) -> str:
    """把同一实体的多段 summary 融合成单段。失败时退化为拼接。"""
    if not summaries:
        return ""
    if len(summaries) == 1:
        return summaries[0]
    joined = "\n".join(f"- {s}" for s in summaries if s)
    messages = [
        {
            "role": "system",
            "content": "你是文本融合专家。把同一实体的多条描述合并成一段连贯、无重复的中文描述（不超过 150 字）。严格输出 JSON：{\"summary\": \"...\"}",
        },
        {"role": "user", "content": f"实体：{name}\n描述列表：\n{joined}"},
    ]
    try:
        obj = await llm.complete_json(messages)
        s = str(obj.get("summary", "")).strip()
        return s or " ".join(summaries)
    except LLMError:
        return " ".join(summaries)


class ResolutionResult:
    def __init__(self, entities: list[Entity], relations: list[Relation]):
        self.entities = entities
        self.relations = relations


async def resolve(
    extraction_results: list[ExtractionResult],
    llm: LLMClient,
    embeddings: EmbeddingClient,
    config: Config,
) -> ResolutionResult:
    """两阶段消歧合并，产出最终 Entity / Relation 列表。

    关系 source/target 用实体 name 引用；合并后统一映射到代表实体的 uuid。
    """
    # ---- 收集全部实体出现 ----
    occurrences: dict[str, ExtractEntity] = {}  # key: casefold(name) -> 代表
    order: list[str] = []
    name_type: dict[str, str] = {}
    summaries: dict[str, list[str]] = {}

    for res in extraction_results:
        for e in res.entities:
            key = e.name.strip().casefold()
            if key not in occurrences:
                occurrences[key] = e
                order.append(key)
                name_type[key] = e.type
                summaries[key] = []
            summaries[key].append(e.summary)

    # ---- 阶段二：同类型嵌入相似候选 + LLM 判定 ----
    keys = [k for k in order]
    rep_key: dict[str, str] = {k: k for k in keys}  # key -> 代表 key
    merged_summaries: dict[str, str] = {}  # 保留 key -> LLM 融合 summary（keys<=1 时也可安全访问）

    if len(keys) > 1:
        texts = [
            f"{occurrences[k].name}\n{occurrences[k].summary}" if occurrences[k].summary else occurrences[k].name
            for k in keys
        ]
        vectors = await embeddings.embed(texts)
        cand_pairs: list[tuple[int, int]] = []
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                if name_type[keys[i]] != name_type[keys[j]]:
                    continue
                sim = _cosine(vectors[i], vectors[j])
                if sim > config.resolve_sim_threshold:
                    cand_pairs.append((i, j))

        # 并发 LLM 判定（限流）
        semaphore = asyncio.Semaphore(config.llm_concurrency)
        merge_map: dict[str, str] = {}  # 被合并 key -> 保留 key

        async def _judge(i: int, j: int) -> None:
            async with semaphore:
                a, b = occurrences[keys[i]], occurrences[keys[j]]
                # 已经并入其他簇的跳过（简化：并查在判定后统一处理）
                same, summary = await _llm_same_entity(llm, a.name, a.summary, b.name, b.summary)
                if same:
                    keep, drop = keys[i], keys[j]
                    merge_map[drop] = keep
                    if summary:
                        merged_summaries.setdefault(keep, summary)

        await asyncio.gather(*(_judge(i, j) for i, j in cand_pairs))

        def find(k: str) -> str:
            while rep_key[k] != k:
                rep_key[k] = rep_key[rep_key[k]]
                k = rep_key[k]
            return k

        for drop, keep in merge_map.items():
            rep_key[drop] = find(keep)

    # ---- 构建最终实体 ----
    cluster_members: dict[str, list[str]] = {}
    for k in keys:
        cluster_members.setdefault(rep_key[k], []).append(k)

    # summary 聚合：多成员簇并发调 LLM 融合（原先逐簇串行，簇多时明显拖慢构建）
    merge_sem = asyncio.Semaphore(config.llm_concurrency)
    pending_merges: dict[str, list[str]] = {}
    for rep, members in cluster_members.items():
        if len(members) > 1 and not merged_summaries.get(rep):
            texts = [s for m in members for s in summaries[m] if s]
            if texts:
                pending_merges[rep] = texts

    async def _merge_cluster(rep: str) -> None:
        try:
            async with merge_sem:
                merged_summaries[rep] = await _merge_summaries_llm(
                    llm, occurrences[rep].name, pending_merges[rep]
                )
        except Exception as e:  # noqa: BLE001  聚合失败退化为拼接，不中断构建
            logger.warning("实体 %s summary 聚合失败: %s", occurrences[rep].name, e)

    if pending_merges:
        await asyncio.gather(*(_merge_cluster(rep) for rep in pending_merges))

    final_entities: list[Entity] = []
    final_name: dict[str, Entity] = {}  # casefold(name) -> 最终实体
    for rep, members in cluster_members.items():
        rep_occ = occurrences[rep]
        all_summaries: list[str] = []
        for m in members:
            all_summaries.extend(summaries[m])
        # summary：阶段二 LLM 融合结果优先，否则聚合阶段的融合结果，再退化为多段拼接
        if len(members) == 1:
            summary = " ".join(s for s in all_summaries if s)
        else:
            summary = merged_summaries.get(rep) or " ".join(s for s in all_summaries if s)
        attrs: dict = {}
        for m in members:
            attrs.update(occurrences[m].attributes)
        ent = Entity(
            name=rep_occ.name,
            type=rep_occ.type,
            summary=summary,
            attributes=attrs,
        )
        final_entities.append(ent)
        for m in members:
            final_name[m] = ent

    # ---- 构建关系（映射到最终实体 uuid）----
    final_relations: list[Relation] = []
    for res in extraction_results:
        for r in res.relations:
            src_key = r.source.strip().casefold()
            tgt_key = r.target.strip().casefold()
            src_ent = final_name.get(src_key)
            tgt_ent = final_name.get(tgt_key)
            if src_ent is None or tgt_ent is None:
                continue
            final_relations.append(
                Relation(
                    name=r.type,
                    fact=r.fact,
                    source=r.source,
                    target=r.target,
                    source_node_uuid=src_ent.uuid,
                    target_node_uuid=tgt_ent.uuid,
                    attributes=r.attributes,
                )
            )

    return ResolutionResult(entities=final_entities, relations=final_relations)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
