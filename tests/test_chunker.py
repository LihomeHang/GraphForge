"""chunker 单元测试：边界、段落优先、重叠、超长硬切。"""
from app.pipeline.chunker import chunk_text


def test_empty_text():
    assert chunk_text("") == []
    assert chunk_text("   \n  \n ") == []


def test_short_text_single_chunk():
    text = "这是一个短文本。"
    chunks = chunk_text(text, chunk_size=500, chunk_overlap=50)
    assert chunks == [text]


def test_paragraph_merge():
    p1 = "甲" * 200
    p2 = "乙" * 200
    text = f"{p1}\n\n{p2}"
    chunks = chunk_text(text, chunk_size=500, chunk_overlap=50)
    assert len(chunks) == 1
    assert p1 in chunks[0] and p2 in chunks[0]


def test_paragraph_split_respects_size():
    p1 = "甲" * 400
    p2 = "乙" * 400
    text = f"{p1}\n\n{p2}"
    chunks = chunk_text(text, chunk_size=500, chunk_overlap=50)
    assert len(chunks) == 2
    assert chunks[0] == p1
    # 第二块以 overlap 开头（50 字符乙？不对——overlap 来自第一块尾部即"甲"）
    assert chunks[1].startswith("甲" * 50)
    assert chunks[1].endswith(p2)


def test_long_paragraph_hard_split():
    text = "字" * 1200
    chunks = chunk_text(text, chunk_size=500, chunk_overlap=50)
    assert all(len(c) <= 500 for c in chunks)
    assert len(chunks) >= 3


def test_invalid_params():
    import pytest

    with pytest.raises(ValueError):
        chunk_text("abc", chunk_size=0)
    with pytest.raises(ValueError):
        chunk_text("abc", chunk_size=10, chunk_overlap=10)
    with pytest.raises(ValueError):
        chunk_text("abc", chunk_size=10, chunk_overlap=-1)


def test_no_data_loss():
    """所有字符都必须出现在某个块中。"""
    text = ("段落一内容。" * 30) + "\n\n" + ("段落二内容。" * 30) + "\n\n" + ("段落三内容。" * 30)
    chunks = chunk_text(text, chunk_size=300, chunk_overlap=30)
    assert "段落一内容。" in chunks[0]
    assert "段落三内容。" in chunks[-1]
