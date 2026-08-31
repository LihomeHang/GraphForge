"""API 全端点冒烟测试（mock LLM + fake 存储，不依赖真实 Neo4j/Qdrant）。"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.llm.client import MockLLMClient
from app.llm.embeddings import MockEmbeddingClient
from app.pipeline.runner import Services
from app.storage.tasks import TaskStore


class FakeNeo4jStore:
    """内存 Neo4j 替身。"""

    def __init__(self) -> None:
        self.graphs: dict[str, dict[str, Any]] = {}
        self.nodes: dict[str, list[dict]] = {}
        self.edges: dict[str, list[dict]] = {}

    async def verify(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def create_graph(self, graph_id: str, name: str) -> None:
        self.graphs[graph_id] = {"graph_id": graph_id, "name": name, "status": "empty", "created_at": "2026-01-01T00:00:00+00:00"}

    async def set_graph_status(self, graph_id: str, status: str, ontology_json: str | None = None) -> None:
        self.graphs.setdefault(graph_id, {"graph_id": graph_id})["status"] = status
        if ontology_json is not None:
            self.graphs[graph_id]["ontology_json"] = ontology_json

    async def list_graphs(self) -> list[dict[str, Any]]:
        return list(self.graphs.values())

    async def get_graph_meta(self, graph_id: str) -> dict[str, Any] | None:
        return self.graphs.get(graph_id)

    async def delete_graph(self, graph_id: str) -> None:
        self.graphs.pop(graph_id, None)
        self.nodes.pop(graph_id, None)
        self.edges.pop(graph_id, None)

    async def clear_graph_data(self, graph_id: str) -> None:
        self.nodes.pop(graph_id, None)
        self.edges.pop(graph_id, None)

    async def upsert_entities(self, entities, batch_size: int = 500) -> None:
        for e in entities:
            self.nodes.setdefault(e.graph_id, []).append(
                {"uuid": e.uuid, "name": e.name, "type": e.type, "summary": e.summary, "attributes": e.attributes}
            )

    async def upsert_relations(self, relations, batch_size: int = 500) -> None:
        for r in relations:
            self.edges.setdefault(r.graph_id, []).append(
                {"uuid": r.uuid, "name": r.name, "fact": r.fact, "source_node_uuid": r.source_node_uuid, "target_node_uuid": r.target_node_uuid}
            )

    async def count_nodes_edges(self, graph_id: str) -> tuple[int, int]:
        return len(self.nodes.get(graph_id, [])), len(self.edges.get(graph_id, []))

    async def list_nodes(self, graph_id: str, offset: int = 0, limit: int = 100):
        from app.models.graph import EntityNode

        return [
            EntityNode(uuid=n["uuid"], name=n["name"], labels=["Entity", n["type"]], summary=n["summary"], attributes=n["attributes"])
            for n in self.nodes.get(graph_id, [])[offset : offset + limit]
        ]

    async def list_edges(self, graph_id: str, offset: int = 0, limit: int = 100):
        from app.models.graph import EdgeNode

        return [
            EdgeNode(**e)
            for e in self.edges.get(graph_id, [])[offset : offset + limit]
        ]

    async def get_node(self, uuid: str):
        for gid, ns in self.nodes.items():
            for n in ns:
                if n["uuid"] == uuid:
                    return {"node": {**n, "labels": ["Entity", n["type"]]}, "outgoing": [], "incoming": []}
        return None


class FakeQdrantStore:
    def __init__(self) -> None:
        self.points: dict[str, list[dict]] = {}

    async def verify(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def ensure_collection(self, graph_id: str, dim: int) -> None:
        return None

    async def delete_collection(self, graph_id: str) -> None:
        self.points.pop(graph_id, None)

    async def upsert_nodes(self, graph_id, entities, vectors) -> None:
        for e, v in zip(entities, vectors):
            self.points.setdefault(graph_id, []).append({"type": "node", "uuid": e.uuid, "name": e.name, "summary": e.summary, "vector": v})

    async def upsert_edges(self, graph_id, relations, vectors) -> None:
        for r, v in zip(relations, vectors):
            self.points.setdefault(graph_id, []).append({"type": "edge", "uuid": r.uuid, "fact": r.fact, "source_node_uuid": r.source_node_uuid, "target_node_uuid": r.target_node_uuid, "vector": v})

    async def search(self, graph_id, query_vector, top_k=10, types=None):
        pts = self.points.get(graph_id, [])
        out = []
        for p in pts:
            if types and p["type"] not in types:
                continue
            out.append({
                "type": p["type"], "uuid": p["uuid"], "score": 0.9,
                "name": p.get("name", ""), "summary": p.get("summary", ""), "fact": p.get("fact", ""),
                "source_node_uuid": p.get("source_node_uuid", ""), "target_node_uuid": p.get("target_node_uuid", ""),
            })
        return out[:top_k]


@pytest.fixture
def client(tmp_path):
    """构造带替身服务的 TestClient。"""
    from app import main as app_main

    config = Config(
        llm_provider="mock",
        neo4j_password="test",
        data_dir=str(tmp_path),
        llm_concurrency=2,
    )
    llm = MockLLMClient()
    llm.enqueue_json({  # 本体生成响应
        "entity_types": [
            {"name": "person", "description": "人物"},
            {"name": "company", "description": "公司"},
        ],
        "edge_types": [{"name": "works at", "source_targets": [{"source": "person", "target": "company"}]}],
        "analysis_summary": "测试本体",
    })
    llm.enqueue_json({  # 抽取响应
        "entities": [
            {"name": "张三", "type": "person", "summary": "工程师"},
            {"name": "ACME", "type": "company", "summary": "公司"},
        ],
        "relations": [{"source": "张三", "target": "ACME", "type": "works at", "fact": "张三在ACME工作"}],
    })
    llm.enqueue_json({"summary": "张三，工程师"})  # summary 聚合

    services = Services(
        config=config,
        llm=llm,
        embeddings=MockEmbeddingClient(dim=32),
        neo4j=FakeNeo4jStore(),
        qdrant=FakeQdrantStore(),
        tasks=TaskStore(tmp_path / "tasks.db"),
    )
    app_main._services = services
    # 跳过真实 lifespan 的外部连接：monkeypatch _build_services 与 Config.load
    import unittest.mock as _mock

    with _mock.patch.object(app_main, "_build_services", return_value=services), \
         _mock.patch.object(app_main.Config, "load", return_value=config):
        with TestClient(app_main.app, raise_server_exceptions=False) as c:
            yield c
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(services.tasks.close())


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_graph_preview_returns_finished_chunk_results(client):
    """实时预览合并已完成块，并生成可供 d3 直接连接的稳定端点 ID。"""
    from app.models.graph import ExtractionResult, ExtractEntity, ExtractRelation
    from app.pipeline.runner import PREVIEW_RESULTS

    graph_id = client.post("/api/graphs", json={"name": "实时预览图"}).json()["data"]["graph_id"]
    PREVIEW_RESULTS[graph_id] = [
        ExtractionResult(
            entities=[
                ExtractEntity(name="张三", type="Person", summary="工程师"),
                ExtractEntity(name="ACME", type="Organization", summary="公司"),
            ],
            relations=[
                ExtractRelation(
                    source="张三", target="ACME", type="WORKS_AT", fact="张三在 ACME 工作"
                )
            ],
        )
    ]
    try:
        r = client.get(f"/api/graphs/{graph_id}/preview")
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["source"] == "preview"
        assert data["node_count"] == 2
        assert data["edge_count"] == 1
        assert data["edges"][0]["source_node_uuid"] == "张三"
        assert data["edges"][0]["target_node_uuid"] == "acme"
    finally:
        PREVIEW_RESULTS.pop(graph_id, None)


def test_graph_preview_keeps_relation_when_target_appears_in_later_chunk(client):
    """关系端点可分布在不同抽取块中，预览合并后仍应展示该关系。"""
    from app.models.graph import ExtractionResult, ExtractEntity, ExtractRelation
    from app.pipeline.runner import PREVIEW_RESULTS

    graph_id = client.post("/api/graphs", json={"name": "跨块关系图"}).json()["data"]["graph_id"]
    PREVIEW_RESULTS[graph_id] = [
        ExtractionResult(
            entities=[ExtractEntity(name="张三", type="Person")],
            relations=[
                ExtractRelation(
                    source="张三", target="ACME", type="WORKS_AT", fact="张三在 ACME 工作"
                )
            ],
        ),
        ExtractionResult(entities=[ExtractEntity(name="ACME", type="Organization")]),
    ]
    try:
        data = client.get(f"/api/graphs/{graph_id}/preview").json()["data"]

        assert data["node_count"] == 2
        assert data["edge_count"] == 1
    finally:
        PREVIEW_RESULTS.pop(graph_id, None)


def test_graph_preview_restores_persisted_results_after_restart(client):
    """内存预览丢失时，接口应从 SQLite 恢复已完成块。"""
    from app.api.graphs import _services
    from app.pipeline.runner import PREVIEW_RESULTS

    graph_id = client.post("/api/graphs", json={"name": "持久化预览图"}).json()["data"]["graph_id"]
    svc = _services()
    loop = asyncio.get_event_loop_policy().new_event_loop()
    loop.run_until_complete(svc.tasks.reset_preview(graph_id, "task-persisted"))
    loop.run_until_complete(
        svc.tasks.put_preview_result(
            graph_id,
            "task-persisted",
            0,
            {
                "entities": [
                    {"name": "张三", "type": "Person"},
                    {"name": "ACME", "type": "Organization"},
                ],
                "relations": [
                    {
                        "source": "张三",
                        "target": "ACME",
                        "type": "WORKS_AT",
                        "fact": "张三在 ACME 工作",
                    }
                ],
            },
        )
    )
    loop.close()
    PREVIEW_RESULTS.pop(graph_id, None)

    data = client.get(f"/api/graphs/{graph_id}/preview").json()["data"]

    assert data["node_count"] == 2
    assert data["edge_count"] == 1


def test_graph_crud_flow(client):
    # 创建
    r = client.post("/api/graphs", json={"name": "测试图"})
    assert r.status_code == 200
    graph_id = r.json()["data"]["graph_id"]
    # 列表
    r = client.get("/api/graphs")
    assert any(g["graph_id"] == graph_id for g in r.json()["data"])
    # 详情
    r = client.get(f"/api/graphs/{graph_id}")
    assert r.json()["data"]["name"] == "测试图"
    # 删除
    r = client.delete(f"/api/graphs/{graph_id}")
    assert r.json()["success"] is True
    assert client.get(f"/api/graphs/{graph_id}").status_code == 404


def test_full_build_flow(client):
    """上传 → 本体 → 构建 → 任务完成 → 节点/边/搜索/导出。"""
    import io
    import time

    # 创建图
    graph_id = client.post("/api/graphs", json={"name": "流程图"}).json()["data"]["graph_id"]

    # 上传文档（单文件，兼容写法：files 字段列表）
    files = [("files", ("doc.txt", io.BytesIO("张三在ACME公司工作。".encode("utf-8")), "text/plain"))]
    r = client.post(f"/api/graphs/{graph_id}/documents", files=files, data={"purpose": "人员关系"})
    assert r.status_code == 200
    assert r.json()["data"]["count"] == 1
    assert r.json()["data"]["files"][0]["chars"] > 0

    # 生成本体（同步）
    files = {"file": ("doc.txt", io.BytesIO("张三在ACME公司工作。".encode("utf-8")), "text/plain")}
    r = client.post(f"/api/graphs/{graph_id}/ontology", files=files, data={"purpose": "人员关系"})
    assert r.status_code == 200
    ontology = r.json()["data"]
    assert any(et["name"] == "Person" for et in ontology["entity_types"])

    # 启动构建（带内联本体）
    r = client.post(f"/api/graphs/{graph_id}/build", json={"ontology": ontology})
    assert r.status_code == 200
    task_id = r.json()["data"]["task_id"]

    # 等待后台任务完成（内存注册表 + asyncio）
    from app.api.graphs import _services

    svc = _services()
    for _ in range(100):
        if not svc.registry.is_running(task_id):
            break
        time.sleep(0.05)
    task = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(svc.tasks.get_task(task_id))
    assert task is not None
    assert task.status.value == "completed", f"任务失败: {task.error}"

    # 节点/边
    nodes = client.get(f"/api/graphs/{graph_id}/nodes").json()["data"]
    edges = client.get(f"/api/graphs/{graph_id}/edges").json()["data"]
    assert any(n["name"] == "张三" for n in nodes)
    assert any(e["fact"] == "张三在ACME工作" for e in edges)

    # 搜索
    r = client.post(f"/api/graphs/{graph_id}/search", json={"query": "工程师", "top_k": 5})
    assert r.status_code == 200
    hits = r.json()["data"]["hits"]
    assert len(hits) > 0

    # 导出
    r = client.get(f"/api/graphs/{graph_id}/export/mirofish")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"


def test_multi_file_build_flow(client):
    """多文件上传 → 列表 → 构建合并 → 单文件删除 → 再构建。"""
    import io
    import time

    graph_id = client.post("/api/graphs", json={"name": "多文件图"}).json()["data"]["graph_id"]

    # 一次请求上传两个文件
    files = [
        ("files", ("a.txt", io.BytesIO("张三在ACME公司工作。".encode("utf-8")), "text/plain")),
        ("files", ("b.txt", io.BytesIO("李四也在ACME公司工作。".encode("utf-8")), "text/plain")),
    ]
    r = client.post(f"/api/graphs/{graph_id}/documents", files=files, data={})
    assert r.status_code == 200
    assert r.json()["data"]["count"] == 2

    # 分批再传一个，验证累加语义
    r = client.post(
        f"/api/graphs/{graph_id}/documents",
        files=[("files", ("c.txt", io.BytesIO("张三和李四是同事。".encode("utf-8")), "text/plain"))],
    )
    assert r.status_code == 200

    # 文件列表
    r = client.get(f"/api/graphs/{graph_id}/documents")
    assert r.status_code == 200
    assert r.json()["data"]["filenames"] == ["a.txt", "b.txt", "c.txt"]

    # 删除单个文件
    r = client.delete(f"/api/graphs/{graph_id}/documents/b.txt")
    assert r.status_code == 200
    r = client.get(f"/api/graphs/{graph_id}/documents")
    assert r.json()["data"]["filenames"] == ["a.txt", "c.txt"]

    # 构建（自动本体）：两文件内容拼接抽取
    r = client.post(f"/api/graphs/{graph_id}/build", json={})
    assert r.status_code == 200
    task_id = r.json()["data"]["task_id"]

    from app.api.graphs import _services

    svc = _services()
    for _ in range(100):
        if not svc.registry.is_running(task_id):
            break
        time.sleep(0.05)
    task = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(svc.tasks.get_task(task_id))
    assert task is not None, "任务未创建"
    assert task.status.value == "completed", f"任务失败: {task.error}"
    # 进度消息应反映多文件解析
    nodes = client.get(f"/api/graphs/{graph_id}/nodes").json()["data"]
    assert any(n["name"] == "张三" for n in nodes)


def test_upload_unsupported_type_rejected(client):
    """不支持扩展名的文件返回 400，且不进入暂存区。"""
    import io

    graph_id = client.post("/api/graphs", json={"name": "拒绝图"}).json()["data"]["graph_id"]
    r = client.post(
        f"/api/graphs/{graph_id}/documents",
        files=[("files", ("x.exe", io.BytesIO(b"MZ..."), "application/octet-stream"))],
    )
    assert r.status_code == 400
    r = client.get(f"/api/graphs/{graph_id}/documents")
    assert r.json()["data"]["filenames"] == []


def test_upload_can_replace_all_staged_documents(client):
    import io

    graph_id = client.post("/api/graphs", json={"name": "替换文档图"}).json()["data"]["graph_id"]
    client.post(
        f"/api/graphs/{graph_id}/documents",
        files=[
            ("files", ("old-a.txt", io.BytesIO(b"old a"), "text/plain")),
            ("files", ("old-b.txt", io.BytesIO(b"old b"), "text/plain")),
        ],
    )

    response = client.post(
        f"/api/graphs/{graph_id}/documents",
        files=[("files", ("new.txt", io.BytesIO(b"new"), "text/plain"))],
        data={"replace_existing": "true"},
    )

    assert response.status_code == 200
    listed = client.get(f"/api/graphs/{graph_id}/documents").json()["data"]["filenames"]
    assert listed == ["new.txt"]


def test_failed_replacement_upload_keeps_existing_documents(client):
    import io

    graph_id = client.post("/api/graphs", json={"name": "安全替换文档图"}).json()["data"]["graph_id"]
    client.post(
        f"/api/graphs/{graph_id}/documents",
        files=[("files", ("existing.txt", io.BytesIO(b"existing"), "text/plain"))],
    )

    response = client.post(
        f"/api/graphs/{graph_id}/documents",
        files=[("files", ("invalid.exe", io.BytesIO(b"invalid"), "application/octet-stream"))],
        data={"replace_existing": "true"},
    )

    assert response.status_code == 400
    listed = client.get(f"/api/graphs/{graph_id}/documents").json()["data"]["filenames"]
    assert listed == ["existing.txt"]


def test_ontology_from_staged_files(client):
    """基于暂存多文件生成本体；暂存区为空时返回 400。"""
    import io

    graph_id = client.post("/api/graphs", json={"name": "暂存本体图"}).json()["data"]["graph_id"]
    # 空暂存区 → 400
    r = client.post(f"/api/graphs/{graph_id}/ontology/staged", json={"purpose": ""})
    assert r.status_code == 400

    # 上传两个文件后生成
    files = [
        ("files", ("a.txt", io.BytesIO("张三在ACME公司工作。".encode("utf-8")), "text/plain")),
        ("files", ("b.txt", io.BytesIO("李四负责销售业务。".encode("utf-8")), "text/plain")),
    ]
    client.post(f"/api/graphs/{graph_id}/documents", files=files, data={})
    r = client.post(f"/api/graphs/{graph_id}/ontology/staged", json={"purpose": "人员关系"})
    assert r.status_code == 200
    ontology = r.json()["data"]
    assert any(et["name"] == "Person" for et in ontology["entity_types"])


def test_settings_get_and_update(client):
    """Web 端设置：查看 / 校验失败拒绝 / 更新热生效 / key 掩码不回显。"""
    # 初始：mock 模式（fixture config 无 api_key）
    r = client.get("/api/settings")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["llm_provider"] == "mock"
    assert data["llm_api_key_set"] is False
    assert data["embedding_provider"] == "auto"
    assert data["chunk_size"] == 1200
    assert data["chunk_overlap"] == 100
    assert data["llm_concurrency"] == 2
    assert data["extract_batch_size"] == 4
    assert data["resolve_candidate_k"] == 8

    # 处理参数可持久化并热生效，不依赖容器重启。
    r = client.put(
        "/api/settings",
        json={
            "embedding_provider": "local",
            "chunk_size": 1600,
            "chunk_overlap": 120,
            "llm_concurrency": 6,
            "extract_batch_size": 3,
            "resolve_candidate_k": 5,
        },
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["embedding_provider"] == "local"
    assert (data["chunk_size"], data["chunk_overlap"], data["llm_concurrency"]) == (1600, 120, 6)
    assert (data["extract_batch_size"], data["resolve_candidate_k"]) == (3, 5)

    # 切 openai 但缺 api_key（env 也没有）→ 400，配置不变
    r = client.put(
        "/api/settings",
        json={"llm_provider": "openai", "llm_base_url": "http://x/v1", "llm_model": "m"},
    )
    assert r.status_code == 400
    assert client.get("/api/settings").json()["data"]["llm_provider"] == "mock"

    # 带 api_key 更新成功，客户端热重建为 openai
    r = client.put(
        "/api/settings",
        json={
            "llm_provider": "openai",
            "llm_base_url": "http://x/v1",
            "llm_api_key": "test-key-secret-1234567890",
            "llm_model": "test-model",
        },
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["llm_provider"] == "openai"
    assert data["llm_api_key_set"] is True
    assert data["llm_api_key_masked"].endswith("7890")
    assert "test-key-secret-1234567890" not in r.text  # 掩码，不回显完整 key
    from app.api.graphs import _services

    svc = _services()
    assert svc.config.llm_provider == "openai" and svc.config.llm_model == "test-model"

    # GET 反映热更新后的配置；web_overrides 记录覆盖项
    data = client.get("/api/settings").json()["data"]
    assert data["llm_provider"] == "openai"
    assert "LLM_API_KEY" in data["web_overrides"]

    # 切回 mock（api_key 缺省 = 保持不变）
    r = client.put("/api/settings", json={"llm_provider": "mock"})
    assert r.status_code == 200
    assert client.get("/api/settings").json()["data"]["llm_provider"] == "mock"


def test_settings_no_op_and_test_endpoint(client):
    """空更新是 no-op；/test 在 mock 下直接成功。"""
    r = client.put("/api/settings", json={})
    assert r.status_code == 200

    r = client.post("/api/settings/test", json={"llm_provider": "mock"})
    assert r.status_code == 200
    assert r.json()["data"]["llm"]["ok"] is True


def test_settings_persist_and_overrides(tmp_path):
    """设置持久化到 SQLite；重启路径（load_all → with_overrides）能正确应用覆盖项。"""
    import asyncio

    from app.config import Config
    from app.storage.settings import SettingsStore

    async def write_and_read():
        store = SettingsStore(tmp_path / "settings.db")
        await store.update(
            {
                "LLM_PROVIDER": "openai",
                "LLM_BASE_URL": "http://x/v1",
                "LLM_API_KEY": "test-key-persist-98765432",
                "LLM_MODEL": "m1",
                "CHUNK_SIZE": "1800",
                "CHUNK_OVERLAP": "150",
                "LLM_CONCURRENCY": "10",
            }
        )
        # 模拟重启：新连接重新读回
        overrides = await store.load_all()
        await store.close()
        return overrides

    overrides = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(write_and_read())

    base = Config(llm_provider="mock", neo4j_password="t", llm_model="gpt-4o-mini")
    cfg = base.with_overrides(overrides)
    assert cfg.llm_provider == "openai"
    assert cfg.llm_model == "m1"
    assert cfg.llm_api_key == "test-key-persist-98765432"
    assert (cfg.chunk_size, cfg.chunk_overlap, cfg.llm_concurrency) == (1800, 150, 10)
    # 未覆盖项保持 env 缺省；frozen dataclass 不被原地修改
    assert cfg.neo4j_password == "t"
    assert base.llm_provider == "mock"
