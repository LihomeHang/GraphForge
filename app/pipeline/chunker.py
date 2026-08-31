"""文本切块：默认 500 字符 / 50 重叠（同 MiroFish TextProcessor 默认值），优先按段落边界切分。"""
from __future__ import annotations

import re

_PARA_RE = re.compile(r"\n\s*\n")


def chunk_text(
    text: str,
    chunk_size: int = 1200,
    chunk_overlap: int = 100,
) -> list[str]:
    """把文本切成带重叠的块。优先段落边界，其次换行边界，最后硬切。

    规则：
    - 空文本返回 []。
    - 段落 <= chunk_size 时尽量整段成块（相邻段落合并直到超出）。
    - 超长段落先按换行切，再按 chunk_size 硬切。
    - 相邻块之间保留 chunk_overlap 字符重叠。
    """
    if not text or not text.strip():
        return []
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须 > 0")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap 必须 >= 0 且 < chunk_size")

    paragraphs = [p.strip() for p in _PARA_RE.split(text) if p.strip()]

    # 展平：超长段落内部再切
    pieces: list[str] = []
    for para in paragraphs:
        if len(para) <= chunk_size:
            pieces.append(para)
        else:
            pieces.extend(_split_long(para, chunk_size))

    # 合并 pieces 成块（贪心），块间加 overlap
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for piece in pieces:
        add_len = len(piece) + (1 if current else 0)  # 拼接时的 \n\n
        if current and current_len + add_len > chunk_size:
            chunks.append("\n\n".join(current))
            # overlap：从当前块尾部取 overlap 字符作为下一块开头
            tail = chunks[-1][-chunk_overlap:] if chunk_overlap > 0 else ""
            current = [tail] if tail else []
            current_len = len(tail)
            # 若尾部 overlap + 新 piece 仍超限，则丢弃 overlap（保证单 piece 能进块）
            add_len = len(piece) + (1 if current else 0)
            while current and current_len + add_len > chunk_size:
                removed = current.pop(0)
                current_len -= len(removed) + (1 if current else 1)
        current.append(piece)
        current_len += add_len
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _split_long(text: str, chunk_size: int) -> list[str]:
    """超长段落：按换行切，仍超长则硬切。"""
    out: list[str] = []
    for seg in text.split("\n"):
        seg = seg.strip()
        if not seg:
            continue
        while len(seg) > chunk_size:
            out.append(seg[:chunk_size])
            seg = seg[chunk_size:]
        if seg:
            out.append(seg)
    return out
