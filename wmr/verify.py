from __future__ import annotations

from docx import Document

from .rebuild import clean_text


def _sig(path):
    d = Document(path)
    paras = []
    for p in d.paragraphs:
        runs = [(clean_text(r.text), bool(r.bold), bool(r.italic), r.font.size.pt if r.font.size else None,
                 str(r.font.color.rgb) if r.font.color is not None and r.font.color.type is not None else None)
                for r in p.runs if r.text]
        paras.append((p.style.name, runs))
    return paras, len(d.tables), len(d.inline_shapes)


def compare(a: str, b: str):
    """比對原檔與輸出檔的段落、樣式、run 格式、表格數、圖片數。回傳差異清單。"""
    pa, ta, ia = _sig(a)
    pb, tb, ib = _sig(b)
    diffs = []
    if (ta, ia) != (tb, ib):
        diffs.append("表格/圖片數不同：%s vs %s" % ((ta, ia), (tb, ib)))
    # 隱藏文字被移除屬預期：忽略原檔空段落造成的長度差，逐段比對
    if len(pa) != len(pb):
        diffs.append("段落數不同：%d vs %d" % (len(pa), len(pb)))
    for i, (x, y) in enumerate(zip(pa, pb)):
        if x != y:
            diffs.append("第 %d 段不同" % (i + 1))
    return diffs
