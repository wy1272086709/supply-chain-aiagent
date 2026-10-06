import hashlib

from app.modules.pipeline import chunking


def test_chunk_id_formula(monkeypatch):
    chunks = chunking.chunk(
        content="# 标题\n\n" + "正文内容。" * 50,
        document_id="123456",
        document_title="文档",
    )
    assert chunks
    assert chunks[0]["chunkId"] == hashlib.sha256(b"123456:0").hexdigest()[:64]
    assert all(c["totalChunks"] == len(chunks) for c in chunks)


def test_heading_inherit_and_prefix():
    chunks = chunking.chunk(
        content="# 差旅制度\n\n" + "规则一。" * 200 + "\n\n## 细则\n\n" + "规则二。" * 200,
        document_id="doc1",
        document_title="差旅",
    )
    assert chunks[0]["heading"] == "差旅制度"
    # 后续块若不含标题行，内容应带标题前缀
    prefixed = [c for c in chunks if c["heading"] == "差旅制度" and not c["content"].startswith("# ")]
    assert all(c["content"].startswith("差旅制度\n\n") for c in prefixed)


def test_empty_content():
    assert chunking.chunk(content="", document_id="x", document_title="t") == []
    assert chunking.chunk(content="   \n  ", document_id="x", document_title="t") == []
