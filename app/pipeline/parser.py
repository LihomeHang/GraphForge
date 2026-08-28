"""文档解析：PDF (PyMuPDF) / md / txt (charset-normalizer 编码探测)。"""
from __future__ import annotations

import logging
from pathlib import Path

import charset_normalizer
import pymupdf  # PyMuPDF

logger = logging.getLogger("graphforge.parser")

SUPPORTED_SUFFIXES = {".pdf", ".md", ".txt", ".markdown"}


class ParseError(RuntimeError):
    pass


def parse_file(path: str | Path) -> str:
    """按扩展名解析文件为纯文本。"""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ParseError(f"不支持的文件类型: {suffix}（支持 {sorted(SUPPORTED_SUFFIXES)}）")
    if suffix == ".pdf":
        return _parse_pdf(p)
    return _parse_text(p)


def parse_bytes(content: bytes, filename: str) -> str:
    """按文件名扩展名解析字节内容（上传场景）。"""
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ParseError(f"不支持的文件类型: {suffix}（支持 {sorted(SUPPORTED_SUFFIXES)}）")
    if suffix == ".pdf":
        return _parse_pdf_bytes(content)
    return _decode_text(content)


def _parse_pdf(path: Path) -> str:
    return _parse_pdf_bytes(path.read_bytes())


def _parse_pdf_bytes(content: bytes) -> str:
    try:
        with pymupdf.open(stream=content, filetype="pdf") as doc:
            pages = [page.get_text() for page in doc]
    except Exception as e:
        raise ParseError(f"PDF 解析失败: {e}") from e
    text = "\n\n".join(pages).strip()
    if not text:
        raise ParseError("PDF 内容为空（可能是扫描件，不支持 OCR）")
    return text


def _parse_text(path: Path) -> str:
    raw = path.read_bytes()
    return _decode_text(raw)


def _decode_text(raw: bytes) -> str:
    result = charset_normalizer.from_bytes(raw).best()
    if result is None:
        raise ParseError("无法检测文件编码或文件为空")
    text = str(result).strip()
    if not text:
        raise ParseError("文件内容为空")
    return text
