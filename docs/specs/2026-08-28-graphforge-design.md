# GraphForge 设计文档

- **日期**：2026-08-28
- **状态**：待用户评审
- **定位**：独立通用知识图谱生成工具，接入大模型做图谱生成与语义搜索；输出兼容 MiroFish 的 Zep 读取合约，但不嵌入 MiroFish

## 1. 背景与目标

MiroFish 的群体记忆完全绑定 Zep Cloud（SaaS），数据出境且需付费额度。GraphForge 以独立项目形态提供同等核心能力：文档 → 知识图谱生成 → 语义搜索，数据与存储完全自持。

**成功标准**：

1. 上传 pdf/md/txt 文档 + 一句分析目的，自动产出结构化知识图谱（本体 + 实体 + 关系事实）
2. 语义搜索能召回相关节点和事实，且随图谱规模增长保持亚秒级响应
3. 提供 MiroFish 兼容导出：导出数据可不经修改地被 MiroFish 现有读取代码消费（见 §6）
4. docker-compose 一键拉起全栈（app + neo4j + qdrant）

**非目标（YAGNI）**：episode 追加写入与 bi-temporal 时间演化、社区检测与社区摘要、多用户/鉴权体系、图数据库可替换抽象层、分布式 worker 队列。

## 2. 总体架构（方案 A：模块化单体）

```
graphforge/
├── app/
│   ├── main.py            # FastAPI 入口，托管 API 与构建后的 Web UI
│   ├── config.py          # 环境变量配置（LLM、Neo4j、Qdrant、管道参数）
│   ├── api/
│   │   ├── graphs.py      # 图谱 CRUD、构建任务
│   │   ├── documents.py   # 文档上传与解析
│   │   ├── search.py      # 语义搜索
│   │   └── export.py      # MiroFish 兼容导出
│   ├── pipeline/
│   │   ├── parser.py      # pdf/md/txt 解析（PyMuPDF + charset-normalizer）
│   │   ├── chunker.py     # 切块（默认 500 字符 / 50 重叠）
│   │   ├── ontology.py    # 本体生成与规范化
│   │   ├── extractor.py   # 逐块实体/关系抽取（并发 + JSON 修复重试）
│   │   ├── resolver.py    # 实体消歧与合并（归一化 + 嵌入聚类 + LLM 判定）
│   │   └── writer.py      # 批量写入 Neo4j / upsert Qdrant
│   ├── llm/
│   │   ├── client.py      # OpenAI 兼容 chat 客户端（JSON 模式 + 重试）
│   │   └── embeddings.py  # embedding 客户端（维度自动探测）
│   ├── storage/
│   │   ├── neo4j_store.py # 图读写（MERGE 批量、label 管理）
│   │   ├── qdrant_store.py# 向量读写（per-graph collection）
│   │   └── tasks.py       # SQLite 任务状态（asyncio 后台任务）
│   └── models/            # Pydantic 模型（本体、实体、关系、搜索结果）
├── web/                   # Vue 3 + Vite（上传、本体审阅、图谱可视化、搜索）
├── tests/                 # pytest（单元 + 合约测试）
├── docker-compose.yml     # app + neo4j + qdrant
├── Dockerfile
├── pyproject.toml         # uv 管理；Python 3.11–3.12
└── README.md
```

**技术栈**：Python 3.11–3.12、FastAPI、uv、neo4j 5.x 官方 Python driver、qdrant-client、PyMuPDF、charset-normalizer、pydantic v2、pytest。前端 Vue 3 + Vite + d3（图谱可视化）。

**运行形态**：单进程 FastAPI；构建管道以 asyncio 后台任务执行（`asyncio.create_task` + 内存任务注册表 + SQLite 持久化，重启后可标记中断任务并支持重新触发）。LLM 并发由 `asyncio.Semaphore` 限流（默认 4）。

## 3. 数据模型

### 3.1 Neo4j（图结构，多图隔离靠 graph_id）

```
(:Entity:Person {uuid, name, summary, graph_id, created_at, ...attributes})
[:KNOWS {uuid, name, fact, graph_id, created_at, ...attributes}]
(:GraphMeta {graph_id, name, ontology_json, status, created_at})
```

