"""逐块实体/关系抽取（并发 + JSON 修复重试 + 块级失败隔离）。"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging

from app.config import Config
from app.llm.client import LLMClient, LLMError, LLMJsonError, emit_task_log, parse_json_loose
from app.models.graph import ExtractionResult, ExtractEntity, ExtractRelation
from app.models.ontology import Ontology
from app.storage.tasks import TaskStore

logger = logging.getLogger("graphforge.extractor")

_STRICT_SYSTEM_PROMPT = """你是一位知识图谱信息抽取专家。给定文本与本体（ontology），抽取文本中出现的实体与关系。

要求：
1. 只使用本体中定义的实体类型（entity_types[].name）和关系类型（edge_types[].name）。
2. entities：文本中明确出现的实体，含 name（原文中的名称）、type（本体实体类型）、summary（一句话描述该实体在此文本中的信息）、attributes（仅本体中定义过的属性名，无则为空对象）。
3. relations：实体间的关系，含 source（源实体 name）、target（目标实体 name）、type（本体关系类型）、fact（一句自然语言事实，含主谓宾，体现原文依据）、attributes（可为空对象）。
4. source/target 必须引用 entities 中已列出的实体 name。
5. 不要臆造文本中没有的事实；某块没有可抽取内容时输出空的 entities/relations。
6. 严格输出 JSON 对象，不要输出任何其他文本：
{"entities": [...], "relations": [...]}
"""

_SOFT_SYSTEM_PROMPT = """你是一位知识图谱信息抽取专家。给定文本与扩展本体（ontology），完整抽取文本中出现的实体与关系。

