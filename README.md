# GraphForge

独立知识图谱工具：把文档（pdf / md / txt）变成可查询、可导出的知识图谱。

上传一个或多个文档（pdf / md / txt，多文件合并构建同一图谱）+ 一句分析目的 → LLM 生成本体（实体/关系类型）→ 逐块抽取实体与关系 → 消歧合并 → 写入 **Neo4j**（图）与 **Qdrant**（向量）→ 语义搜索 + **MiroFish 兼容导出** + Vue 3 可视化界面。

## 功能

- **文档解析**：pdf（PyMuPDF）/ Markdown / 纯文本，UTF-8 / GBK 自动识别
- **本体生成**：按分析目的由 LLM 设计实体与关系类型，自动规范化（实体名 PascalCase、关系名 SCREAMING_SNAKE_CASE、类型白名单、MiroFish 风格兜底）
- **双本体模式**：`strict` 将本体作为白名单；`soft` 将本体作为优先分类，并用 `Entity` / `RELATED_TO` 保留未覆盖的知识与事实（MiroFish 兼容模式）
- **抽取管道**：段落优先切块（默认 1200 字 / 100 重叠）→ 并发抽取（默认 8 路、信号量限流）→ 块级失败隔离 → JSON 修复重试 → SQLite 块级缓存（幂等重跑不重复计费）
- **消歧合并**：casefold 精确合并 → 同类型嵌入相似候选对交 LLM 判定（保守失败不合并）→ 并查集合并 + summary 融合
- **存储**：Neo4j 5.x（属性存 `attributes_json`）+ Qdrant（每图一个 collection，节点/边混合向量）
- **语义搜索**：查询向量化 → 节点/事实混合检索，边命中自动补端点实体名
- **MiroFish 导出**：`nodes.json`（uuid/name/labels/summary/attributes）+ `edges.json`（uuid/name/fact/source_node_uuid/target_node_uuid/attributes）+ `ontology.json` + `manifest.json`，字段由合约测试固化
- **Web UI**：图列表 / 构建工作台（进度轮询）/ d3 力导向图可视化 / 语义搜索四区块 / **系统设置（Web 端配置 LLM，热生效）**

## 快速开始（docker-compose，一键全栈）

```bash
cp .env.example .env        # 填入 LLM_API_KEY 等
docker compose up -d --build
# 打开 http://localhost:8080
```

服务端口：

| 服务 | 地址 |
|------|------|
| Web UI / API | http://localhost:8080 |
| Neo4j Browser | http://localhost:7474（neo4j / graphforge-dev） |
| Qdrant | http://localhost:6333 |

试一下真实 LLM 模式（`.env` 里配置 OpenAI 兼容端点与 key）后，在界面：创建图谱 → 上传文档 → 生成本体（或直接构建）→ 等进度完成 → 搜索 / 可视化 / 导出 zip。

## Web 端 LLM 设置（热生效）

界面右上角 ⚙ 设置可直接配置 LLM / Embedding（Provider / Base URL / API Key / 模型 / Temperature / Embedding 高级项），**保存后立即生效，无需重启容器**：

- 配置持久化在 `data/settings.db`，优先级高于环境变量 / `.env`；重启服务仍生效
- API Key 只回显掩码（`sk-***abcd`），留空表示保持现有值
- 支持「测试连接」（LLM ping + Embedding 维度探测，20s 超时）
- 有构建任务运行中时拒绝修改（409），避免运行中任务拿到已关闭的客户端

对应 API：`GET /api/settings`（掩码查看）/ `PUT /api/settings`（更新）/ `POST /api/settings/test`（连通性测试，不保存）

## 构建模式

`POST /api/graphs/{graph_id}/build` 支持以下兼容参数：

- `ontology_mode`: `strict`（默认）或 `soft`；软模式不会丢弃自定义本体之外的实体和事实
- `replace_existing`: 默认 `true`，新构建在写入前替换已有 Neo4j/Qdrant 数据，避免重建累积
- `documents_are_chunks`: 默认 `false`；设为 `true` 时每个暂存文件直接作为一个抽取块，不再二次切分

上传文档时可向 `POST /api/graphs/{graph_id}/documents` 传 multipart 字段 `replace_existing=true`。服务会先校验全部新文件，再整体替换暂存输入。MiroFish 适配器会逐 episode 上传并启用上述软本体参数。

