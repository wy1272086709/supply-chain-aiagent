"""文档分块 — 对应 chunking.service.ts（langchain-textsplitters markdown 感知切分）。"""

from __future__ import annotations

import hashlib
import logging
import re

from langchain_text_splitters import Language, RecursiveCharacterTextSplitter

from app.core.config import get_settings

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = 2.0
HEADING_LINE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

_splitter: RecursiveCharacterTextSplitter | None = None


def _get_splitter() -> RecursiveCharacterTextSplitter:
    global _splitter
    if _splitter is None:
        cfg = get_settings()
        chunk_size = int(cfg.rag_chunk_size * CHARS_PER_TOKEN)
        chunk_overlap = int(cfg.rag_chunk_overlap * CHARS_PER_TOKEN)
        markdown_separators = RecursiveCharacterTextSplitter.get_separators_for_language(
            Language.MARKDOWN
        )
        # 内置 markdown 分隔符未含 H1（\n# ），补上以免一级标题不切分
        _splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            keep_separator=True,
            separators=["\n# ", *markdown_separators],
        )
    return _splitter


def _extract_heading(text: str) -> str | None:
    m = HEADING_LINE.search(text)
    return m.group(2).strip() if m and m.group(2).strip() else None


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

    texts = _get_splitter().split_text(content)
    chunks: list[dict] = []
    current_heading: str | None = None

    for text in texts:
        trimmed = text.strip()
        if not trimmed:
            continue

        heading_in_chunk = _extract_heading(trimmed)
        if heading_in_chunk:
            current_heading = heading_in_chunk

        chunk_content = trimmed
        if current_heading and not HEADING_LINE.search(trimmed):
            chunk_content = f"{current_heading}\n\n{trimmed}"

        chunk_id = hashlib.sha256(
            f"{document_id}:{len(chunks)}".encode("utf-8")
        ).hexdigest()[:64]
        chunks.append(
            {
                "chunkId": chunk_id,
                "documentId": document_id,
                "documentTitle": document_title,
                "content": chunk_content,
                "heading": current_heading,
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