- 本体实体类型作为附加 label（PascalCase），全部节点带基类 label `Entity`
- 关系类型 = 本体边名（SCREAMING_SNAKE_CASE，Neo4j 关系类型合法字符集校验）
- 一条关系携带一句自然语言事实（`fact`）——Zep 风格，是语义检索的核心载体
- 同一对节点的同名关系允许多条（不同 fact），uuid 区分
- uuid：uuid4，写入时生成

### 3.2 Qdrant（向量检索）

- 每图一个 collection：`graphforge_{graph_id}`，图删除时连带删除
- 两类 point（同一 collection，靠 payload `type` 区分）：
  - 节点向量 `{type: "node", uuid, name, summary}`，embedding 文本 = `"{name}\n{summary}"`
  - 边向量 `{type: "edge", uuid, fact, source_node_uuid, target_node_uuid}`，embedding 文本 = `fact`
- embedding 调 OpenAI 兼容 `/embeddings`；首次写入自动探测维度并创建 collection（Cosine 距离）

### 3.3 任务状态

SQLite（`data/tasks.db`，自动建目录）：`tasks(task_id PK, graph_id, status, stage, progress, message, error, created_at, updated_at)`。状态机：`pending → parsing → chunking → ontology → extracting → resolving → writing → completed | failed`。

## 4. 抽取管道

```
上传文件 → ① 解析 → ② 切块 → ③ 本体生成 → ④ 逐块抽取 → ⑤ 消歧合并 → ⑥ 写入
                                ↑ 可跳过（请求带 ontology 时直接用）
```

1. **解析**：PyMuPDF 提取 PDF；md/txt 用 charset-normalizer 检测编码读取（与 MiroFish `file_parser.py` 同策略）
2. **切块**：默认 500/50（同 MiroFish `TextProcessor` 默认值），按段落边界优先切分
3. **本体生成**：LLM 读全文（超限时按块采样）+ 分析目的，输出格式**逐字段对齐** MiroFish `OntologyGenerator`：`{entity_types: [{name, description, attributes: [{name, type, description}], examples}], edge_types: [{name, description, source_targets: [实体类型对], attributes, examples}], analysis_summary}`。规范化规则同 MiroFish：PascalCase 实体名、SCREAMING_SNAKE_CASE 关系名、实体/关系类型数上限（16/24）、属性名白名单校验、Person/Organization 兜底补充
4. **逐块抽取**：每块一次 LLM 调用，prompt 携带完整本体，输出 `{entities: [{name, type, summary, attributes}], relations: [{source, target, type, fact, attributes}]}`。要求引用的实体类型/关系类型必须在本体内。JSON 解析失败 → 修复重试（把解析错误喂回去让模型自修，最多 2 次），仍失败则跳过该块并记录警告（不中断整个任务）
5. **消歧合并**（图谱质量关键，两阶段）：
   - 阶段一：`name.strip().casefold()` 精确匹配自动合并，summary 拼接
   - 阶段二：同类型实体两两做嵌入相似度（> 0.85 阈值，可配），候选对交 LLM 判定"是否指同一实体"，判定为同则合并（summary 由 LLM 融合成一段）
6. **实体 summary 聚合**：跨块出现多次的实体，各块 summary 交 LLM 合并成单段
7. **写入**：批量 MERGE Neo4j（`UNWIND` 批次 500）、Qdrant 批量 upsert（批次 256）

## 5. API 设计

