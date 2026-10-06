"""文件解析分发 — 对应 file-parser.service.ts。"""

from __future__ import annotations

import asyncio
import logging
import time

from app.core.errors import BadRequestError as BadRequestException
from app.modules.documents.parsers.docx_parser import parse_docx
from app.modules.documents.parsers.markdown_util import get_extension
from app.modules.documents.parsers.pdf_parser import parse_pdf
from app.modules.documents.parsers.plain_text import parse_plain_text
from app.modules.documents.parsers.pptx_parser import parse_pptx
from app.modules.documents.parsers.xlsx_parser import parse_xlsx

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {"pdf", "docx", "xlsx", "pptx", "txt", "md"}


def is_supported(extension: str) -> bool:
    return (extension or "").lower() in SUPPORTED_EXTENSIONS


def supported_list() -> str:
    return ", ".join(sorted(SUPPORTED_EXTENSIONS))


async def parse(file_name: str, data: bytes, rustfs_upload=None, rustfs_enabled: bool = False) -> str:
    extension = get_extension(file_name)
    if not is_supported(extension):
        raise BadRequestException(
            f"不支持的文件格式: {extension or '(无扩展名)'}，支持的格式: {supported_list()}"
        )
    if not data:
        raise BadRequestException("文件内容为空，无法解析")

    start = time.monotonic()
    ext = extension.lower()
    if ext == "docx":
        result = await asyncio.to_thread(parse_docx, data)
    elif ext == "pdf":
        upload_image = None
        if rustfs_enabled and rustfs_upload is not None:
            async def upload_image(bytes_: bytes, file_name_: str, content_type: str) -> str:
                return await rustfs_upload(bytes_, file_name=file_name_, content_type=content_type,
                                           prefix="pdf-images")

        result = await parse_pdf(data, upload_image=upload_image)
    elif ext == "pptx":
        result = await asyncio.to_thread(parse_pptx, data)
    elif ext == "xlsx":
        result = await _parse_xlsx_with_fallback(data)
    else:  # txt / md
        result = parse_plain_text(data)

    elapsed = int((time.monotonic() - start) * 1000)
    logger.info(
        "文件解析完成: name=%s, format=%s, chars=%d, elapsed=%dms",
        file_name, ext, len(result), elapsed,
    )

    if not result.strip():
        raise BadRequestException("文件解析结果为空，请确认文件包含可提取的文本内容")
    return result


async def _parse_xlsx_with_fallback(data: bytes) -> str:
    """openpyxl 优先；失败降级重载（只读模式，公式字符串）。"""
    try:
        start = time.monotonic()
        result = await asyncio.to_thread(parse_xlsx, data, True)
        logger.info(
            "XLSX(openpyxl) 解析成功: chars=%d, elapsed=%dms",
            len(result), int((time.monotonic() - start) * 1000),
        )
        return result
    except Exception as e:  # noqa: BLE001
        logger.warning("XLSX(openpyxl) 解析失败，降级重载: %s", e)
        return await asyncio.to_thread(parse_xlsx, data, False)
