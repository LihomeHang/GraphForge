import asyncio

import pytest

from app.config import Config
from app.models.graph import Entity, Relation
from app.pipeline.resolver import ResolutionResult
from app.pipeline import writer


class _ConcurrentEmbeddings:
    def __init__(self):
        self.active = 0
        self.max_active = 0

    async def dim(self):
        return 2

    async def embed(self, texts):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0)
        self.active -= 1
        return [[1.0, 0.0] for _ in texts]


class _Neo4j:
    async def upsert_entities(self, entities, batch_size=500):
        return None

    async def upsert_relations(self, relations, batch_size=500):
        return None


class _Qdrant:
    def __init__(self):
        self.node_batch_sizes = []
        self.edge_batch_sizes = []

    async def ensure_collection(self, graph_id, dim):
        return None

    async def upsert_nodes(self, graph_id, entities, vectors):
        self.node_batch_sizes.append(len(entities))

    async def upsert_edges(self, graph_id, relations, vectors):
        self.edge_batch_sizes.append(len(relations))


class _ClearingNeo4j(_Neo4j):
    def __init__(self, events):
        self.events = events

    async def clear_graph_data(self, graph_id):
        self.events.append(("neo4j.clear", graph_id))

    async def upsert_entities(self, entities, batch_size=500):
        self.events.append(("neo4j.entities", entities[0].graph_id))


class _ClearingQdrant(_Qdrant):
    def __init__(self, events):
        super().__init__()
        self.events = events

    async def delete_collection(self, graph_id):
        self.events.append(("qdrant.clear", graph_id))

    async def ensure_collection(self, graph_id, dim):
        self.events.append(("qdrant.ensure", graph_id))


@pytest.mark.asyncio
async def test_writer_embeds_nodes_and_edges_concurrently():
    embeddings = _ConcurrentEmbeddings()
    result = ResolutionResult(
        [Entity(name="A", type="Person")],
        [Relation(name="RELATED_TO", fact="A relates B")],
    )
    await writer.write_all(result, "g1", _Neo4j(), _Qdrant(), embeddings, Config(neo4j_password="x"))
    assert embeddings.max_active >= 2


@pytest.mark.asyncio
async def test_writer_batches_qdrant_upserts():
    embeddings = _ConcurrentEmbeddings()
    qdrant = _Qdrant()
    result = ResolutionResult(
        [Entity(name=f"E{i}", type="Person") for i in range(5)],
        [Relation(name="RELATED_TO", fact=f"fact-{i}") for i in range(5)],
    )

    await writer.write_all(
        result,
        "g1",
        _Neo4j(),
        qdrant,
        embeddings,
        Config(neo4j_password="x", qdrant_batch_size=2),
    )

    assert qdrant.node_batch_sizes == [2, 2, 1]
    assert qdrant.edge_batch_sizes == [2, 2, 1]


@pytest.mark.asyncio
async def test_writer_replace_existing_clears_both_stores_before_upsert():
    events = []
    result = ResolutionResult([Entity(name="A", type="Entity")], [])

    await writer.write_all(
        result,
        "g1",
        _ClearingNeo4j(events),
        _ClearingQdrant(events),
        _ConcurrentEmbeddings(),
        Config(neo4j_password="x"),
        replace_existing=True,
    )

    assert events[:3] == [
        ("neo4j.clear", "g1"),
        ("qdrant.clear", "g1"),
        ("neo4j.entities", "g1"),
    ]
