"""图谱 CRUD 与构建任务端点。"""
from __future__ import annotations

import json
import uuid as _uuid

from fastapi import APIRouter, HTTPException

from app.models.api import BuildRequest, GraphCreateRequest
from app.models.ontology import Ontology
from app.pipeline import ontology as ontology_mod
from app.pipeline.runner import BuildParams, Services, new_task_id, run_build

router = APIRouter(prefix="/api/graphs", tags=["graphs"])


def _services() -> Services:
    from app.main import get_services

    return get_services()


def _ok(data=None):
    return {"success": True, "data": data}


@router.post("")
async def create_graph(req: GraphCreateRequest):
    svc = _services()
    graph_id = str(_uuid.uuid4())
    await svc.neo4j.create_graph(graph_id, req.name)
    return _ok({"graph_id": graph_id, "name": req.name})


@router.get("")
async def list_graphs():
    svc = _services()
    graphs = await svc.neo4j.list_graphs()
    out = []
    for g in graphs:
        meta = g
        node_count, edge_count = await svc.neo4j.count_nodes_edges(g["graph_id"])
        out.append(
            {
                "graph_id": g["graph_id"],
                "name": g.get("name", ""),
                "status": g.get("status", ""),
                "created_at": g.get("created_at", ""),
                "node_count": node_count,
                "edge_count": edge_count,
            }
        )
    return _ok(out)


@router.get("/{graph_id}")
async def get_graph(graph_id: str):
    svc = _services()
    meta = await svc.neo4j.get_graph_meta(graph_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="graph not found")
    node_count, edge_count = await svc.neo4j.count_nodes_edges(graph_id)
    ontology_raw = meta.get("ontology_json")
    ontology = json.loads(ontology_raw) if ontology_raw else None
    return _ok(
        {
            "graph_id": graph_id,
            "name": meta.get("name", ""),
            "status": meta.get("status", ""),
            "created_at": meta.get("created_at", ""),
            "node_count": node_count,
            "edge_count": edge_count,
            "ontology": ontology,
        }
    )


@router.delete("/{graph_id}")
async def delete_graph(graph_id: str):
    svc = _services()
    meta = await svc.neo4j.get_graph_meta(graph_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="graph not found")
    await svc.neo4j.delete_graph(graph_id)
    await svc.qdrant.delete_collection(graph_id)
    from app.main import clear_uploaded_files

    clear_uploaded_files(graph_id)  # 磁盘暂存文件一并清理，防泄漏
    return _ok({"deleted": graph_id})


@router.post("/{graph_id}/build")
async def build_graph(graph_id: str, req: BuildRequest):
    """启动构建任务。缺省 ontology 时管道自动生成；消费全部已上传文档。"""
    from app.main import get_uploaded_files

    svc = _services()
    meta = await svc.neo4j.get_graph_meta(graph_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="graph not found")
    files = get_uploaded_files(graph_id)
    if not files:
        raise HTTPException(
            status_code=400,
            detail="未上传文档：请先调用 /api/graphs/{graph_id}/documents 上传",
        )
    if svc.registry.has_running_for(graph_id):
        raise HTTPException(
            status_code=409,
            detail="该图谱已有构建任务运行中，请等待其完成或失败后再试",
        )

    task_id = new_task_id()
    params = BuildParams(
        graph_id=graph_id,
        files=files,
        purpose=req.purpose,
        ontology=req.ontology,
        chunk_size=req.chunk_size,
        chunk_overlap=req.chunk_overlap,
    )
    from app.models.task import Task, TaskStatus

    task = Task(task_id=task_id, graph_id=graph_id, status=TaskStatus.pending)
    await svc.tasks.create_task(task)
    svc.registry.register(task_id, run_build(task_id, params, svc), graph_id)
    return _ok({"task_id": task_id})


from fastapi import APIRouter as _AR  # noqa: E402

tasks_router = _AR(prefix="/api/tasks", tags=["tasks"])


@tasks_router.get("/by-graph/{graph_id}/latest")
async def latest_graph_task(graph_id: str):
    """该图谱最近一次构建任务（前端刷新页面后恢复进度面板）。"""
    svc = _services()
    tasks = await svc.tasks.get_tasks_for_graph(graph_id)
    if not tasks:
        return _ok(None)
    return _ok(tasks[0].model_dump())


@tasks_router.get("/{task_id}")
async def get_task(task_id: str):
    """任务状态与进度。"""
    svc = _services()
    task = await svc.tasks.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return _ok(task.model_dump())
