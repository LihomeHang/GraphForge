"""Neo4j 读写（MERGE 批量、label 管理、多图隔离靠 graph_id）。"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any, Iterable

from neo4j import AsyncGraphDatabase

from app.config import Config
from app.models.graph import EdgeNode, Entity, EntityNode, Relation

logger = logging.getLogger("graphforge.neo4j")

# Neo4j 属性不支持 map，attributes 统一序列化为 JSON 字符串存储。
_ATTR_KEY = "attributes_json"


def _safe_label(name: str) -> str:
    """校验并反引号包裹 label / 关系类型，防注入。"""
    if not name or not name.replace("_", "").isalnum():
        raise ValueError(f"非法 Neo4j 标签/关系类型: {name!r}")
    return f"`{name}`"


def _rows(entities: Iterable[Entity]) -> list[dict[str, Any]]:
    return [
        {
            "uuid": e.uuid,
            "props": {
                "name": e.name,
                "summary": e.summary,
                "graph_id": e.graph_id,
                "created_at": e.created_at,
                _ATTR_KEY: json.dumps(e.attributes, ensure_ascii=False),
            },
        }
        for e in entities
    ]


def _edge_rows(relations: Iterable[Relation]) -> list[dict[str, Any]]:
    return [
        {
            "uuid": r.uuid,
            "source": r.source_node_uuid,
            "target": r.target_node_uuid,
            "props": {
                "fact": r.fact,
                "name": r.name,
                "graph_id": r.graph_id,
                "created_at": r.created_at,
                _ATTR_KEY: json.dumps(r.attributes, ensure_ascii=False),
            },
        }
        for r in relations
    ]


def _chunks(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


class Neo4jStore:
    def __init__(self, config: Config):
        self.config = config
        self._driver = AsyncGraphDatabase.driver(
            config.neo4j_uri,
            auth=(config.neo4j_user, config.neo4j_password),
        )

    async def verify(self) -> None:
        await self._driver.verify_connectivity()
        statements = (
            "CREATE INDEX entity_uuid IF NOT EXISTS FOR (n:Entity) ON (n.uuid)",
            "CREATE INDEX entity_graph_id IF NOT EXISTS FOR (n:Entity) ON (n.graph_id)",
            "CREATE INDEX graph_meta_graph_id IF NOT EXISTS FOR (g:GraphMeta) ON (g.graph_id)",
            "CALL db.awaitIndexes(300)",
        )
        async with self._driver.session() as session:
            for statement in statements:
                result = await session.run(statement)
                await result.consume()

    async def close(self) -> None:
        await self._driver.close()

    # ---- GraphMeta ----

    async def create_graph(self, graph_id: str, name: str) -> None:
        async with self._driver.session() as s:
            await s.run(
                """
                MERGE (g:GraphMeta {graph_id: $graph_id})
                SET g.name = $name, g.created_at = $created_at, g.status = $status
                """,
                graph_id=graph_id,
                name=name,
                created_at=_now(),
                status="empty",
            )

    async def set_graph_status(self, graph_id: str, status: str, ontology_json: str | None = None) -> None:
        async with self._driver.session() as s:
            params: dict[str, Any] = {"graph_id": graph_id, "status": status}
            set_clause = "SET g.status = $status"
            if ontology_json is not None:
                set_clause += ", g.ontology_json = $ontology_json"
                params["ontology_json"] = ontology_json
            await s.run(f"MERGE (g:GraphMeta {{graph_id: $graph_id}}) {set_clause}", **params)

    async def list_graphs(self) -> list[dict[str, Any]]:
        async with self._driver.session() as s:
            result = await s.run(
                "MATCH (g:GraphMeta) RETURN g.graph_id AS graph_id, g.name AS name, "
                "g.status AS status, g.created_at AS created_at ORDER BY g.created_at DESC"
            )
            return [dict(r) for r in await result.data()]

    async def get_graph_meta(self, graph_id: str) -> dict[str, Any] | None:
        async with self._driver.session() as s:
            result = await s.run(
                "MATCH (g:GraphMeta {graph_id: $graph_id}) RETURN g", graph_id=graph_id
            )
            rec = await result.single()
            if rec is None:
                return None
            return dict(rec["g"])

    async def delete_graph(self, graph_id: str) -> None:
        await self.clear_graph_data(graph_id)
        async with self._driver.session() as s:
            await s.run("MATCH (g:GraphMeta {graph_id: $graph_id}) DELETE g", graph_id=graph_id)

    async def clear_graph_data(self, graph_id: str) -> None:
        """Delete graph entities and their relationships while retaining GraphMeta."""
        async with self._driver.session() as s:
            await s.run(
                "MATCH (n:Entity {graph_id: $graph_id}) DETACH DELETE n", graph_id=graph_id
            )

    # ---- 节点/边写入 ----

    async def upsert_entities(self, entities: list[Entity], batch_size: int = 500) -> None:
        by_type: dict[str, list[Entity]] = defaultdict(list)
        for e in entities:
            by_type[e.type].append(e)
        for label, group in by_type.items():
            safe = _safe_label(label)
            query = (
                f"UNWIND $rows AS row "
                f"MERGE (n:Entity:{safe} {{uuid: row.uuid}}) "
                f"SET n += row.props"
            )
            for batch in _chunks(_rows(group), batch_size):
                async with self._driver.session() as s:
                    await s.run(query, rows=batch)

    async def upsert_relations(self, relations: list[Relation], batch_size: int = 500) -> None:
        by_name: dict[str, list[Relation]] = defaultdict(list)
        for r in relations:
            by_name[r.name].append(r)
        for rel_name, group in by_name.items():
            safe = _safe_label(rel_name)
            query = (
                f"UNWIND $rows AS row "
                f"MATCH (s:Entity {{uuid: row.source}}) "
                f"MATCH (t:Entity {{uuid: row.target}}) "
                f"MERGE (s)-[r:{safe} {{uuid: row.uuid}}]->(t) "
                f"SET r += row.props"
            )
            for batch in _chunks(_edge_rows(group), batch_size):
                async with self._driver.session() as s:
                    await s.run(query, rows=batch)

    # ---- 节点/边读取 ----

    async def count_nodes_edges(self, graph_id: str) -> tuple[int, int]:
        async with self._driver.session() as s:
            n = await s.run(
                "MATCH (n:Entity {graph_id: $graph_id}) RETURN count(n) AS c", graph_id=graph_id
            )
            node_count = (await n.single())["c"]
            e = await s.run(
                "MATCH (n:Entity {graph_id: $graph_id})-[r]->() RETURN count(r) AS c",
                graph_id=graph_id,
            )
            edge_count = (await e.single())["c"]
        return int(node_count), int(edge_count)

    async def list_nodes(self, graph_id: str, offset: int = 0, limit: int = 100) -> list[EntityNode]:
        async with self._driver.session() as s:
            result = await s.run(
                """
                MATCH (n:Entity {graph_id: $graph_id})
                RETURN n ORDER BY n.name, n.uuid SKIP $offset LIMIT $limit
                """,
                graph_id=graph_id,
                offset=offset,
                limit=limit,
            )
            records = [rec async for rec in result]
            return [self._node_to_entity(rec["n"]) for rec in records]

    async def list_edges(self, graph_id: str, offset: int = 0, limit: int = 100) -> list[EdgeNode]:
        async with self._driver.session() as s:
            result = await s.run(
                """
                MATCH (s:Entity {graph_id: $graph_id})-[r]->(t:Entity {graph_id: $graph_id})
                RETURN r, s.uuid AS source_uuid, t.uuid AS target_uuid
                ORDER BY r.fact, r.uuid SKIP $offset LIMIT $limit
                """,
                graph_id=graph_id,
                offset=offset,
                limit=limit,
            )
            records = [rec async for rec in result]
            return [self._rel_to_edge(rec["r"], rec["source_uuid"], rec["target_uuid"]) for rec in records]

    async def get_node(self, uuid: str) -> dict[str, Any] | None:
        """节点详情：节点本身 + 出边 + 入边。"""
        async with self._driver.session() as s:
            result = await s.run(
                """
                MATCH (n:Entity {uuid: $uuid})
                OPTIONAL MATCH (n)-[r_out]->(t_out:Entity)
                OPTIONAL MATCH (s_in:Entity)-[r_in]->(n)
                RETURN n,
                       collect(DISTINCT {rel: r_out, other: t_out.uuid}) AS outgoing,
                       collect(DISTINCT {rel: r_in, other: s_in.uuid}) AS incoming
                """,
                uuid=uuid,
            )
            rec = await result.single()
            if rec is None:
                return None
            node = self._node_to_entity(rec["n"])
            outgoing = [
                {
                    **self._rel_to_edge(x["rel"], node.uuid, x["other"]).model_dump(),
                }
                for x in rec["outgoing"]
                if x["rel"] is not None
            ]
            incoming = [
                self._rel_to_edge(x["rel"], x["other"], node.uuid).model_dump()
                for x in rec["incoming"]
                if x["rel"] is not None
            ]
            return {"node": node.model_dump(), "outgoing": outgoing, "incoming": incoming}

    # ---- 内部转换 ----

    @staticmethod
    def _node_to_entity(node: Any) -> EntityNode:
        attrs_raw = node.get(_ATTR_KEY) or "{}"
        try:
            attrs = json.loads(attrs_raw)
        except (json.JSONDecodeError, TypeError):
            attrs = {}
        labels = [l for l in node.labels if l != "Entity"]
        return EntityNode(
            uuid=node["uuid"],
            name=node.get("name", ""),
            labels=["Entity", *labels],
            summary=node.get("summary", ""),
            attributes=attrs,
        )

    @staticmethod
    def _rel_to_edge(rel: Any, source_uuid: str, target_uuid: str) -> EdgeNode:
        attrs_raw = rel.get(_ATTR_KEY) or "{}"
        try:
            attrs = json.loads(attrs_raw)
        except (json.JSONDecodeError, TypeError):
            attrs = {}
        rel_type = rel.get("name") or rel.type
        return EdgeNode(
            uuid=rel["uuid"],
            name=rel_type,
            fact=rel.get("fact", ""),
            source_node_uuid=source_uuid,
            target_node_uuid=target_uuid,
            attributes=attrs,
        )


def _now() -> str:
    from app.models.graph import utc_now

    return utc_now()
