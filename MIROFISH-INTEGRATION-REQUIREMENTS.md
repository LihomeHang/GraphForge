# MiroFish 集成要求

本文记录 MiroFish 为接入 GraphForge 所需的代码修改、运行配置与验证方式。GraphForge 负责知识图谱抽取、消歧、存储和实时预览；MiroFish 保留项目、任务、仿真与报告流程。

本文只包含空值示例。真实 API Key、私有模型端点、账号名和连接密码必须保存在各仓库本地的 `.env` 中，不得提交到 Git。

## MiroFish 侧修改清单

| 文件 | 修改内容 |
| --- | --- |
| `backend/app/config.py` | 新增 `GRAPHFORGE_BASE_URL`、`GRAPHFORGE_API_KEY`；配置 GraphForge 时不再强制要求 Zep Key |
| `backend/app/utils/graphforge.py` | 新增 Zep 形状的 GraphForge REST 适配器，负责图谱、批次、节点、边、搜索和实时预览读取 |
| `backend/app/utils/zep.py` | `GRAPHFORGE_BASE_URL` 存在时返回 `GraphForgeClient`，否则保留原 Zep Cloud 路径 |
| `backend/tests/test_graphforge_adapter.py` | 覆盖 episode 上传、软本体构建、替换构建、实时预览及长任务等待 |
| `frontend/src/views/projectGraphState.js` | 将项目状态转换为加载图谱、轮询图谱和轮询任务的稳定策略 |
| `frontend/src/views/MainView.vue` | 只要存在 `graph_id` 就自动读取图数据；构建或中断时持续轮询实时预览 |
| `frontend/src/views/projectGraphState.test.js` | 覆盖构建中、完成、失败但已有图谱以及尚无图谱四种状态 |
| `Dockerfile.graphforge` | 基于 MiroFish 上游镜像重建修改后的前端资源 |
| `docker-compose.yml` | 启用集成镜像、宿主机访问地址和本地后端代码挂载 |
| `.env.example` | 增加空值 GraphForge 配置，不包含账号或密钥 |

## 适配器行为

### 图谱创建与本体

MiroFish 仍通过原有 Zep 调用形状创建图谱和设置动态本体。适配器把本体转换为 GraphForge 的 `entity_types`、`edge_types` 和 `source_targets` 结构，并在批次构建时提交给 GraphForge。

### 文档上传与构建

每个 MiroFish episode 作为一个独立文本文件上传。构建请求使用：

```json
{
  "purpose": "MiroFish knowledge graph extraction",
  "ontology_mode": "soft",
  "replace_existing": true,
  "documents_are_chunks": true
}
```

- `soft`：优先使用 MiroFish 本体，同时保留本体外实体和事实。
- `replace_existing`：重新构建前替换旧输入和旧图数据，避免重复累积。
- `documents_are_chunks`：episode 已经是抽取边界，GraphForge 不再二次切块。

提交构建请求的 HTTP 超时为 300 秒，等待 GraphForge 后台任务的上限为 3600 秒，以支持大文档图谱。

### 实时图谱

GraphForge 构建期间，适配器从 `GET /api/graphs/{graph_id}/preview` 读取节点和边。节点直接使用预览 UUID；缺少 UUID 的预览边根据图谱、端点、关系名和事实生成稳定 UUID。节点与边共享一秒快照缓存，避免同一轮前端刷新重复请求。

GraphForge 构建完成后，适配器自动切换到分页读取最终节点和边。MiroFish 前端只要发现项目已有 `graph_id` 就加载图数据，即使旧任务因超时或进程中断被标记为失败，也能继续显示 GraphForge 中已经生成的预览或最终图谱。

## 本地配置

GraphForge `.env` 至少配置：

```env
LLM_BASE_URL=
LLM_API_KEY=
LLM_MODEL=
NEO4J_PASSWORD=
```

启动 GraphForge：

```bash
docker compose up -d --build
```

GraphForge 默认发布在宿主机 `http://localhost:8080`。

MiroFish `.env` 配置：

```env
LLM_API_KEY=
LLM_BASE_URL=
LLM_MODEL_NAME=

GRAPHFORGE_BASE_URL=http://host.docker.internal:8080
GRAPHFORGE_API_KEY=
```

GraphForge 未启用 API 鉴权时，`GRAPHFORGE_API_KEY` 保持为空。Docker Desktop 中的 MiroFish 容器通过 `host.docker.internal` 访问宿主机 GraphForge。

启动 MiroFish 集成镜像：

```bash
docker compose up -d --build
```

默认前端地址为 `http://localhost:3000`，MiroFish 后端为 `http://localhost:5001`。

## 验证

1. 在 MiroFish 新建项目并上传文档。
2. 进入 GraphRAG 构建页面，确认已有 `graph_id` 后自动加载图谱。
3. 构建过程中确认节点和边数量从零开始增长，不需要手动点击刷新。
4. 构建完成后刷新项目页面，确认最终节点和边仍自动显示且数量非零。
5. 在 GraphForge 查询对应图谱，确认状态为 `completed` 且最终节点和边可分页读取。

相关自动测试：

```bash
# MiroFish backend
pytest backend/tests/test_graphforge_adapter.py -q

# MiroFish frontend
node --test frontend/src/views/projectGraphState.test.js
npm --prefix frontend run build
```

## 提交安全检查

- `.env`、`.env.local`、`.env.*.local`、日志和上传文件必须保持忽略且未跟踪。
- `.env.example` 只保留变量名和空值，不写真实端点、模型账号或密钥。
- 提交前检查 `git status --short`、`git diff --cached --check`，并扫描暂存内容中的密钥前缀和私有域名。
- 密钥一旦出现在聊天、日志或 Git 历史中，应立即在服务端轮换；从工作区删除并不能使旧密钥失效。
