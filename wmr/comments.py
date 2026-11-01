"""待修版：在需要人工處理的段落加上 Word 註解，讓你在 Word 內用「審閱 → 下一則」逐一處理。

- 只標「需人工」（不能自動修）的類型，且信心 >= min_conf；每段合併成一則註解。
- 註解作者固定為 TOOL_COMMENT_AUTHOR；之後把待修版交給 clean 時，rebuild 會把這些註解整批移除。
- 待修版是工作副本，**不可直接繳交**。
"""
from __future__ import annotations

import datetime as _dt
import re
import zipfile
from typing import Callable, Dict, List, Optional

from lxml import etree

from .ai import mismatches
from .common import NS_CT, NS_REL, NS_W, TOOL_COMMENT_AUTHOR
from .rebuild import DECL, _ser
from .suspect import CATEGORIES, CONF_WEIGHT, analyze

W = "{%s}" % NS_W
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
REL_COMMENTS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
CT_COMMENTS = "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"


def _lines(flags) -> List[str]:
    out = ["【檢測提示】這一段有 %d 類可疑訊號（可疑度，不代表一定是 AI）：" % len({f.cat for f in flags})]
    seen = set()
    for f in sorted(flags, key=lambda x: -CONF_WEIGHT[x.conf]):
        if f.cat in seen:
            continue
        seen.add(f.cat)
        snip = (f.snippet or "").replace("\n", " ")
        if len(snip) > 24:
            snip = snip[:24] + "…"
        out.append("・[%s信心] %s%s" % (f.conf, f.label, "：「%s」" % snip if snip and f.span else ""))
        out.append("　建議：" + CATEGORIES[f.cat][4])
    return out


def _suggestion_lines(original: str, text: str) -> List[str]:
    out = ["【Claude 改寫建議】僅供參考，不會自動寫入文件；採用前請逐句核對："]
    gone, added = mismatches(original, text)
    if gone or added:
        out.append("⚠ 數字或英文詞有變動，請核對。%s%s" % (
            "不見：%s。" % "、".join(gone) if gone else "", "多出：%s。" % "、".join(added) if added else ""))
    out.append(text)
    return out


def _comment_el(cid: int, lines: List[str], date: str):
    c = etree.Element(W + "comment")
    c.set(W + "id", str(cid))
    c.set(W + "author", TOOL_COMMENT_AUTHOR)
    c.set(W + "date", date)
    c.set(W + "initials", TOOL_COMMENT_AUTHOR[:1])
    for ln in lines:
        p = etree.SubElement(c, W + "p")
        r = etree.SubElement(p, W + "r")
        t = etree.SubElement(r, W + "t")
        t.text = ln
        t.set(XML_SPACE, "preserve")
    return c


def add_comments(src: str, dst: str, min_conf: str = "中", skip_headings=None,
                 suggest: Optional[Callable[[List[Dict]], Dict]] = None) -> Dict:
    """回傳 {"comments": 註解數, "paragraphs": 段數}；有 suggest 時另含 "ai"（見 ai.suggest）。"""
    rv = analyze(src) if skip_headings is None else analyze(src, tuple(skip_headings))
    order = {"低": 0, "中": 1, "高": 2}
    by_para: Dict[int, List] = {}
    for f in rv.flags:
        if CATEGORIES[f.cat][2]:          # 可自動修的不標（風格層處理）
            continue
        if order[f.conf] >= order[min_conf]:
            by_para.setdefault(f.para, []).append(f)
    paras = {p.idx: p for p in rv.paras}
    ai = None
    if suggest is not None:
        ai = suggest([{"idx": i, "text": paras[i].text, "flags": sorted({f.label for f in by_para[i]}),
                       "weight": max(CONF_WEIGHT[f.conf] for f in by_para[i])}
                      for i in sorted(by_para) if not paras[i].is_heading and not paras[i].in_table])
    extra = (ai or {}).pop("suggestions", {})
    with zipfile.ZipFile(src) as z:
        names = z.namelist()
        existing = etree.fromstring(z.read("word/comments.xml")) if "word/comments.xml" in names else None
        rels = etree.fromstring(z.read("word/_rels/document.xml.rels"))
        ct = etree.fromstring(z.read("[Content_Types].xml"))
        blobs = {i.filename: z.read(i.filename) for i in z.infolist()}
    comments = existing if existing is not None else etree.Element(W + "comments", nsmap={"w": NS_W})
    used = [int(c.get(W + "id")) for c in comments if c.get(W + "id", "").isdigit()]
    nxt = (max(used) + 1) if used else 0
    date = _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    n = 0
    for idx in sorted(by_para):
        p = paras[idx].element
        cid = nxt + n
        n += 1
        lines = _lines(by_para[idx])
        if idx in extra:
            lines += _suggestion_lines(paras[idx].text, extra[idx])
        comments.append(_comment_el(cid, lines, date))
        start = etree.Element(W + "commentRangeStart"); start.set(W + "id", str(cid))
        end = etree.Element(W + "commentRangeEnd"); end.set(W + "id", str(cid))
        ref = etree.Element(W + "r")
        etree.SubElement(ref, W + "commentReference").set(W + "id", str(cid))
        ppr = p.find(W + "pPr")
        if ppr is not None:
            ppr.addnext(start)
        else:
            p.insert(0, start)
        p.append(end)
        p.append(ref)
    if n == 0:
        with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zo:
            for info in zin.infolist():
                zo.writestr(info, zin.read(info.filename))
        return {"comments": 0, "paragraphs": 0, **({"ai": ai} if ai else {})}
    if existing is None:
        ids = {r.get("Id") for r in rels}
        k = 1
        while "rId%d" % k in ids:
            k += 1
        rel = etree.SubElement(rels, "{%s}Relationship" % NS_REL)
        rel.set("Id", "rId%d" % k); rel.set("Type", REL_COMMENTS); rel.set("Target", "comments.xml")
        ov = etree.SubElement(ct, "{%s}Override" % NS_CT)
        ov.set("PartName", "/word/comments.xml"); ov.set("ContentType", CT_COMMENTS)
    root = rv.paras[0].element.getroottree().getroot()
    blobs["word/document.xml"] = _ser(root)
    blobs["word/comments.xml"] = _ser(comments)
    blobs["word/_rels/document.xml.rels"] = _ser(rels)
    blobs["[Content_Types].xml"] = _ser(ct)
    order_names = list(names) + (["word/comments.xml"] if "word/comments.xml" not in names else [])
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zo:
        for name in order_names:
            zi = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.create_system = 0
            zi.external_attr = 0
            zo.writestr(zi, blobs[name])
    return {"comments": n, "paragraphs": len(by_para), **({"ai": ai} if ai else {})}
