import re


def test_parse_cited_indexes():
    from app.modules.ai.types import parse_cited_indexes

    assert parse_cited_indexes("见[1]和[3]") == [1, 3]
    assert parse_cited_indexes("[2][2]") == [2]
    assert parse_cited_indexes("无引用") == []
    assert parse_cited_indexes("[99]") == [99]


def test_cited_sources_fallback_all():
    from app.modules.ai.chat_service import _to_cited_sources

    hits = [{"documentId": str(i), "documentTitle": f"d{i}", "heading": None,
             "content": "c" * 250, "score": 0.5} for i in range(3)]
    # 未标注 → 回退全部
    sources = _to_cited_sources("没有引用", hits)
    assert [s["index"] for s in sources] == [1, 2, 3]
    # 标注 2 → 只返回 2
    sources = _to_cited_sources("依据[2]", hits)
    assert [s["index"] for s in sources] == [2]
    # excerpt 截断到 200 字符 + "..."（TS slice(0,200)+'...' 同义）
    assert sources[0]["excerpt"].endswith("...")
    assert len(sources[0]["excerpt"]) == 203
