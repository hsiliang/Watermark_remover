import pytest
from docx import Document
from wmr.suspect import analyze, sentences, slen


def _doc(tmp_path, items):
    d = Document()
    for kind, text in items:
        if kind == "h1":
            d.add_heading(text, 1)
        elif kind == "h2":
            d.add_heading(text, 2)
        elif kind == "boldlabel":
            p = d.add_paragraph()
            p.add_run("重點").bold = True
            p.add_run("：" + text)
        else:
            d.add_paragraph(text)
    out = str(tmp_path / "s.docx")
    d.save(out)
    return out


def cats(r):
    return {f.cat for f in r.flags}


def test_phrase_categories(tmp_path):
    r = analyze(_doc(tmp_path, [
        ("h1", "第一章"),
        ("p", "這項技術不僅提升效率，而且賦能產業。研究表明這很有效。"),
        ("p", "值得注意的是，使用者信息很重要——非常重要。"),
    ]))
    assert {"neg_parallel", "bigword", "vague_attr", "filler", "region", "dash"} <= cats(r)


def test_vague_attribution_not_flagged_when_cited(tmp_path):
    r = analyze(_doc(tmp_path, [("p", "研究表明此效果顯著（王小明，2021）。")]))
    assert "vague_attr" not in cats(r)


def test_references_section_is_skipped(tmp_path):
    r = analyze(_doc(tmp_path, [
        ("h1", "正文"), ("p", "此處正常。"),
        ("h1", "參考文獻"), ("p", "賦能 助力 打造 研究表明"),
        ("h1", "附錄"), ("p", "附錄內容 賦能"),
    ]))
    paras = [p.text for p in r.paras]
    assert "賦能 助力 打造 研究表明" not in paras and "附錄內容 賦能" in paras


def test_heading_path_and_search_key(tmp_path):
    r = analyze(_doc(tmp_path, [("h1", "第二章"), ("h2", "第一節"), ("p", "此外，這是賦能的例子。")]))
    f = [x for x in r.flags if x.cat == "bigword"][0]
    assert f.heading == "第二章 > 第一節"
    assert "賦能" in f.key and len(f.key) <= 30
    assert f.span and r.paras[f.para - 1].text[f.span[0]:f.span[1]] == "賦能"


def test_connector_density_and_summary_tail(tmp_path):
    r = analyze(_doc(tmp_path, [("p", "此外，甲很好。然而，乙不好。因此，丙需要。同時，丁也是。綜上所述，戊成立。")]))
    assert "connector" in cats(r) and "summary_tail" in cats(r)


def test_uniform_sentences_relative(tmp_path):
    uniform = "這是一個長度相當的句子啊。" * 6          # 每句 12 字
    varied = "短句。這是一個比較長而且內容更豐富的句子，用來製造明顯的長短差異。再來一個。這又是另外一個稍微長一點的句子啊。"
    r = analyze(_doc(tmp_path, [("p", uniform), ("p", varied), ("p", varied + "好。"), ("p", varied + "對。"), ("p", varied + "是。")]))
    flagged = {f.para for f in r.flags if f.cat == "uniform_sent"}
    assert 1 in flagged and 2 not in flagged


def test_bold_label_and_simplified(tmp_path):
    r = analyze(_doc(tmp_path, [("boldlabel", "這是一段說明文字內容，長度足夠。"), ("p", "這个研究关于发展与对策，繁體為主的文件裡混入簡體字，而且還有許多繁體字在這裡。")]))
    assert "bold_label" in cats(r) and "simplified" in cats(r)


def test_summary_counts_and_hotspots(tmp_path):
    r = analyze(_doc(tmp_path, [("p", "值得注意的是，賦能與助力與打造。"), ("p", "普通的一句話。")]))
    assert r.by_cat["bigword"] == 3
    assert r.hotspots[0]["para"] == 1 and r.hotspots[0]["score"] > 0


def test_clean_text_has_no_flags(tmp_path):
    r = analyze(_doc(tmp_path, [("h1", "緒論"), ("p", "今天天氣很好，我們去公園散步。路上遇到一位老朋友，聊了很久。")]))
    assert r.flags == []


def test_sentence_helpers():
    assert sentences("甲。乙！丙？") == ["甲。", "乙！", "丙？"]
    assert slen("a b 中") == 3


def test_false_positives_from_real_thesis(tmp_path):
    r = analyze(_doc(tmp_path, [
        ("p", "影響因素不只一個，而且不同企業的情況各異。"),   # 只是數量，不是否定排比修辭
        ("p", "本研究認為技術與組織條件彼此相關。"),          # 作者自己的主張，不是模糊引用
        ("p", "資料驅動的決策支援系統已被廣泛採用。"),        # 技術用語，不是空洞大詞
    ]))
    assert cats(r) == set(), [(f.cat, f.snippet) for f in r.flags]


