"""文档上传与解析端点。"""
from __future__ import annotations

from pydantic import BaseModel

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.pipeline import parser
from app.pipeline import ontology as ontology_mod

router = APIRouter(prefix="/api/graphs", tags=["documents"])


def _ok(data=None):
    return {"success": True, "data": data}


@router.post("/{graph_id}/documents")
async def upload_document(
    graph_id: str,
    files: list[UploadFile] = File(...),
    purpose: str = Form(""),
    replace_existing: bool = Form(False),
):
    """上传一个或多个文档（pdf/md/txt），同步解析校验并暂存，等待 build 消费。

    默认多次调用会追加文件；replace_existing=true 时在全部新文件校验成功后整体替换。
    """
    from app.api.graphs import _services
    from app.main import clear_uploaded_files, store_uploaded_file

    svc = _services()
    meta = await svc.neo4j.get_graph_meta(graph_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="graph not found")
    pending = []
    for file in files:
        content = await file.read()
        try:
            text = parser.parse_bytes(content, file.filename or "")
        except parser.ParseError as e:
            raise HTTPException(status_code=400, detail=f"{file.filename}: {e}")
        pending.append((content, file.filename or "", len(text)))
    if replace_existing:
        clear_uploaded_files(graph_id)
    stored = []
    for content, filename, char_count in pending:
        store_uploaded_file(graph_id, content, filename)
        stored.append({"filename": filename, "chars": char_count})
    return {
        "success": True,
        "data": {"files": stored, "count": len(stored), "purpose": purpose},
    }


@router.get("/{graph_id}/documents")
async def list_documents(graph_id: str):
    """列出该图谱已暂存（等待构建）的文件名。"""
    from app.api.graphs import _services
    from app.main import list_uploaded_filenames

    svc = _services()
    meta = await svc.neo4j.get_graph_meta(graph_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="graph not found")
    return _ok({"filenames": list_uploaded_filenames(graph_id)})


@router.delete("/{graph_id}/documents/{filename:path}")
async def delete_document(graph_id: str, filename: str):
    """从暂存区移除单个文件（不影响已构建内容）。"""
    from app.api.graphs import _services
    from app.main import remove_uploaded_file

    svc = _services()
    meta = await svc.neo4j.get_graph_meta(graph_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="graph not found")
    if not remove_uploaded_file(graph_id, filename):
        raise HTTPException(status_code=404, detail="file not found")
    return _ok({"removed": filename})


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


class OntologyStagedRequest(BaseModel):
    purpose: str = ""


@router.post("/{graph_id}/ontology/staged")
async def generate_ontology_staged(graph_id: str, req: OntologyStagedRequest):
    """基于已暂存的多文件合并语料生成本体，同步返回本体 JSON。"""
    from app.api.graphs import _services
    from app.main import get_uploaded_files

    svc = _services()
    meta = await svc.neo4j.get_graph_meta(graph_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="graph not found")
    files = get_uploaded_files(graph_id)
    if not files:
        raise HTTPException(status_code=400, detail="暂存区为空：请先上传文档")
    texts = []
    for content, filename in files:
        try:
            texts.append(parser.parse_bytes(content, filename))
        except parser.ParseError as e:
            raise HTTPException(status_code=400, detail=f"{filename}: {e}")
    text = "\n\n".join(t for t in texts if t.strip())
    if not text.strip():
        raise HTTPException(status_code=400, detail="暂存文件解析后内容为空")
    try:
        onto = await ontology_mod.generate_ontology(svc.llm, text, req.purpose, svc.config)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"本体生成失败: {e}")
    return _ok(onto.model_dump())
