"""节点/边读取端点。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/api/graphs", tags=["read"])


@router.get("/{graph_id}/nodes")
async def list_nodes(graph_id: str, offset: int = 0, limit: int = Query(100, le=500)):
    from app.api.graphs import _services

    svc = _services()
    meta = await svc.neo4j.get_graph_meta(graph_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="graph not found")
    nodes = await svc.neo4j.list_nodes(graph_id, offset=offset, limit=limit)
    return {"success": True, "data": [n.model_dump() for n in nodes]}


@router.get("/{graph_id}/edges")
async def list_edges(graph_id: str, offset: int = 0, limit: int = Query(100, le=500)):
    from app.api.graphs import _services

    svc = _services()
    meta = await svc.neo4j.get_graph_meta(graph_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="graph not found")
    edges = await svc.neo4j.list_edges(graph_id, offset=offset, limit=limit)
    return {"success": True, "data": [e.model_dump() for e in edges]}


@router.get("/{graph_id}/nodes/{uuid}")
async def get_node(graph_id: str, uuid: str):
    from app.api.graphs import _services

    svc = _services()
    meta = await svc.neo4j.get_graph_meta(graph_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="graph not found")
    detail = await svc.neo4j.get_node(uuid)
    if detail is None:
        raise HTTPException(status_code=404, detail="node not found")
    return {"success": True, "data": detail}
