"""Markdown 工具 — 对应 src/document/parser/utils/markdown.util.ts。"""

from __future__ import annotations

import re


def clean_markdown(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n")
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def escape_table_cell(value: str) -> str:
    return re.sub(r"\|", "\\|", value).replace("\r\n", " ").replace("\n", " ").strip()


def to_markdown_table(rows: list[list[str]]) -> str:
    """二维数组 → Markdown 表格（首行表头 + `| --- |` 分隔，按最大列对齐）。"""
    if not rows:
        return ""
    max_cols = max((len(r) for r in rows), default=0)
    if max_cols == 0:
        return ""
    lines: list[str] = []
    for r_idx, row in enumerate(rows):
        cells = [escape_table_cell(row[c] if c < len(row) else "") for c in range(max_cols)]
        lines.append(f"| {' | '.join(cells)} |")
        if r_idx == 0:
            lines.append(f"| {' | '.join(['---'] * max_cols)} |")
    return "\n".join(lines) + "\n\n"


def get_extension(filename: str | None) -> str:
    if not filename or "." not in filename:
        return ""
    return filename.rsplit(".", 1)[1].lower()


def decode_upload_filename(filename: str | None) -> str:
    """multipart 文件名可能被按 Latin-1 误解码（中文乱码），还原为 UTF-8。"""
    if not filename:
        return ""
    try:
        decoded = filename.encode("latin1").decode("utf8")
        if "�" in decoded:
            return filename
        return decoded
    except (UnicodeDecodeError, UnicodeEncodeError):
        return filename


def title_from_filename(filename: str | None) -> str:
    if not filename:
        return "未命名文档"
    idx = filename.rfind(".")
    return filename[:idx] if idx > 0 else filename
