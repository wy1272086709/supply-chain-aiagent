"""DOCX → Markdown — mammoth(Python) + markdownify，对应 docx.parser.ts（mammoth+turndown+GFM）。"""

from __future__ import annotations

import io

import mammoth
from markdownify import markdownify as md_convert

from app.modules.documents.parsers.markdown_util import clean_markdown

# 英文 + 中文 Word 内置样式名，避免中文版 Word 标题丢失层级
STYLE_MAP = """
p[style-name='Title'] => h1:fresh
p[style-name='Subtitle'] => h2:fresh
p[style-name='Heading 1'] => h1:fresh
p[style-name='Heading 2'] => h2:fresh
p[style-name='Heading 3'] => h3:fresh
p[style-name='Heading 4'] => h4:fresh
p[style-name='标题 1'] => h1:fresh
p[style-name='标题 2'] => h2:fresh
p[style-name='标题 3'] => h3:fresh
p[style-name='标题 4'] => h4:fresh
"""


def parse_docx(data: bytes) -> str:
    with io.BytesIO(data) as fobj:
        result = mammoth.convert_to_html(fobj, style_map=STYLE_MAP)
    html = result.value
    markdown = md_convert(
        html,
        heading_style="ATX",  # # 标题
        bullets="-",  # - 列表
        code_language="",
    )
    return clean_markdown(markdown)