要求：
1. 本体中的自定义类型是优先分类，不是白名单。匹配时使用自定义类型；不匹配的实体使用 Entity，不匹配的关系使用 RELATED_TO。
2. 不得因为实体是技术概念、主题、事件、方法或抽象知识而省略它。
3. entities：文本中明确出现的实体，含 name、type、summary、attributes。
4. relations：实体间的事实，含 source、target、type、fact、attributes；source/target 应引用 entities 中的名称。
5. 不要臆造文本中没有的事实；某块没有可抽取内容时输出空数组。
6. 严格输出 JSON 对象，不要输出任何其他文本：
{"entities": [...], "relations": [...]}
"""


def _system_prompt(ontology_mode: str) -> str:
    return _SOFT_SYSTEM_PROMPT if ontology_mode == "soft" else _STRICT_SYSTEM_PROMPT


def _ontology_compact(ontology: Ontology, ontology_mode: str = "strict") -> str:
    """只发送抽取所需的 schema，避免每个块重复携带 examples/summary。"""
    entity_types = [
        {
            "name": item.name,
            "description": item.description,
            "attributes": [a.name for a in item.attributes],
        }
        for item in ontology.entity_types
    ]
    edge_types = [
        {
            "name": item.name,
            "description": item.description,
            "source_targets": [pair.model_dump() for pair in item.source_targets],
            "attributes": [a.name for a in item.attributes],
        }
        for item in ontology.edge_types
    ]
    if ontology_mode == "soft":
        if not any(item["name"].casefold() == "entity" for item in entity_types):
            entity_types.append(
                {"name": "Entity", "description": "不匹配自定义类型的通用实体", "attributes": []}
            )
        if not any(item["name"].casefold() == "related_to" for item in edge_types):
            edge_types.append(
                {
                    "name": "RELATED_TO",
                    "description": "不匹配自定义类型的通用事实关系",
                    "source_targets": [{"source": "Entity", "target": "Entity"}],
                    "attributes": [],
                }
            )
    payload = {"entity_types": entity_types, "edge_types": edge_types}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _build_user_prompt(chunk: str, ontology: Ontology, ontology_mode: str = "strict") -> str:
    return (
        f"本体：\n{_ontology_compact(ontology, ontology_mode)}\n\n文本：\n{chunk}\n\n"
        f"请抽取实体与关系，严格输出 JSON。"
    )


def chunk_hash(
    graph_id: str,
    ontology_json: str,
    chunk: str,
    ontology_mode: str = "strict",
) -> str:
    """块级缓存键：同一文本/本体可跨图谱复用，结果不含 graph_id。"""
    ontology_key = ontology_json if ontology_mode == "strict" else f"soft\0{ontology_json}"
    h1 = hashlib.sha256(ontology_key.encode("utf-8")).hexdigest()[:16]
    h2 = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
    return f"{h1}:{h2}"


def _build_batch_user_prompt(
    chunks: list[tuple[int, str]],
    ontology: Ontology,
    ontology_mode: str = "strict",
) -> str:
    items = [{"index": i, "text": text} for i, text in chunks]
    return (
        f"本体：\n{_ontology_compact(ontology, ontology_mode)}\n\n"
        f"待抽取文本块（必须逐个返回相同 index）：\n"
        f"{json.dumps(items, ensure_ascii=False)}\n\n"
        "请严格输出 JSON：{\"results\":[{\"index\":0,\"entities\":[],\"relations\":[]}]}."
    )


async def _extract_batch(
    llm: LLMClient,
    chunks: list[tuple[int, str]],
    ontology: Ontology,
    config: Config,
    ontology_mode: str = "strict",
) -> dict[int, ExtractionResult]:
    """批量抽取；响应不完整时返回已校验的子集，由调用方逐块回退。"""
    base_messages = [
        {
            "role": "system",
            "content": _system_prompt(ontology_mode) + "\n一次处理多个文本块，结果放入 results 数组。",
        },
        {"role": "user", "content": _build_batch_user_prompt(chunks, ontology, ontology_mode)},
    ]
    messages = base_messages
    allowed_indices = {i for i, _ in chunks}
    for attempt in range(config.extract_max_retry + 1):
        try:
            raw = await llm.complete(messages)
            obj = parse_json_loose(raw)
            out: dict[int, ExtractionResult] = {}
            for item in obj.get("results") or []:
                if not isinstance(item, dict):
                    continue
                try:
                    index = int(item["index"])
                except (KeyError, TypeError, ValueError):
                    continue
                if index not in allowed_indices:
                    continue
                out[index] = _validate_extraction(
                    item, ontology, ontology_mode=ontology_mode
                )
            return out
        except LLMJsonError as e:
            messages = [
                *base_messages,
                {"role": "assistant", "content": e.raw},
                {"role": "user", "content": f"上次输出不是合法 JSON（{e.inner}），请只输出修复后的 JSON。"},
            ]
        except LLMError:
            return {}
    return {}


async def _extract_one(
    llm: LLMClient,
    chunk: str,
    ontology: Ontology,
    ontology_json: str,
    config: Config,
    task_store: TaskStore | None,
    ontology_mode: str = "strict",
) -> tuple[ExtractionResult | None, str | None]:
    """抽取单个块。返回 (结果, 错误信息)；结果为 None 表示彻底失败。"""
    messages = [
        {"role": "system", "content": _system_prompt(ontology_mode)},
        {"role": "user", "content": _build_user_prompt(chunk, ontology, ontology_mode)},
    ]
    last_error: str | None = None
    base_messages = messages
    for attempt in range(config.extract_max_retry + 1):
        try:
            raw = await llm.complete(messages)
            obj = parse_json_loose(raw)
            result = _validate_extraction(obj, ontology, ontology_mode=ontology_mode)
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
                return _mock_extract_fallback(chunk, ontology, ontology_mode), None
            last_error = str(e)
            messages = base_messages  # 网络/服务错误：回到原始消息重试
            await asyncio.sleep(1.0)
    return None, last_error


def _mock_extract_fallback(
    chunk: str, ontology: Ontology, ontology_mode: str = "strict"
) -> ExtractionResult:
    """mock provider 专用：从块文本产出占位实体（仅 mock provider）。"""
    import re

    etype = "Entity" if ontology_mode == "soft" else (
        ontology.entity_names()[0] if ontology.entity_names() else "Concept"
    )
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
        names = [f"Chunk_{chunk_hash('x', '', chunk, ontology_mode)[:8]}"]
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


def _validate_extraction(
    obj: dict,
    ontology: Ontology,
    ontology_mode: str = "strict",
) -> ExtractionResult:
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
        if etype is None and ontology_mode == "soft":
            etype = "Entity"
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
        if rtype is None and ontology_mode == "soft":
            rtype = "RELATED_TO"
        fact = str(r.get("fact", "")).strip()
        if not src or not tgt or rtype is None or not fact:
            continue
        if ontology_mode == "soft":
            for endpoint in (src, tgt):
                endpoint_key = endpoint.casefold()
                if endpoint_key not in seen_names:
                    entities.append(ExtractEntity(name=endpoint, type="Entity"))
                    seen_names.add(endpoint_key)
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
    chunk_result_cb=None,
    ontology_mode: str = "strict",
) -> tuple[list[ExtractionResult], list[str]]:
    """并发抽取所有块（Semaphore 限流）。

    返回 (结果列表（与 chunks 对齐，失败块为空结果）, 警告列表)。
    块级失败隔离：跳过失败块并记录警告，不中断整个任务。
    带块级缓存（extract_cache），幂等重跑不重复计费。
    chunk_result_cb(i, result)：每块出结果（含缓存命中）时回调，供实时预览。
    """
    semaphore = asyncio.Semaphore(config.llm_concurrency)
    warnings: list[str] = []
    results: list[ExtractionResult | None] = [None] * len(chunks)
    done = 0  # 实际完成数：并发下完成顺序 ≠ 块序，必须计数保证进度单调不回退

    async def _work_batch(batch: list[tuple[int, str]]) -> None:
        nonlocal done
        async with semaphore:
            batch_results: dict[int, ExtractionResult] = {}
            missing: list[tuple[int, str]] = []
            for i, chunk in batch:
                key = chunk_hash(graph_id, ontology_json, chunk, ontology_mode)
                cached = await task_store.get_extract_cache(key) if task_store else None
                if cached is None and task_store:
                    # 兼容旧版 graph_id:key 格式；命中后写入新键，完成惰性迁移。
                    cached = await task_store.get_extract_cache(f"{graph_id}:{key}")
                    if cached is not None:
                        await task_store.put_extract_cache(key, cached)
                if cached is not None:
                    batch_results[i] = ExtractionResult.model_validate(cached)
                else:
                    missing.append((i, chunk))
            if len(missing) == 1:
                i, chunk = missing[0]
                result, error = await _extract_one(
                    llm, chunk, ontology, ontology_json, config, task_store, ontology_mode
                )
                if result is not None:
                    batch_results[i] = result
                else:
                    warnings.append(f"块 {i} 抽取失败已跳过: {error}")
                    logger.warning("块 %s 抽取失败已跳过: %s", i, error)
                    emit_task_log(f"块 {i} 抽取失败已跳过: {error}")
            elif len(missing) > 1:
                batch_results.update(
                    await _extract_batch(llm, missing, ontology, config, ontology_mode)
                )
                # 批量响应缺块时逐块回退，保证失败隔离和进度完整。
                for i, chunk in missing:
                    if i in batch_results:
                        continue
                    result, error = await _extract_one(
                        llm, chunk, ontology, ontology_json, config, task_store, ontology_mode
                    )
                    if result is not None:
                        batch_results[i] = result
                    else:
                        warnings.append(f"块 {i} 抽取失败已跳过: {error}")
                        logger.warning("块 %s 抽取失败已跳过: %s", i, error)
                        emit_task_log(f"块 {i} 抽取失败已跳过: {error}")

            for i, chunk in batch:
                result = batch_results.get(i) or ExtractionResult()
                results[i] = result
                key = chunk_hash(graph_id, ontology_json, chunk, ontology_mode)
                if task_store and (result.entities or result.relations):
                    await task_store.put_extract_cache(key, result.model_dump())
                done += 1
                if chunk_result_cb:
                    callback_result = chunk_result_cb(i, result)
                    if inspect.isawaitable(callback_result):
                        await callback_result
                if progress_cb:
                    await progress_cb(done, len(chunks))

    batch_size = max(1, config.extract_batch_size)
    batches = [
        list(enumerate(chunks))[start : start + batch_size]
        for start in range(0, len(chunks), batch_size)
    ]
    await asyncio.gather(*(_work_batch(batch) for batch in batches))
    return [r for r in results if r is not None], warnings