def test_true_positives_still_detected(tmp_path):
    r = analyze(_doc(tmp_path, [
        ("p", "這不僅是技術問題，而且是組織問題。"),
        ("p", "相關研究表明這個方法有效。"),
        ("p", "專案以創新驅動發展，持續賦能產業。"),
    ]))
    assert {"neg_parallel", "vague_attr", "bigword"} <= cats(r)


def test_annotation_avoids_colors_already_in_document(tmp_path):
    from docx.enum.text import WD_COLOR_INDEX
    d = Document()
    p = d.add_paragraph()
    p.add_run("原有的待填欄位").font.highlight_color = WD_COLOR_INDEX.YELLOW
    d.add_paragraph("這是賦能的例子。")           # bigword → 預設黃色
    src = str(tmp_path / "h.docx")
    d.save(src)
    r = analyze(src)
    f = [x for x in r.flags if x.cat == "bigword"][0]
    assert f.color != "yellow"
    assert "yellow" in str(r.metrics["原文件既有螢光筆顏色"])
    # 各類別的顏色對映保持一致（同一類別同一顏色）
    assert len({x.color for x in r.flags if x.cat == "bigword"}) == 1


def test_standalone_dash_placeholder_is_not_flagged(tmp_path):
    r = analyze(_doc(tmp_path, [("p", "—"), ("p", "結果很好——而且穩定。")]))
    flagged = [f.para for f in r.flags if f.cat == "dash"]
    assert flagged == [2]


def test_heading_structure_always_three(tmp_path):
    items = []
    for i in range(5):
        items.append(("h1", "第%d節" % (i + 1)))
        for j in range(3):
            items.append(("h2", "小節%d-%d" % (i, j)))
            items.append(("p", "內容在此，說明這個小節的要點。"))
    r = analyze(_doc(tmp_path, items))
    assert "heading_rule3" in cats(r)


def test_heading_structure_varied_not_flagged(tmp_path):
    items = []
    for i, n in enumerate([2, 3, 4, 1, 5]):
        items.append(("h1", "第%d節" % (i + 1)))
        for j in range(n):
            items.append(("h2", "小節%d-%d" % (i, j)))
            items.append(("p", "內容在此。"))
    assert "heading_rule3" not in cats(analyze(_doc(tmp_path, items)))


def test_restate_and_opener_phrases(tmp_path):
    r = analyze(_doc(tmp_path, [("p", "這個問題很複雜。換言之，沒有簡單的答案。這也是為什麼我們需要更多討論。隨著科技的快速發展，情況改變。")]))
    assert {"restate", "opener"} <= cats(r)


def test_first_second_third_with_zaici(tmp_path):
    r = analyze(_doc(tmp_path, [("p", "首先，政府應該增加預算。其次，企業應該投入資源。再次，社區應該參與。")]))
    assert "triad" in cats(r)


def test_triple_list_density(tmp_path):
    r = analyze(_doc(tmp_path, [("p", "我們需要資金、人力與技術。同時要整合醫療、照護及預防。還要兼顧效率、公平與品質。")]))
    assert "triple_list" in cats(r)
    r2 = analyze(_doc(tmp_path, [("p", "我們需要資金、人力與技術。今天的天氣不錯，所以出門散步。")]))
    assert "triple_list" not in cats(r2)


def test_generic_paragraph_vs_specific(tmp_path):
    generic = "高齡化帶來許多挑戰，政府與社會都需要共同努力，透過各種方式改善現況，並且讓每一個人都能夠得到適當的照顧與支持，這是一件非常重要的事情。"
    specific = "依衛福部二○二三年統計，失能人口達八十萬人，其中Alzheimer型占四成（Chen et al., 2021），《長期照顧服務法》第十條規定了服務範圍與資格。"
    items = [("p", generic), ("p", specific), ("p", specific + "再補充一點，日間照顧中心共有三千二百處。"),
             ("p", specific.replace("八十萬", "九十萬")), ("p", specific + "又一項資料。")]
    r = analyze(_doc(tmp_path, items))
    flagged = {f.para for f in r.flags if f.cat == "generic"}
    assert 1 in flagged and not ({2, 3, 4, 5} & flagged)


def test_generic_is_document_level_when_everything_is_generic(tmp_path):
    generic = "高齡化帶來許多挑戰，政府與社會都需要共同努力，透過各種方式改善現況，並且讓每一個人都能夠得到適當的照顧與支持，這是一件非常重要的事情。"
    r = analyze(_doc(tmp_path, [("p", generic)] * 6))
    assert "generic" not in cats(r)                       # 全部都空泛 → 不逐段標示（無法區分）
    assert r.metrics["具體資訊稀少的段落比例"] >= 0.9    # 改以文件層級指標呈現
