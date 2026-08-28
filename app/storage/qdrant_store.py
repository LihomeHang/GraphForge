"""Qdrant 向量读写（per-graph collection）。"""
from __future__ import annotations

import logging
from typing import Any

from qdrant_client import AsyncQdrantClient, models

from app.config import Config
from app.models.graph import Entity, Relation

logger = logging.getLogger("graphforge.qdrant")


def collection_name(graph_id: str) -> str:
    return f"graphforge_{graph_id}"


class QdrantStore:
    def __init__(self, config: Config):
        self.config = config
        kwargs: dict[str, Any] = {"url": config.qdrant_url}
        if config.qdrant_api_key:
            kwargs["api_key"] = config.qdrant_api_key
        self._client = AsyncQdrantClient(**kwargs)

    async def verify(self) -> None:
        # 触发一次集合列表请求以探活
        await self._client.get_collections()

    async def close(self) -> None:
        await self._client.close()

    async def ensure_collection(self, graph_id: str, dim: int) -> None:
        name = collection_name(graph_id)
        if not await self._client.collection_exists(name):
            await self._client.create_collection(
                collection_name=name,
                vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
            )

    async def delete_collection(self, graph_id: str) -> None:
        name = collection_name(graph_id)
        if await self._client.collection_exists(name):
            await self._client.delete_collection(name)

    async def upsert_nodes(self, graph_id: str, entities: list[Entity], vectors: list[list[float]]) -> None:
        points = [
            models.PointStruct(
                id=e.uuid,
                vector=vectors[i],
                payload={
                    "type": "node",
                    "uuid": e.uuid,
                    "name": e.name,
                    "summary": e.summary,
                },
            )
            for i, e in enumerate(entities)
        ]
        await self._client.upsert(collection_name(graph_id), points=points)

    async def upsert_edges(self, graph_id: str, relations: list[Relation], vectors: list[list[float]]) -> None:
        points = [
            models.PointStruct(
                id=r.uuid,
                vector=vectors[i],
                payload={
                    "type": "edge",
                    "uuid": r.uuid,
                    "fact": r.fact,
                    "source_node_uuid": r.source_node_uuid,
                    "target_node_uuid": r.target_node_uuid,
                },
            )
            for i, r in enumerate(relations)
        ]
        await self._client.upsert(collection_name(graph_id), points=points)

    async def search(
        self,
        graph_id: str,
        query_vector: list[float],
        top_k: int = 10,
        types: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        name = collection_name(graph_id)
        query_filter = None
        if types:
            conditions = [
                models.FieldCondition(key="type", match=models.MatchValue(value=t))
                for t in types
            ]
            # type 命中任一即可：单类型用 must，多类型用 should（must 是 AND 语义，
            # 两个 type 条件 AND 在一起永远为假）
            if len(conditions) == 1:
                query_filter = models.Filter(must=conditions)
            else:
                query_filter = models.Filter(should=conditions)
        results = await self._client.query_points(
            collection_name=name,
            query=query_vector,
            limit=top_k,
            query_filter=query_filter,
        )
        # qdrant-client >= 1.10: query_points 返回 QueryResponse(points=[...])
        hits = getattr(results, "points", results)
        return [
            {
                "type": hit.payload.get("type", ""),
                "uuid": hit.payload.get("uuid", ""),
                "score": hit.score,
                "name": hit.payload.get("name", ""),
                "summary": hit.payload.get("summary", ""),
                "fact": hit.payload.get("fact", ""),
                "source_node_uuid": hit.payload.get("source_node_uuid", ""),
                "target_node_uuid": hit.payload.get("target_node_uuid", ""),
            }
            for hit in hits
        ]
