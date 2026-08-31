import pytest

from app.storage.neo4j_store import Neo4jStore


class _Result:
    async def consume(self):
        return None


class _Session:
    def __init__(self, queries):
        self.queries = queries

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def run(self, query, **_params):
        self.queries.append(" ".join(query.split()))
        return _Result()


class _Driver:
    def __init__(self):
        self.verified = False
        self.queries = []

    async def verify_connectivity(self):
        self.verified = True

    def session(self):
        return _Session(self.queries)


@pytest.mark.asyncio
async def test_verify_creates_lookup_indexes_for_bulk_graph_writes():
    store = object.__new__(Neo4jStore)
    store._driver = _Driver()

    await store.verify()

    assert store._driver.verified is True
    assert any("Entity" in query and "n.uuid" in query for query in store._driver.queries)
    assert any("GraphMeta" in query and "g.graph_id" in query for query in store._driver.queries)
    assert any("CALL db.awaitIndexes" in query for query in store._driver.queries)
