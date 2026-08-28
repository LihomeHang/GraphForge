"""写入：批量 MERGE Neo4j + 批量 upsert Qdrant。"""
from __future__ import annotations

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
) -> tuple[int, int]:
    """实体/关系写入图库与向量库。返回 (节点数, 边数)。"""
    entities = result.entities
    relations = result.relations

    for rel in relations:
        rel.graph_id = graph_id
    for ent in entities:
        ent.graph_id = graph_id

    # 1. Neo4j 实体 → 关系
    await neo4j.upsert_entities(entities, batch_size=config.neo4j_batch_size)
    await neo4j.upsert_relations(relations, batch_size=config.neo4j_batch_size)

    # 2. Qdrant 向量
    node_texts = [f"{e.name}\n{e.summary}" if e.summary else e.name for e in entities]
    edge_texts = [r.fact for r in relations]

    dim = await embeddings.dim()
    await qdrant.ensure_collection(graph_id, dim)

    node_vectors: list[list[float]] = []
    for batch in _chunks(node_texts, 64):
        node_vectors.extend(await embeddings.embed(batch))
    edge_vectors: list[list[float]] = []
    for batch in _chunks(edge_texts, 64):
        edge_vectors.extend(await embeddings.embed(batch))

    if entities:
        await qdrant.upsert_nodes(graph_id, entities, node_vectors)
    if relations:
        await qdrant.upsert_edges(graph_id, relations, edge_vectors)

    logger.info(
        "graph %s 写入完成: %s 节点 / %s 边", graph_id, len(entities), len(relations)
    )
    return len(entities), len(relations)


def _chunks(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]
