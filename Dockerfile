# ---- 前端构建 ----
FROM node:20-alpine AS web-builder
WORKDIR /web
COPY web/package.json web/package-lock.json* ./
RUN npm install
COPY web/ ./
RUN npm run build

# ---- Python 运行时 ----
FROM python:3.12-slim
WORKDIR /app

# uv 安装依赖
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev || uv sync --no-dev

# 应用代码
COPY app/ ./app/
COPY --from=web-builder /web/dist ./web/dist

# 数据目录
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uv", "run", "--no-dev", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
