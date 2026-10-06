from app.modules.documents.parsers.markdown_util import (
    clean_markdown,
    decode_upload_filename,
    escape_table_cell,
    get_extension,
    title_from_filename,
    to_markdown_table,
)


def test_clean_markdown():
    assert clean_markdown("a\r\nb") == "a\nb"
    assert clean_markdown("a\n\n\n\n\n\nb") == "a\n\n\nb"
    assert clean_markdown("  x  ") == "x"


def test_escape_table_cell():
    assert escape_table_cell("a|b") == "a\\|b"
    assert escape_table_cell("a\nb") == "a b"


def test_to_markdown_table():
    table = to_markdown_table([["h1", "h2"], ["a", "b"]])
    assert table == "| h1 | h2 |\n| --- | --- |\n| a | b |\n\n"
    # 参差不齐的行补空
    table = to_markdown_table([["h1", "h2", "h3"], ["a"]])
    assert table.rstrip("\n").splitlines()[2] == "| a |  |  |"


def test_get_extension():
    assert get_extension("a.B.PDF") == "pdf"
    assert get_extension("noext") == ""
    assert get_extension(None) == ""


def test_title_from_filename():
    assert title_from_filename("李先生_28岁.pdf") == "李先生_28岁"
    assert title_from_filename(".hidden") == ".hidden"
    assert title_from_filename("") == "未命名文档"


def test_decode_upload_filename():
    # latin1 误解码的中文名可还原：先构造「UTF-8 字节被当 latin1 解读」的串
    mangled = "系统文档".encode("utf-8").decode("latin1")
    assert decode_upload_filename(mangled) == "系统文档"
    # 正常 ASCII 原样
    assert decode_upload_filename("ok.txt") == "ok.txt"
