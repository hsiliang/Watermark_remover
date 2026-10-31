import json
import zipfile

import pytest
from docx import Document
from docx.shared import Pt

from wmr.edits import apply_edits, export_edits


def _src(tmp_path):
    d = Document()
    d.add_heading("第一章", 1)
    p = d.add_paragraph()
    r = p.add_run("這項技術不僅提升效率，而且賦能產業。研究表明這很有效。")
    r.font.size = Pt(13)
    r.bold = False
    p2 = d.add_paragraph()
    p2.add_run("重點").bold = True
    p2.add_run("：這是一段普通說明文字。")
    d.add_paragraph("乾淨的段落，沒有任何問題，只是普通地描述今天的天氣與心情。")
    path = str(tmp_path / "e.docx")
    d.save(path)
    return path


def test_export_lists_flagged_paragraphs_with_guidance(tmp_path):
    src = _src(tmp_path)
    out = str(tmp_path / "edits.json")
    n = export_edits(src, out)
    data = json.load(open(out, encoding="utf-8"))
    assert n == len(data["edits"]) >= 1
    e = data["edits"][0]
    assert e["id"] and e["text"] and e["flags"] and e["new_text"] is None
    assert "instructions" in data and "保留" in data["instructions"]
    assert all("乾淨的段落" not in x["text"] for x in data["edits"])   # 沒問題的段落不輸出


def test_apply_replaces_text_keeps_format(tmp_path):
    src = _src(tmp_path)
    ed = str(tmp_path / "edits.json")
    export_edits(src, ed)
    data = json.load(open(ed, encoding="utf-8"))
    first = data["edits"][0]
    first["new_text"] = "這項技術提升了效率，也帶動了整個產業。"
    json.dump(data, open(ed, "w", encoding="utf-8"), ensure_ascii=False)
    out = str(tmp_path / "out.docx")
    rep = apply_edits(src, ed, out)
    assert rep["applied"] == 1 and not rep["skipped"]
    paras = Document(out).paragraphs
    target = [p for p in paras if p.text.startswith("這項技術提升了")][0]
    assert target.runs[0].font.size.pt == 13           # 格式保留
    assert "賦能" not in "".join(p.text for p in paras)
    assert "乾淨的段落" in "".join(p.text for p in paras)  # 其他段落不動


def test_apply_skips_stale_edits(tmp_path):
    src = _src(tmp_path)
    ed = str(tmp_path / "edits.json")
    export_edits(src, ed)
    data = json.load(open(ed, encoding="utf-8"))
    data["edits"][0]["text"] = "這不是原文"      # 模擬原稿已被改過
    data["edits"][0]["new_text"] = "新內容"
    json.dump(data, open(ed, "w", encoding="utf-8"), ensure_ascii=False)
    rep = apply_edits(src, ed, str(tmp_path / "o.docx"))
    assert rep["applied"] == 0 and rep["skipped"] and "不符" in rep["skipped"][0][1]


def test_bold_markers_create_bold_runs(tmp_path):
    src = _src(tmp_path)
    ed = str(tmp_path / "edits.json")
    export_edits(src, ed)
    data = json.load(open(ed, encoding="utf-8"))
    lab = [e for e in data["edits"] if e["text"].startswith("重點")][0]
    lab["new_text"] = "**重點**：改寫後的說明。"
    json.dump(data, open(ed, "w", encoding="utf-8"), ensure_ascii=False)
    out = str(tmp_path / "o.docx")
    apply_edits(src, ed, out)
    p = [p for p in Document(out).paragraphs if p.text.startswith("重點")][0]
    assert p.text == "重點：改寫後的說明。" and p.runs[0].bold and not p.runs[-1].bold


def test_paragraphs_with_images_are_not_editable(tmp_path):
    import hashlib
    d = Document()
    p = d.add_paragraph("賦能與助力與打造。")
    png = tmp_path / "a.png"
    import struct, zlib
    def ch(t, b):
        c = struct.pack(">I", len(b)) + t + b
        return c + struct.pack(">I", zlib.crc32(t + b) & 0xFFFFFFFF)
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) + ch(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + ch(b"IEND", b""))
    p.add_run().add_picture(str(png))
    src = str(tmp_path / "i.docx")
    d.save(src)
    ed = str(tmp_path / "e.json")
    export_edits(src, ed)
    e = json.load(open(ed, encoding="utf-8"))["edits"][0]
    assert e["editable"] is False and "圖" in e["why_not"]
