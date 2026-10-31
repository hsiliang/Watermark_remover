from __future__ import annotations

from docx import Document

from .rebuild import clean_text


def _sig(path):
    """逐字元的格式簽章，不受 run 切法影響（Word 重存會重新切 run）。"""
    d = Document(path)
    paras = []
    for p in d.paragraphs:
        chars = []
        for r in p.runs:
            fmt = (bool(r.bold), bool(r.italic), r.font.size.pt if r.font.size else None,
                   str(r.font.color.rgb) if r.font.color is not None and r.font.color.type is not None else None)
            chars.extend((ch, fmt) for ch in clean_text(r.text))
        paras.append((p.style.name, tuple(chars)))
    return paras, len(d.tables), len(d.inline_shapes)


def compare(a: str, b: str):
    """比對原檔與輸出檔的段落、樣式、run 格式、表格數、圖片數。回傳差異清單。"""
    pa, ta, ia = _sig(a)
    pb, tb, ib = _sig(b)
    # Word 會在結尾表格後補空段落；忽略兩邊尾端的空段落
    def trim(ps):
        while ps and not ps[-1][1]:
            ps = ps[:-1]
        return ps
    pa, pb = trim(pa), trim(pb)
    diffs = []
    if (ta, ia) != (tb, ib):
        diffs.append("表格/圖片數不同：%s vs %s" % ((ta, ia), (tb, ib)))
    # 隱藏文字被移除屬預期：忽略原檔空段落造成的長度差，逐段比對
    if len(pa) != len(pb):
        diffs.append("段落數不同：%d vs %d" % (len(pa), len(pb)))
    bad = [i + 1 for i, (x, y) in enumerate(zip(pa, pb)) if x != y]
    if bad:
        diffs.append("%d 段的文字／樣式／字元格式不同，前幾段：%s" % (len(bad), bad[:10]))
    return diffs