MiroFish 侧需要修改的文件、适配器行为、Docker 配置和验证步骤见 [MiroFish 集成要求](./MIROFISH-INTEGRATION-REQUIREMENTS.md)。

## 本地开发

```bash
# 后端（需本地或 docker 的 Neo4j/Qdrant）
uv sync
cp .env.example .env
uv run uvicorn app.main:app --reload --port 8080

# 前端
cd web && npm install && npm run dev   # http://localhost:5173，代理 /api 到 8080

# 测试（不触网：mock LLM + mock embeddings）
uv run pytest tests -q
```

## Mock 模式（离线演示 / CI）

`LLM_PROVIDER=mock` 时使用确定性假客户端，不需要任何 API key：

- `MockLLMClient`：按队列返回预置响应；队列耗尽即抛错（fail-fast，防止响应错位被静默吞掉）；本体生成与块抽取在 mock 下有词频启发式兜底，保证无网络也能跑通全链路
- `MockEmbeddingClient`：文本 sha256 → 固定维度伪随机单位向量（余弦可比较）

`scripts/demo_e2e.py` 是对运行中服务的端到端演示脚本（创建→上传→构建→搜索→导出）。

## 配置（环境变量，见 .env.example）

| 变量 | 说明 |
|------|------|
| `LLM_PROVIDER` | `openai`（兼容接口）或 `mock` |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | LLM 端点配置 |
| `EMBEDDING_BASE_URL` / `EMBEDDING_API_KEY` / `EMBEDDING_MODEL` | 缺省复用 LLM 配置 |
| `EMBEDDING_PROVIDER` | `auto`（远程失败切本地）、`remote`（仅远程）或 `local`（纯本地无模型） |
| `EMBEDDING_BATCH_SIZE` / `EMBEDDING_CONCURRENCY` / `EMBEDDING_MAX_RETRIES` | 嵌入请求分批、并发和瞬时故障重试（128 / 4 / 2） |
| `LOCAL_EMBEDDING_DIM` | 本地特征哈希向量维度（1024） |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | Neo4j 连接 |
| `QDRANT_URL` | Qdrant 地址 |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | 切块参数（1200 / 100，可在系统设置中热调整） |
| `LLM_CONCURRENCY` | 抽取并发（8，可在系统设置中热调整） |
| `EXTRACT_BATCH_SIZE` | 每次 LLM 请求处理的块数（4，可在系统设置中热调整） |
| `RESOLVE_SIM_THRESHOLD` | 消歧嵌入相似阈值（0.85） |
| `RESOLVE_CANDIDATE_K` | 每个实体最多进入 LLM 消歧的近邻数（8） |
| `EMBEDDING_DIM` | 嵌入维度；缺省自动探测（mock 模式为 32） |
| `QDRANT_API_KEY` | Qdrant 开启鉴权时必填 |
| `DATA_DIR` | SQLite 任务/缓存目录（`./data`） |
| `LLM_TEMPERATURE` / `EXTRACT_MAX_RETRY` / `ENTITY_TYPE_LIMIT` / `EDGE_TYPE_LIMIT` / `NEO4J_BATCH_SIZE` / `QDRANT_BATCH_SIZE` | 高级调优项，有合理默认值 |

## 架构

```
文档 → parser → chunker → ontology(LLM) → extractor(LLM×N, 缓存)
     → resolver(嵌入+LLM消歧) → writer → Neo4j + Qdrant
                                        ↘ search / export / Web UI
```

- `app/config.py` 配置；`app/models/` Pydantic 模型
- `app/llm/` OpenAI 兼容客户端（429/5xx 指数退避）+ mock
- `app/pipeline/` 解析、切块、本体、抽取、消歧、写入（六阶段任务）
- `app/storage/` neo4j_store / qdrant_store / tasks（SQLite 任务与抽取缓存，重启中断任务自动标 failed）
- `app/api/` graphs / documents / read / search / export / tasks
- `web/` Vue 3 + Vite + d3

## 测试

```bash
uv run pytest tests -q   # 34 项：单元 + MiroFish 合约 + API 冒烟（全 mock，不触网）
```
