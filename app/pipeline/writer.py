"""写入：批量 MERGE Neo4j + 批量 upsert Qdrant。"""
from __future__ import annotations

import asyncio
import logging

from app.config import Config
from app.llm.embeddings import EmbeddingClient
from app.models.graph import Entity, Relation
from app.pipeline.resolver import ResolutionResult
from app.storage.neo4j_store import Neo4jStore
from app.storage.qdrant_store import QdrantStore

logger = logging.getLogger("graphforge.writer")


async def write_all(
    result: ResolutionResult,
    graph_id: str,
    neo4j: Neo4jStore,
    qdrant: QdrantStore,
    embeddings: EmbeddingClient,
    config: Config,
    replace_existing: bool = False,
) -> tuple[int, int]:
    """实体/关系写入图库与向量库。返回 (节点数, 边数)。"""
    entities = result.entities
    relations = result.relations

    for rel in relations:
        rel.graph_id = graph_id
    for ent in entities:
        ent.graph_id = graph_id

    # 1. 先生成全部向量；失败时不破坏已有图谱。
    node_texts = [f"{e.name}\n{e.summary}" if e.summary else e.name for e in entities]
    edge_texts = [r.fact for r in relations]

    dim = await embeddings.dim()

    async def _embed_all(texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for batch in _chunks(texts, 256):
            vectors.extend(await embeddings.embed(batch))
        return vectors

    node_vectors, edge_vectors = await asyncio.gather(
        _embed_all(node_texts), _embed_all(edge_texts)
    )

    # 2. 替换式重建在写入前清理两套存储，避免历史构建累积。
    if replace_existing:
        await neo4j.clear_graph_data(graph_id)
        await qdrant.delete_collection(graph_id)

    # 3. Neo4j 实体 → 关系
    await neo4j.upsert_entities(entities, batch_size=config.neo4j_batch_size)
    await neo4j.upsert_relations(relations, batch_size=config.neo4j_batch_size)

    # 4. Qdrant 向量
    await qdrant.ensure_collection(graph_id, dim)
    batch_size = config.qdrant_batch_size
    for start in range(0, len(entities), batch_size):
        end = start + batch_size
        await qdrant.upsert_nodes(graph_id, entities[start:end], node_vectors[start:end])
    for start in range(0, len(relations), batch_size):
        end = start + batch_size
        await qdrant.upsert_edges(graph_id, relations[start:end], edge_vectors[start:end])

    logger.info(
        "graph %s 写入完成: %s 节点 / %s 边", graph_id, len(entities), len(relations)
    )
    return len(entities), len(relations)


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]
