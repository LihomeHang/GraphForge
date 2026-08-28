"""MiroFish 兼容导出端点（zip: nodes.json / edges.json / ontology.json / manifest.json）。"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.models.graph import EdgeNode, EntityNode

router = APIRouter(prefix="/api/graphs", tags=["export"])

SCHEMA_VERSION = "1.0"


@router.get("/{graph_id}/export/mirofish")
async def export_mirofish(graph_id: str, batch: int = 500):
    from app.api.graphs import _services

    svc = _services()
    meta = await svc.neo4j.get_graph_meta(graph_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="graph not found")

    # 全量拉取节点与边（分页滚动）
    nodes: list[EntityNode] = []
    offset = 0
    while True:
        page = await svc.neo4j.list_nodes(graph_id, offset=offset, limit=batch)
        nodes.extend(page)
        if len(page) < batch:
            break
        offset += batch

    edges: list[EdgeNode] = []
    offset = 0
    while True:
        page = await svc.neo4j.list_edges(graph_id, offset=offset, limit=batch)
        edges.extend(page)
        if len(page) < batch:
            break
        offset += batch

    ontology_raw = meta.get("ontology_json")
    ontology = json.loads(ontology_raw) if ontology_raw else {}

    manifest = {
        "graph_id": graph_id,
        "name": meta.get("name", ""),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": SCHEMA_VERSION,
        "target": "mirofish",
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("nodes.json", json.dumps([n.model_dump() for n in nodes], ensure_ascii=False, indent=2))
        zf.writestr("edges.json", json.dumps([e.model_dump() for e in edges], ensure_ascii=False, indent=2))
        zf.writestr("ontology.json", json.dumps(ontology, ensure_ascii=False, indent=2))
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename=graphforge_{graph_id[:8]}_mirofish.zip"},
    )
