"""PDF → Markdown — pypdf（文本/图片）+ pdfplumber（表格），对应 pdf.parser.ts（pdf-parse v2）。

流程：按页文本 → 可选图片提取上传（阈值 50px）→ 按页拼装 →
正文无 Markdown 表格时追加「检测到的表格」附录。
"""

from __future__ import annotations

import io
import logging

from app.modules.documents.parsers.markdown_util import clean_markdown, to_markdown_table

logger = logging.getLogger(__name__)

IMAGE_THRESHOLD = 50


def _sniff_image_content_type(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"\x89PNG":
        return "image/png"
    if data[:4] == b"RIFF":
        return "image/webp"
    return "image/png"


async def parse_pdf(
    data: bytes,
    upload_image=None,
    image_threshold: int = IMAGE_THRESHOLD,
) -> str:
    import asyncio

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    page_count = len(reader.pages)

    # ---------- 1. 文本：按页取出（CPU 密集，放线程池避免阻塞事件循环） ----------
    def _extract_texts() -> list[str]:
        texts: list[str] = []
        for page in reader.pages:
            try:
                texts.append((page.extract_text() or "").strip())
            except Exception:  # noqa: BLE001
                texts.append("")
        return texts

    page_texts: list[str] = await asyncio.to_thread(_extract_texts)

    # ---------- 2. 图片（可选）：提取 → 过滤小图 → 上传 ----------
    page_image_urls: dict[int, list[str]] = {}
    if upload_image is not None:
        try:

            def _extract_images() -> dict[int, list[tuple[bytes, int, int]]]:
                result: dict[int, list[tuple[bytes, int, int]]] = {}
                for page_idx, page in enumerate(reader.pages):
                    try:
                        for image in page.images:
                            pil = image.image
                            width, height = pil.size
                            result.setdefault(page_idx, []).append((image.data, width, height))
                    except Exception:  # noqa: BLE001
                        continue
                return result

            images = await asyncio.to_thread(_extract_images)
            for page_idx, items in images.items():
                urls: list[str] = []
                for img_idx, (img_bytes, width, height) in enumerate(items):
                    if 0 < width < image_threshold or 0 < height < image_threshold:
                        continue
                    if not img_bytes:
                        continue
                    content_type = _sniff_image_content_type(img_bytes)
                    ext = "jpg" if content_type == "image/jpeg" else "png"
                    file_name = f"pdf_img_p{page_idx + 1}_{img_idx}.{ext}"
                    try:
                        url = await upload_image(img_bytes, file_name, content_type)
                        urls.append(url)
                    except Exception as e:  # noqa: BLE001
                        logger.warning("PDF 图片上传失败: page=%d, err=%s", page_idx + 1, e)
                if urls:
                    page_image_urls[page_idx] = urls
        except Exception as e:  # noqa: BLE001
            logger.warning("PDF 图片提取失败，继续仅文本: %s", e)

    # ---------- 3. 按页拼装 ----------
    parts: list[str] = []
    if page_count > 0:
        for idx, text in enumerate(page_texts):
            if text:
                parts.append(text)
            for url in page_image_urls.get(idx, []):
                parts.append(f"![]({url})")
            if text or page_image_urls.get(idx):
                parts.append("")
    else:
        fallback = "\n".join(t for t in page_texts if t).strip()
        if fallback:
            parts.append(fallback)
        for urls in page_image_urls.values():
            for url in urls:
                parts.append(f"![]({url})")

    markdown = clean_markdown("\n\n".join(parts))

    # ---------- 4. 表格（尽力而为，正文无表格时追加） ----------
    if "| ---" not in markdown:
        try:

            def _extract_tables() -> list[list[list[str]]]:
                import pdfplumber

                tables: list[list[list[str]]] = []
                with pdfplumber.open(io.BytesIO(data)) as pdf:
                    for page in pdf.pages:
                        for table in page.extract_tables() or []:
                            rows = [
                                ["" if cell is None else str(cell).strip() for cell in row]
                                for row in table
                            ]
                            if rows:
                                tables.append(rows)
                return tables

            tables = await asyncio.to_thread(_extract_tables)
            if tables:
                table_parts: list[str] = []
                for table_idx, rows in enumerate(tables):
                    table_parts.append(f"### 表格 {table_idx + 1}\n\n{to_markdown_table(rows)}")
                markdown = clean_markdown(
                    f"{markdown}\n\n## 检测到的表格\n\n" + "\n".join(table_parts)
                )
        except Exception:  # noqa: BLE001
            pass

    return markdown
