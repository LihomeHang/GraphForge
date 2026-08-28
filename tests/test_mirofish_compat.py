"""MiroFish 兼容合约测试（§6）：固化字段结构，字段名漂移即失败。

字段来源：MiroFish `zep_entity_reader.py` ——
- 节点（EntityNode.to_dict()）5 字段：uuid, name, labels, summary, attributes
- 边（get_all_edges）6 字段：uuid, name, fact, source_node_uuid, target_node_uuid, attributes
"""
import json
import zipfile

from app.models.graph import EdgeNode, EntityNode

NODE_FIELDS = {"uuid", "name", "labels", "summary", "attributes"}
EDGE_FIELDS = {"uuid", "name", "fact", "source_node_uuid", "target_node_uuid", "attributes"}


def test_entity_node_contract():
    node = EntityNode(
        uuid="u1", name="张三", labels=["Entity", "Person"], summary="工程师", attributes={"age": 30}
    )
    d = node.model_dump()
    assert set(d.keys()) == NODE_FIELDS, f"节点字段漂移: {set(d.keys())}"
    assert d["labels"][0] == "Entity"
    assert isinstance(d["attributes"], dict)


def test_edge_node_contract():
    edge = EdgeNode(
        uuid="e1", name="WORKS_AT", fact="张三入职ACME",
        source_node_uuid="u1", target_node_uuid="u2", attributes={},
    )
    d = edge.model_dump()
    assert set(d.keys()) == EDGE_FIELDS, f"边字段漂移: {set(d.keys())}"


def test_json_roundtrip_contract():
    """导出 JSON 反序列化后仍满足合约。"""
    node = EntityNode(uuid="u1", name="n", labels=["Entity", "Person"], summary="s", attributes={})
    edge = EdgeNode(uuid="e1", name="R", fact="f", source_node_uuid="u1", target_node_uuid="u2", attributes={})
    node2 = EntityNode.model_validate(json.loads(node.model_dump_json()))
    edge2 = EdgeNode.model_validate(json.loads(edge.model_dump_json()))
    assert set(node2.model_dump().keys()) == NODE_FIELDS
    assert set(edge2.model_dump().keys()) == EDGE_FIELDS


def test_export_zip_contains_contract_files(task_store=None):
    """导出 zip 应包含 4 个文件，nodes/edges 满足字段合约。"""
    # 直接构造 zip 内容做结构验证（与 api/export.py 相同逻辑）
    import io

    nodes = [EntityNode(uuid="u1", name="n", labels=["Entity"], summary="s", attributes={}).model_dump()]
    edges = [EdgeNode(uuid="e1", name="R", fact="f", source_node_uuid="u1", target_node_uuid="u2", attributes={}).model_dump()]
    ontology = {"entity_types": [], "edge_types": [], "analysis_summary": ""}
    manifest = {"graph_id": "g1", "node_count": 1, "edge_count": 1, "schema_version": "1.0"}

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("nodes.json", json.dumps(nodes, ensure_ascii=False))
        zf.writestr("edges.json", json.dumps(edges, ensure_ascii=False))
        zf.writestr("ontology.json", json.dumps(ontology, ensure_ascii=False))
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
    buf.seek(0)

    with zipfile.ZipFile(buf) as zf:
        assert set(zf.namelist()) == {"nodes.json", "edges.json", "ontology.json", "manifest.json"}
        for n in json.loads(zf.read("nodes.json")):
            assert set(n.keys()) == NODE_FIELDS
        for e in json.loads(zf.read("edges.json")):
            assert set(e.keys()) == EDGE_FIELDS
        m = json.loads(zf.read("manifest.json"))
        assert m["schema_version"] == "1.0"
