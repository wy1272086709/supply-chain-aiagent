"""文档分块 — 标题感知 + 表格原子切分（替代原 langchain-textsplitters 递归切分）。

原递归按字符窗口切会把 Markdown 表格拦腰截断，导致规则类内容（时限/费率表）
在向量与切题评估里不完整。现按标题分节、表格整块保留，每块自带所属标题。
"""

from __future__ import annotations

import hashlib
import logging
import re

from app.core.config import get_settings

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = 2.0
HEADING_LINE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def _extract_heading(text: str) -> str | None:
    m = HEADING_LINE.search(text)
    return m.group(2).strip() if m and m.group(2).strip() else None


def _split_sections(content: str) -> list[tuple[str, str, str]]:
    """按标题切成 (heading全文, 标题文本, 正文)；标题前的开头算无标题节。"""
    sections: list[tuple[str, str, str]] = []
    heading_line: str = ""
    heading_text: str = ""
    body: list[str] = []

    def flush() -> None:
        text_body = "\n".join(body).strip()
        if heading_line or text_body:
            sections.append((heading_line, heading_text, text_body))

    for line in content.split("\n"):
        m = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if m:
            flush()
            heading_line = line
            heading_text = m.group(2).strip()
            body = []
        else:
            body.append(line)
    flush()
    return sections


def _split_body_blocks(body: str, max_chars: int) -> list[str]:
    """正文按 段落/表格 两种块切；表格原子不切断，块合并到不超过 max_chars。"""
    blocks: list[str] = []
    para: list[str] = []
    table: list[str] = []

    def flush_para() -> None:
        if para:
            blocks.append("\n".join(para).strip())
            para.clear()

    def flush_table() -> None:
        if table:
            blocks.append("\n".join(table))
            table.clear()

    for line in body.split("\n"):
        if line.lstrip().startswith("|"):
            flush_para()
            table.append(line)
        else:
            flush_table()
            para.append(line)
    flush_para()
    flush_table()

    out: list[str] = []
    current = ""
    for block in (b for b in blocks if b.strip()):
        if len(block) > max_chars:
            if current:
                out.append(current)
                current = ""
            if block.lstrip().startswith("|"):
                out.append(block)  # 超长表格整体保留，不拦腰切断
            else:
                for i in range(0, len(block), max_chars):
                    out.append(block[i : i + max_chars])
            continue
        if current and len(current) + len(block) + 2 > max_chars:
            out.append(current)
            current = block
        else:
            current = f"{current}\n\n{block}" if current else block
    if current:
        out.append(current)
    return out


def chunk(
    *,
    content: str,
    document_id: str,
    document_title: str,
    category_id: str | None = None,
    author_id: str | None = None,
    team_id: str | None = None,
    is_public: bool | None = None,
    doc_status: int | None = None,
    publish_time: str | None = None,
) -> list[dict]:
    if not (content or "").strip():
        logger.warning("文档内容为空，跳过分块：documentId=%s", document_id)
        return []

    cfg = get_settings()
    max_chars = int(cfg.rag_chunk_size * CHARS_PER_TOKEN)
    chunks: list[dict] = []

    for heading_line, heading_text, body in _split_sections(content):
        for part in _split_body_blocks(body, max_chars):
            if not part:
                continue
            # 每块都带上所属标题，保证向量与切题评估看到的是自包含语义单元
            chunk_content = f"{heading_line}\n\n{part}" if heading_line else part
            chunk_id = hashlib.sha256(
                f"{document_id}:{len(chunks)}".encode("utf-8")
            ).hexdigest()[:64]
            chunks.append(
                {
                    "chunkId": chunk_id,
                    "documentId": document_id,
                    "documentTitle": document_title,
                    "content": chunk_content,
                    "heading": heading_text or None,
                    "chunkIndex": len(chunks),
                    "totalChunks": 0,
                    "categoryId": category_id,
                    "authorId": author_id,
                    "teamId": team_id,
                    "isPublic": is_public,
                    "docStatus": doc_status,
                    "publishTime": publish_time,
                }
            )

    for c in chunks:
        c["totalChunks"] = len(chunks)
    return chunks
