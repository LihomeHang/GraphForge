"""docker 全栈端到端演示脚本（对运行中的服务执行完整流程，默认宿主 8080）。"""
import io
import json
import time
import urllib.request

BASE = "http://localhost:8080/api"


def req(method, path, data=None, raw=False):
    url = BASE + path
    body = None
    headers = {}
    if data is not None:
        if isinstance(data, dict):
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        else:
            body = data
    r = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(r) as resp:
        content = resp.read()
        return content if raw else json.loads(content)


def upload(path, filename, content, purpose=""):
    boundary = "----graphforge-demo"
    part = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\n{purpose}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    r = urllib.request.Request(
        BASE + path, data=part, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(r) as resp:
        return json.loads(resp.read())


text = """中色股份（000758.SZ）是中国有色矿业集团控股的上市公司，实际控制人为国务院国资委。
公司主营国际工程承包与铅锌采选冶炼业务。
中国有色矿业集团持有中色股份约35.39%股权，是中色股份的控股股东。
中色股份在蒙古国投资建设铜冶炼项目，年产能12万吨阴极铜。
国资委监督管理中国有色矿业集团。
张伟担任中色股份总经理，负责公司日常经营。
中色股份总部位于北京，其工程业务主要分布在蒙古国和越南。"""

print("== 1. 创建图谱 ==")
g = req("POST", "/graphs", {"name": "中色股份演示"})
gid = g["data"]["graph_id"]
print("graph_id:", gid)

print("== 2. 上传文档 + 生成本体 ==")
upload(f"/graphs/{gid}/documents", "demo.txt", text.encode("utf-8"), "梳理股权与业务关系")
ont = upload(f"/graphs/{gid}/ontology", "demo.txt", text.encode("utf-8"), "梳理股权与业务关系")
print("实体类型:", [et["name"] for et in ont["data"]["entity_types"]])
print("关系类型:", [et["name"] for et in ont["data"]["edge_types"]])

print("== 3. 启动构建（内联本体） ==")
b = req("POST", f"/graphs/{gid}/build", {"ontology": ont["data"]})
task_id = b["data"]["task_id"]
print("task_id:", task_id)

print("== 4. 轮询任务 ==")
for _ in range(60):
    t = req("GET", f"/tasks/{task_id}")["data"]
    if t["status"] in ("completed", "failed"):
        break
    time.sleep(0.5)
print("状态:", t["status"], "| 进度:", t["progress"], "|", t["message"])
if t["status"] == "failed":
    print("错误:", t["error"])
    raise SystemExit(1)

print("== 5. 语义搜索 ==")
s = req("POST", f"/graphs/{gid}/search", {"query": "公司的控股股东是谁", "top_k": 3})["data"]
for h in s["hits"]:
    if h["type"] == "node":
        print(f"  [节点] {h['name']} ({h['score']:.3f}): {h['summary'][:40]}")
    else:
        print(f"  [事实] {h['fact']} ({h['score']:.3f})")

print("== 6. MiroFish 导出 ==")
zip_bytes = req("GET", f"/graphs/{gid}/export/mirofish", raw=True)
import zipfile
zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
print("zip 内容:", zf.namelist())
nodes = json.loads(zf.read("nodes.json"))
edges = json.loads(zf.read("edges.json"))
print(f"节点 {len(nodes)} 个 / 边 {len(edges)} 条")
print("节点字段:", sorted(nodes[0].keys()))
print("边字段:", sorted(edges[0].keys()))

print("== 7. 图详情 ==")
detail = req("GET", f"/graphs/{gid}")["data"]
print(f"节点数 {detail['node_count']} / 边数 {detail['edge_count']} / 状态 {detail['status']}")
print("\n✅ 端到端演示全部通过")
