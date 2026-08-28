"""文档上传与解析端点。"""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.pipeline import parser
from app.pipeline import ontology as ontology_mod

router = APIRouter(prefix="/api/graphs", tags=["documents"])


@router.post("/{graph_id}/documents")
async def upload_document(
    graph_id: str,
    file: UploadFile = File(...),
    purpose: str = Form(""),
):
    """上传文档（pdf/md/txt），同步解析校验并暂存，等待 build 消费。"""
    from app.api.graphs import _services
    from app.main import store_uploaded_file

    svc = _services()
    meta = await svc.neo4j.get_graph_meta(graph_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="graph not found")
    content = await file.read()
    try:
        text = parser.parse_bytes(content, file.filename or "")
    except parser.ParseError as e:
        raise HTTPException(status_code=400, detail=str(e))
    store_uploaded_file(graph_id, content, file.filename or "")
    return {"success": True, "data": {"filename": file.filename, "chars": len(text), "purpose": purpose}}


@router.post("/{graph_id}/ontology")
async def generate_ontology(
    graph_id: str,
    file: UploadFile = File(...),
    purpose: str = Form(""),
):
    """生成本体（multipart 文件 + 表单 purpose），同步返回本体 JSON。"""
    from app.api.graphs import _services

    svc = _services()
    meta = await svc.neo4j.get_graph_meta(graph_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="graph not found")
    content = await file.read()
    try:
        text = parser.parse_bytes(content, file.filename or "")
    except parser.ParseError as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        onto = await ontology_mod.generate_ontology(svc.llm, text, purpose, svc.config)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"本体生成失败: {e}")
    return {"success": True, "data": onto.model_dump()}
