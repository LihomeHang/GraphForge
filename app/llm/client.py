"""OpenAI 兼容 chat 客户端（JSON 模式 + 重试），以及测试用 mock 客户端。"""
from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from contextvars import ContextVar
from typing import Callable

import httpx

from app.config import Config

logger = logging.getLogger("graphforge.llm")

# 任务日志汇：构建任务运行期注册（contextvars 按 asyncio task 隔离，并行构建互不串扰），
# LLM 重试等事件透传到任务日志，前端可直接查看
_task_log_sink: ContextVar[Callable[[str], None] | None] = ContextVar("task_log_sink", default=None)


def set_task_log_sink(fn: Callable[[str], None]) -> object:
    return _task_log_sink.set(fn)


def emit_task_log(message: str) -> None:
    sink = _task_log_sink.get()
    if sink is not None:
        try:
            sink(message)
        except Exception:  # noqa: BLE001  日志汇异常不影响主流程
            pass


class LLMError(RuntimeError):
    """LLM 调用失败（网络/超限等）。"""


class LLMJsonError(LLMError):
    """LLM 返回内容无法解析为 JSON（携带原始文本，供修复重试）。"""

    def __init__(self, raw: str, inner: str):
        super().__init__(f"LLM 返回非 JSON: {inner}")
        self.raw = raw
        self.inner = inner


def parse_json_loose(text: str) -> dict:
    """稳健解析 LLM 输出：剥 code fence，截取首尾花括号。返回 dict（对象）。"""
    if text is None:
        raise LLMJsonError("", "empty response")
    t = text.strip()
    if t.startswith("```"):
        # 去掉 ```json ... ```
        lines = t.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise LLMJsonError(text, "no JSON object braces found")
    candidate = t[start : end + 1]
    try:
        obj = json.loads(candidate)
    except json.JSONDecodeError as e:
        raise LLMJsonError(text, str(e)) from e
    if not isinstance(obj, dict):
        raise LLMJsonError(text, "top-level JSON is not an object")
    return obj


class LLMClient(ABC):
    """统一 LLM 接口：complete 返回原始文本，complete_json 解析为 dict。"""

    @abstractmethod
    async def complete(self, messages: list[dict[str, str]]) -> str:
        ...

    async def complete_json(self, messages: list[dict[str, str]]) -> dict:
        text = await self.complete(messages)
        return parse_json_loose(text)

    async def close(self) -> None:  # noqa: B027
        return None


class OpenAILLMClient(LLMClient):
    def __init__(self, config: Config, max_retries: int = 4):
        self.config = config
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(
            base_url=config.llm_base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {config.llm_api_key}"},
            # 300s：深度思考模型（如 glm-5.3-flash）本体/抽取大输出 + 长 reasoning，
            # 120s 会反复 ReadTimeout 表现为任务卡死
            timeout=httpx.Timeout(300.0, connect=10.0),
        )

    async def complete(self, messages: list[dict[str, str]]) -> str:
        payload = {
            "model": self.config.llm_model,
            "messages": messages,
            "temperature": self.config.llm_temperature,
        }
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = await self._client.post("/chat/completions", json=payload)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise LLMError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                resp.raise_for_status()
                data = resp.json()
                content = data["choices"][0]["message"]["content"]
                return content or ""
            except (httpx.HTTPError, LLMError, KeyError, IndexError, json.JSONDecodeError) as e:
                last_exc = e
                delay = 2.0 ** attempt
                # 超时类异常 str() 可能为空（如 httpx.ReadTimeout），补充类型名便于诊断
                detail = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                logger.warning("LLM 调用失败(第 %s 次): %s，%.1fs 后重试", attempt + 1, detail, delay)
                emit_task_log(f"LLM 第{attempt + 1}次调用失败: {detail}，{delay:.0f}s 后重试")
                await asyncio.sleep(delay)
        exhaust = f"LLM 调用重试耗尽: {type(last_exc).__name__}: {last_exc}"
        emit_task_log(exhaust)
        raise LLMError(exhaust) from last_exc

    async def close(self) -> None:
        await self._client.aclose()


class MockLLMClient(LLMClient):
    """测试用假 LLM：按顺序弹出预置响应（str 原文，可注入坏 JSON）。"""

    def __init__(self, responses: list[str] | None = None):
        self.responses: list[str] = list(responses or [])
        self.calls: list[list[dict[str, str]]] = []

    def enqueue(self, response: str) -> None:
        self.responses.append(response)

    def enqueue_json(self, obj: dict) -> None:
        self.responses.append(json.dumps(obj, ensure_ascii=False))

    async def complete(self, messages: list[dict[str, str]]) -> str:
        self.calls.append(messages)
        if not self.responses:
            # 队列耗尽是测试代码的错误，静默返回空对象会掩盖响应错位问题
            raise LLMError("MockLLMClient 响应队列已耗尽（enqueue 不足）")
        return self.responses.pop(0)


def build_llm_client(config: Config) -> LLMClient:
    if config.llm_provider == "mock":
        return MockLLMClient()
    return OpenAILLMClient(config)
