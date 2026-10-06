from app.modules.documents.service import build_content_summary, count_words


def test_empty():
    assert count_words("") == 0
    assert count_words("   ") == 0


def test_pure_cjk():
    assert count_words("差旅报销管理办法") == 8


def test_pure_latin():
    assert count_words("hello world foo") == 3


def test_mixed():
    # 4 个汉字 + 1 个英文词
    assert count_words("报销 invoice 流程") == 5


def test_summary_truncation():
    assert build_content_summary("a" * 300) == "a" * 200 + "..."
    assert build_content_summary("  你好  世界 ") == "你好 世界"
