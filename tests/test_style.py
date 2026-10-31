import zipfile
from docx import Document
from wmr.rebuild import rebuild
from wmr.scan import scan
from wmr.style import restyle_text, scan_text, Ctx


def test_markdown_remnants_and_emoji():
    assert restyle_text("這是 **重點** 與 `code` 😀") == "這是 重點 與 code"
    assert restyle_text("## 標題") == "標題"


def test_dashes_and_lexicon():
    assert "——" not in restyle_text("結果——很好")
    assert restyle_text("值得注意的是，這很重要") == "這很重要"  # 純套話直接刪除
    assert restyle_text("A。此外B。此外C。此外D") == "A。另外B。同時C。再者D"  # 輪替，避免新的單調
    assert restyle_text("We delve into it") == "We look into it"


def test_cjk_punctuation_normalized():
    assert restyle_text("你好,世界:測試") == "你好，世界：測試"
    assert restyle_text("a, b (c)") == "a, b (c)"  # 純英文不動


def test_scan_text_reports_hits():
    hits = scan_text("此外，綜上所述——**重點** 😀")
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


def test_cross_run_phrase_replacement():
    """Word 常把 '綜上所述' 切在兩個 run；段落層級替換必須處理。"""
    from lxml import etree
    from wmr.style import Ctx, restyle_paragraph, W
    xml = ('<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:r><w:t>前文綜上</w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>所述，結論</w:t></w:r></w:p>')
    p = etree.fromstring(xml)
    restyle_paragraph(p, Ctx())
    assert "".join(t.text for t in p.iter(W + "t")) == "前文結論"
    assert len(list(p.iter(W + "r"))) == 2 and p.find(".//" + W + "b") is not None  # 格式保留


def test_artefacts_and_region_terms():
    assert restyle_text("結果顯示【4†source】成立") == "結果顯示成立"
    assert restyle_text("使用者信息與軟件", Ctx(traditional=True)) == "使用者資訊與軟體"
    assert restyle_text("用户信息", Ctx(traditional=False)) == "用户信息"  # 簡體文件不動


def test_optional_groups():
    assert restyle_text("“你好”", Ctx()) == "“你好”"
    assert restyle_text("“你好”", Ctx(groups={"quotes"})) == "「你好」"
    assert restyle_text("此外", Ctx(groups={"filler"})) == "此外"


def test_dash_in_its_own_run_uses_paragraph_context():
    from lxml import etree
    from wmr.style import Ctx, restyle_paragraph, W
    xml = ('<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:r><w:t>結果很好</w:t></w:r><w:r><w:t>——</w:t></w:r><w:r><w:t>而且穩定</w:t></w:r></w:p>')
    p = etree.fromstring(xml)
    restyle_paragraph(p, Ctx())
    assert "——" not in "".join(t.text for t in p.iter(W + "t"))


def test_change_log_records_what_changed():
    ctx = Ctx()
    restyle_text("值得注意的是，這很重要。此外，信息很多——真的。", ctx)
    summary = ctx.summary()
    assert summary["filler"] >= 1 and summary["lexicon"] >= 1 and summary["dash"] >= 1
    assert any("值得注意的是" in b for b, a in ctx.log["filler"])


def test_style_does_not_touch_reference_section(tmp_path):
    from docx import Document
    d = Document()
    d.add_heading("緒論", 1)
    d.add_paragraph("此外，使用者信息很重要。")
    d.add_heading("參考文獻", 1)
    d.add_paragraph("Lee, K. (2020). Data — a study. 此外 信息.")
    d.add_heading("附錄", 1)
    d.add_paragraph("此外，附錄內容。")
    src, out = str(tmp_path / "r.docx"), str(tmp_path / "o.docx")
    d.save(src)
    rebuild(src, out, style=True)
    t = [p.text for p in Document(out).paragraphs]
    assert t[1] == "另外，使用者資訊很重要。"
    assert t[3] == "Lee, K. (2020). Data — a study. 此外 信息."     # 參考文獻原封不動
    assert "此外" not in t[5] and t[5].endswith("，附錄內容。")        # 附錄之後恢復處理（輪替用詞，不固定是哪個）


def test_punct_does_not_break_formulas_or_pair_parens():
    assert restyle_text("取 min(X, Y))，數值愈低") == "取 min(X, Y))，數值愈低"   # 緊鄰全形標點不算中文語境
    assert restyle_text("結果(如圖)很好") == "結果(如圖)很好"                  # 括號不單獨轉換，避免不成對
    assert restyle_text("你好,世界:測試") == "你好，世界：測試"                 # 漢字旁的逗號／冒號仍轉換


def test_region_rule_does_not_break_taiwan_usage():
    # 「網絡」在台灣指抽象網絡（社區網絡、人際網絡），不能改成「網路」(Internet)
    assert restyle_text("建立社區網絡與支持網絡", Ctx(traditional=True)) == "建立社區網絡與支持網絡"
    # 有歧義的詞不自動替換
    assert restyle_text("調用警力，激活免疫細胞", Ctx(traditional=True)) == "調用警力，激活免疫細胞"
    assert restyle_text("使用者信息", Ctx(traditional=True)) == "使用者資訊"


def test_lexicon_replacement_keeps_part_of_speech():
    assert restyle_text("補助是推動轉型的助力") == "補助是推動轉型的幫助"     # 名詞 → 名詞/動詞皆可，不可變成「…的協助」
    assert restyle_text("科技助力產業升級") == "科技幫助產業升級"
