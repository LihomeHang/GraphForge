"""逐块实体/关系抽取（并发 + JSON 修复重试 + 块级失败隔离）。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging

from app.config import Config
from app.llm.client import LLMClient, LLMError, LLMJsonError, emit_task_log, parse_json_loose
from app.models.graph import ExtractionResult, ExtractEntity, ExtractRelation
from app.models.ontology import Ontology
from app.storage.tasks import TaskStore

logger = logging.getLogger("graphforge.extractor")

_SYSTEM_PROMPT = """你是一位知识图谱信息抽取专家。给定文本与本体（ontology），抽取文本中出现的实体与关系。

要求：
1. 只使用本体中定义的实体类型（entity_types[].name）和关系类型（edge_types[].name）。
2. entities：文本中明确出现的实体，含 name（原文中的名称）、type（本体实体类型）、summary（一句话描述该实体在此文本中的信息）、attributes（仅本体中定义过的属性名，无则为空对象）。
3. relations：实体间的关系，含 source（源实体 name）、target（目标实体 name）、type（本体关系类型）、fact（一句自然语言事实，含主谓宾，体现原文依据）、attributes（可为空对象）。
4. source/target 必须引用 entities 中已列出的实体 name。
5. 不要臆造文本中没有的事实；某块没有可抽取内容时输出空的 entities/relations。
6. 严格输出 JSON 对象，不要输出任何其他文本：
{"entities": [...], "relations": [...]}
"""


def _ontology_compact(ontology: Ontology) -> str:
    return json.dumps(ontology.model_dump(), ensure_ascii=False)


def _build_user_prompt(chunk: str, ontology: Ontology) -> str:
    return (
        f"本体：\n{_ontology_compact(ontology)}\n\n文本：\n{chunk}\n\n"
        f"请抽取实体与关系，严格输出 JSON。"
    )


def chunk_hash(graph_id: str, ontology_json: str, chunk: str) -> str:
    """块级缓存键：graph_id + 本体 hash + 块内容 hash。"""
    h1 = hashlib.sha256(ontology_json.encode("utf-8")).hexdigest()[:16]
    h2 = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
    return f"{graph_id}:{h1}:{h2}"


async def _extract_one(
    llm: LLMClient,
    chunk: str,
    ontology: Ontology,
    ontology_json: str,
    config: Config,
    task_store: TaskStore | None,
) -> tuple[ExtractionResult | None, str | None]:
    """抽取单个块。返回 (结果, 错误信息)；结果为 None 表示彻底失败。"""
    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": _build_user_prompt(chunk, ontology)},
    ]
    last_error: str | None = None
    base_messages = messages
    for attempt in range(config.extract_max_retry + 1):
        try:
            raw = await llm.complete(messages)
            obj = parse_json_loose(raw)
            result = _validate_extraction(obj, ontology)
            return result, None
        except LLMJsonError as e:
            last_error = str(e)
            # JSON 修复重试：把解析错误喂回去让模型自修（在原始消息上追加，不累积污染）
            messages = [
                *base_messages,
                {"role": "assistant", "content": e.raw},
                {
                    "role": "user",
                    "content": (
                        f"你上一次的输出无法解析为 JSON：{e.inner}。"
                        f"请修复并重新输出，严格只输出合法 JSON 对象。"
                    ),
                },
            ]
        except LLMError as e:
            # mock provider 队列耗尽：无真实模型，启发式兜底保证演示链路可用
            if config.llm_provider == "mock":
                return _mock_extract_fallback(chunk, ontology), None
            last_error = str(e)
            messages = base_messages  # 网络/服务错误：回到原始消息重试
            await asyncio.sleep(1.0)
    return None, last_error


def _mock_extract_fallback(chunk: str, ontology: Ontology) -> ExtractionResult:
    """mock provider 专用：从块文本产出占位实体（仅 mock provider）。"""
    import re

    etype = ontology.entity_names()[0] if ontology.entity_names() else "Concept"
    # 取文本里的专有名词样片段做占位实体名（有则用，无则哈希截断）
    words = re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}|[\u4e00-\u9fa5]{2,6}", chunk)
    if words:
        seen: list[str] = []
        for w in words:
            if w not in seen:
                seen.append(w)
            if len(seen) >= 3:
                break
        names = seen
    else:
        names = [f"Chunk_{chunk_hash('x', '', chunk)[:8]}"]
    ents = [ExtractEntity(name=n, type=etype, summary=f"（mock 抽取）出现于文本") for n in names]
    rels: list[ExtractRelation] = []
    if len(names) >= 2 and ontology.edge_names():
        rels.append(
            ExtractRelation(
                source=names[0], target=names[1],
                type=ontology.edge_names()[0], fact=f"{names[0]} 与 {names[1]} 存在关联",
            )
        )
    return ExtractionResult(entities=ents, relations=rels)


def _validate_extraction(obj: dict, ontology: Ontology) -> ExtractionResult:
    """校验 LLM 抽取输出：过滤非法类型引用与坏记录。

    类型名匹配大小写不敏感（LLM 常输出 'person'/'WORKS AT' 等变体），
    统一 canonical 化到本体定义名。
    """
    entity_type_map = {n.casefold(): n for n in ontology.entity_names()}
    edge_type_map = {n.casefold(): n for n in ontology.edge_names()}

    def _canon(t: str, m: dict[str, str]) -> str | None:
        return m.get(t.strip().casefold().replace(" ", "_"))

    entities: list[ExtractEntity] = []
    seen_names: set[str] = set()
    for e in obj.get("entities") or []:
        if not isinstance(e, dict):
            continue
        name = str(e.get("name", "")).strip()
        etype = _canon(str(e.get("type", "")), entity_type_map)
        if not name or etype is None:
            continue
        if name.casefold() in seen_names:
            continue  # 同块内同名实体只保留第一个（消歧阶段再全局合并）
        seen_names.add(name.casefold())
        attrs = e.get("attributes") or {}
        entities.append(
            ExtractEntity(
                name=name,
                type=etype,
                summary=str(e.get("summary", "")),
                attributes=attrs if isinstance(attrs, dict) else {},
            )
        )

    relations: list[ExtractRelation] = []
    for r in obj.get("relations") or []:
        if not isinstance(r, dict):
            continue
        src = str(r.get("source", "")).strip()
        tgt = str(r.get("target", "")).strip()
        rtype = _canon(str(r.get("type", "")), edge_type_map)
        fact = str(r.get("fact", "")).strip()
        if not src or not tgt or rtype is None or not fact:
            continue
        if src.casefold() not in seen_names or tgt.casefold() not in seen_names:
            continue  # source/target 必须引用本块实体
        attrs = r.get("attributes") or {}
        relations.append(
            ExtractRelation(
                source=src,
                target=tgt,
                type=rtype,
                fact=fact,
                attributes=attrs if isinstance(attrs, dict) else {},
            )
        )

    return ExtractionResult(entities=entities, relations=relations)


async def extract_chunks(
    llm: LLMClient,
    chunks: list[str],
    ontology: Ontology,
    ontology_json: str,
    graph_id: str,
    config: Config,
    task_store: TaskStore | None = None,
    progress_cb=None,
) -> tuple[list[ExtractionResult], list[str]]:
    """并发抽取所有块（Semaphore 限流）。

    返回 (结果列表（与 chunks 对齐，失败块为空结果）, 警告列表)。
    块级失败隔离：跳过失败块并记录警告，不中断整个任务。
    带块级缓存（extract_cache），幂等重跑不重复计费。
    """
    semaphore = asyncio.Semaphore(config.llm_concurrency)
    warnings: list[str] = []
    results: list[ExtractionResult | None] = [None] * len(chunks)

    async def _work(i: int, chunk: str) -> None:
        async with semaphore:
            key = chunk_hash(graph_id, ontology_json, chunk)
            cached = await task_store.get_extract_cache(key) if task_store else None
            if cached is not None:
                results[i] = ExtractionResult.model_validate(cached)
                return
            result, error = await _extract_one(llm, chunk, ontology, ontology_json, config, task_store)
            if result is None:
                warnings.append(f"块 {i} 抽取失败已跳过: {error}")
                logger.warning("块 %s 抽取失败已跳过: %s", i, error)
                emit_task_log(f"块 {i} 抽取失败已跳过: {error}")
                results[i] = ExtractionResult()
            else:
                results[i] = result
                # 空结果不写缓存：可能是 LLM 响应错位/漂移导致，重跑应再给机会
                if task_store and (result.entities or result.relations):
                    await task_store.put_extract_cache(key, result.model_dump())
        if progress_cb:
            await progress_cb(i + 1, len(chunks))

    async def _noop(i: int, n: int) -> None:
        return None

    await asyncio.gather(*(_work(i, c) for i, c in enumerate(chunks)))
    return [r for r in results if r is not None], warnings
