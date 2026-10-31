"""產生「標註副本」：在可疑處加上螢光筆顏色，方便在 Word 裡直接看到並修改。

僅供人工檢閱，不應直接繳交（有螢光標記）。顏色對照見報告。
"""
from __future__ import annotations

import copy
import zipfile
from typing import List, Tuple

from lxml import etree

from .common import NS_W
from .rebuild import _ser
from .suspect import Review

W = "{%s}" % NS_W
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
# rPr 子元素的 schema 順序；highlight 之後的元素
_AFTER_HIGHLIGHT = ("u", "effect", "bdr", "shd", "fitText", "vertAlign", "rtl", "cs", "em", "lang", "eastAsianLayout", "specVanish", "oMath")


def _set_highlight(run, color: str) -> None:
    rpr = run.find(W + "rPr")
    if rpr is None:
        rpr = etree.Element(W + "rPr")
        run.insert(0, rpr)
    old = rpr.find(W + "highlight")
    if old is not None:
        rpr.remove(old)
    h = etree.Element(W + "highlight")
    h.set(W + "val", color)
    for i, child in enumerate(rpr):
        if etree.QName(child).localname in _AFTER_HIGHLIGHT:
            rpr.insert(i, h)
            return
    rpr.append(h)


def _segments(p) -> List[Tuple[object, int, int]]:
    """與 suspect.para_text 相同的偏移：w:t 取其文字，w:tab 算 1 個字元。"""
    segs, pos = [], 0
    for el in p.iter():
        if el.tag == W + "t" and el.text:
            segs.append((el, pos, pos + len(el.text)))
            pos += len(el.text)
        elif el.tag == W + "tab":
            pos += 1
    return segs


def _highlight_span(p, a: int, b: int, color: str) -> None:
    for t, s, e in _segments(p):
        if e <= a or s >= b:
            continue
        run = t.getparent()
        la, lb = max(a, s) - s, min(b, e) - s
        simple = run.tag == W + "r" and [c for c in run if c.tag not in (W + "rPr",)] == [t]
        if not simple or (la == 0 and lb == len(t.text)):
            _set_highlight(run, color)
            continue
        text = t.text
        parts = [(text[:la], None), (text[la:lb], color), (text[lb:], None)]
        anchor = run
        for chunk, col in parts:
            if not chunk:
                continue
            r2 = copy.deepcopy(run)
            t2 = r2.find(W + "t")
            t2.text = chunk
            t2.set(XML_SPACE, "preserve")
            if col:
                _set_highlight(r2, col)
            anchor.addnext(r2)
            anchor = r2
        run.getparent().remove(run)


def annotate(src: str, dst: str, rv: Review) -> int:
    by_para = {p.idx: p for p in rv.paras}
    n = 0
    # 先段落層級（整段淡標），再字詞層級（覆蓋）
    for f in rv.flags:
        if f.span is None:
            for r in by_para[f.para].element.iter(W + "r"):
                _set_highlight(r, f.color)
            n += 1
    for f in rv.flags:
        if f.span is not None:
            _highlight_span(by_para[f.para].element, f.span[0], f.span[1], f.color)
            n += 1
    root = rv.paras[0].element.getroottree().getroot() if rv.paras else None
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zo:
        for info in zin.infolist():
            data = _ser(root) if (info.filename == "word/document.xml" and root is not None) else zin.read(info.filename)
            zi = zipfile.ZipInfo(info.filename, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.create_system = 0
            zi.external_attr = 0
            zo.writestr(zi, data)
    return n
