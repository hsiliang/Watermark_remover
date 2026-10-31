import zipfile
from docx import Document
from wmr.rebuild import rebuild
from wmr.scan import scan
from wmr.style import restyle_text, scan_text


def test_markdown_remnants_and_emoji():
    assert restyle_text("這是 **重點** 與 `code` 😀") == "這是 重點 與 code"
    assert restyle_text("## 標題") == "標題"


def test_dashes_and_lexicon():
    assert "——" not in restyle_text("結果——很好")
    assert restyle_text("值得注意的是，這很重要") == "要留意的是，這很重要"
    assert restyle_text("We delve into it") == "We look into it"


def test_cjk_punctuation_normalized():
    assert restyle_text("你好,世界:測試(完成)") == "你好，世界：測試（完成）"
    assert restyle_text("a, b (c)") == "a, b (c)"  # 純英文不動


def test_scan_text_reports_hits():
    hits = scan_text("綜上所述——**重點** 😀")
    assert {"lexicon", "dash", "markdown", "emoji"} <= {h[0] for h in hits}


def test_clean_text_unchanged():
    assert restyle_text("今天天氣很好。") == "今天天氣很好。"


def test_rebuild_style_is_optional(dirty_docx, tmp_path):
    d = Document(dirty_docx)
    d.add_paragraph("綜上所述——**重點**")
    src = str(tmp_path / "s.docx")
    d.save(src)
    off, on = str(tmp_path / "off.docx"), str(tmp_path / "on.docx")
    rebuild(src, off)
    rebuild(src, on, style=True)
    assert "綜上所述" in "".join(p.text for p in Document(off).paragraphs)
    t_on = "".join(p.text for p in Document(on).paragraphs)
    assert "綜上所述" not in t_on and "**" not in t_on and "——" not in t_on
    assert any(f.layer == "style" for f in scan(src, style=True))


def test_run_edge_spaces_preserved():
    assert restyle_text("Hello ") == "Hello "
    assert restyle_text(" world") == " world"
    assert restyle_text("你好, ") == "你好， "
