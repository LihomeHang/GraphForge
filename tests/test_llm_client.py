"""LLM client 单元测试：JSON 解析（code fence / 前后杂讯）、坏 JSON 抛错、mock 客户端。"""
import pytest

from app.llm.client import LLMError, LLMJsonError, MockLLMClient, parse_json_loose


def test_parse_plain_json():
    assert parse_json_loose('{"a": 1}') == {"a": 1}


def test_parse_code_fence():
    text = '```json\n{"a": 1}\n```'
    assert parse_json_loose(text) == {"a": 1}


def test_parse_with_noise():
    text = '好的，以下是结果：\n{"a": {"b": 2}}\n希望有帮助'
    assert parse_json_loose(text) == {"a": {"b": 2}}


def test_parse_failure_raises():
    with pytest.raises(LLMJsonError):
        parse_json_loose("没有 JSON")
    with pytest.raises(LLMJsonError):
        parse_json_loose("")


@pytest.mark.asyncio
async def test_mock_client_queue():
    client = MockLLMClient()
    client.enqueue_json({"x": 1})
    client.enqueue("not json")
    assert await client.complete([]) == '{"x": 1}'
    assert await client.complete([]) == "not json"
    # 队列耗尽 → LLMError（fail-fast，避免静默空结果掩盖响应错位）
    with pytest.raises(LLMError):
        await client.complete([])


@pytest.mark.asyncio
async def test_complete_json_with_repair():
    """坏 JSON → LLMJsonError 携带原文，供修复重试。"""
    client = MockLLMClient(["前置说明 {\"ok\": true} 后缀"])
    obj = await client.complete_json([{"role": "user", "content": "hi"}])
    assert obj == {"ok": True}
    assert len(client.calls) == 1