统一响应 `{success, data?, error?}`。所有端点前缀 `/api`。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/graphs` | 创建图（名称）；返回 graph_id |
| GET | `/api/graphs` | 列出图 |
| GET | `/api/graphs/{graph_id}` | 图详情（节点/边计数、本体、状态） |
| DELETE | `/api/graphs/{graph_id}` | 删图（连带 Qdrant collection） |
| POST | `/api/graphs/{graph_id}/ontology` | 生成本体（multipart 文件 + 表单 purpose），同步返回本体 JSON |
| POST | `/api/graphs/{graph_id}/build` | 启动构建任务；JSON 体：`ontology`（可选，内联本体对象，来自上一步同步生成结果）、`chunk_size`、`chunk_overlap`；缺省 ontology 时管道自动生成；返回 task_id |
| GET | `/api/tasks/{task_id}` | 任务状态与进度 |
| GET | `/api/graphs/{graph_id}/nodes` / `/edges` | 分页读节点/边（`offset/limit`，返回 Zep 风格结构） |
| GET | `/api/graphs/{graph_id}/nodes/{uuid}` | 节点详情（含相关边与邻接节点） |
| POST | `/api/graphs/{graph_id}/search` | 语义搜索 `{query, top_k, types?: ["node","edge"]}` |
| GET | `/api/graphs/{graph_id}/export/mirofish` | MiroFish 兼容导出（见 §6） |

错误处理：4xx 带可读 error；LLM/存储故障 → 任务 failed 并保留已完成阶段成果（幂等重跑：抽取结果按块缓存于 SQLite，重跑只处理缺失块）。

## 6. MiroFish 兼容层（关键交付）

MiroFish 通过 Zep SDK 读取的数据结构（已从 `zep_entity_reader.py` 逐字段核实），GraphForge 导出端点精确产出同构 JSON：

**节点**（对齐 `EntityNode.to_dict()`）：

```json
{
  "uuid": "...", "name": "...",
  "labels": ["Entity", "Person"],
  "summary": "...", "attributes": {}
}
```

**边**（对齐 `get_all_edges` 的字典结构）：

```json
{
  "uuid": "...", "name": "KNOWS", "fact": "...",
  "source_node_uuid": "...", "target_node_uuid": "...",
  "attributes": {}
}
```

**导出格式**：`/export/mirofish` 返回一个 zip：`nodes.json`、`edges.json`、`ontology.json`（MiroFish `project.ontology` 同构）、`manifest.json`（图名、计数、生成时间、schema 版本）。

**接入路径**（文档中说明，不在本期实现）：MiroFish 侧只需实现一个提供 `fetch_all_nodes` / `fetch_all_edges` / `graph.search` 等价的适配器（或直接用导出文件注入其 `ProjectManager` 数据流），GraphForge 不反向依赖 MiroFish。

**合约测试**：`tests/test_mirofish_compat.py` 固化上述字段结构——用 MiroFish 仓库中拷贝的字段断言（节点 5 字段、边 6 字段）做双向校验，字段名漂移即测试失败。

## 7. Web UI（单页，4 个区块）

1. **图列表**：创建/删除/进入
2. **图工作台**：上传文档 → 生成本体（可查看类型卡片，只读）→ 启动构建 → 任务进度条
3. **图谱可视化**：d3 force 图，节点按类型着色，点选看详情（summary、属性、关联事实）
4. **搜索**：输入 query → 结果分节点/事实两组展示，点击定位到图中

构建产物仅由 FastAPI 静态托管（`web/dist`），开发期 Vite proxy。

## 8. 错误处理与运维要点

- LLM 调用：指数退避重试（429/5xx），JSON 修复重试 2 次，块级失败隔离（跳过 + 警告计数）
- 构建幂等：块级抽取结果缓存（SQLite `extract_cache(chunk_hash → result_json)`），失败重跑不重复计费
- Neo4j/Qdrant 连接：启动时探活，失败给可读错误；写入批次失败重试一次后整任务 failed
- 配置校验：启动时校验 LLM_API_KEY / NEO4J_URI / QDRANT_URL 缺失即拒绝启动（风格同 MiroFish `Config.validate`）
- 日志：structlog 风格结构化日志，任务阶段带 task_id 贯穿

## 9. 测试策略

- **单元**：chunker 边界、本体规范化（大小写/上限/白名单）、resolver 合并逻辑、fact/summary 组装
- **LLM mock**：extractor/ontology 用假 LLM（预置 JSON 响应与坏 JSON 场景），不触网
- **合约**：`test_mirofish_compat.py`（见 §6）；存储层用 testcontainers 起真实 Neo4j/Qdrant（无 Docker 环境时跳过）
- **管道集成**：小样本文本端到端跑通（mock LLM），断言节点/边/向量落库
- **API**：FastAPI TestClient 全端点冒烟

## 10. 里程碑

1. M1：项目骨架 + 配置 + Neo4j/Qdrant 存储 + 本体生成（对齐 MiroFish 格式）
2. M2：抽取管道全链路（抽取 → 消歧 → 写入）+ 任务系统
3. M3：语义搜索 + 导出端点 + 合约测试
4. M4：Web UI 四区块 + docker-compose 一键部署 + 端到端演示
