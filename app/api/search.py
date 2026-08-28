"""语义搜索端点。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.api import SearchRequest
from app.llm.embeddings import EmbeddingClient

router = APIRouter(prefix="/api/graphs", tags=["search"])


@router.post("/{graph_id}/search")
async def search(graph_id: str, req: SearchRequest):
    from app.api.graphs import _services

    svc = _services()
    meta = await svc.neo4j.get_graph_meta(graph_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="graph not found")
    qvec = await svc.embeddings.embed_one(req.query)
    hits = await svc.qdrant.search(graph_id, qvec, top_k=req.top_k, types=req.types or None)
    # edge 命中补充端点实体名
    for h in hits:
        if h["type"] == "edge":
            src = await svc.neo4j.get_node(h.get("source_node_uuid", ""))
            tgt = await svc.neo4j.get_node(h.get("target_node_uuid", ""))
            h["source_name"] = src["node"]["name"] if src else ""
            h["target_name"] = tgt["node"]["name"] if tgt else ""
    return {"success": True, "data": {"query": req.query, "hits": hits}}
