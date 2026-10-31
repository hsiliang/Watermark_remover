"""AI／人工改寫 → 純本地寫回。

流程：export_edits（列出有可疑訊號的段落，附指引）→ 你或 Claude 在 JSON 的 new_text 填入改寫 →
apply_edits（純本地、確定性）把改寫寫回 docx，保留段落與字元格式。

最後寫回的這一步不使用任何 AI；AI 只參與「想怎麼改」。
"""
from __future__ import annotations

import copy
import json
import os
import re
import zipfile
from typing import Dict, List, Optional

from lxml import etree

from .common import NS_W
from .rebuild import _ser
from .suspect import CATEGORIES, analyze, extract_paragraphs

W = "{%s}" % NS_W
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
DEFAULT_SKIP = ("參考文獻", "參考資料", "References", "Bibliography")

INSTRUCTIONS = (
    "請逐段改寫 edits 中每一項的 text，把結果填入 new_text（不想改就保持 null）。規則：\n"
    "1. 保留原意、所有事實、數字、專有名詞、引用（作者與年份）；不要新增原文沒有的事實、數據或來源。\n"
    "2. 依 flags 的說明處理該段的問題；其他沒被標示的句子盡量不動。\n"
    "3. 讓句子長短交錯：可合併相鄰短句、拆開過長的句子；減少句首連接詞；少用『不僅…更…』『不是…而是…』與三項並列。\n"
    "4. 長度與原文相差不要超過 ±20%。純文字回覆；需要粗體時用 **粗體**。\n"
    "5. 不要改動 editable 為 false 的項目；不要改動 id 與 text。\n"
    "6. 若某段的問題是『模糊引用（無出處）』：你無法憑空補出處，請改成不需要出處的陳述，或保持不變並留給人工處理。"
)

_BLOCKERS = {
    "drawing": "含圖片", "pict": "含圖片", "fldChar": "含欄位（目錄／交互參照）", "fldSimple": "含欄位（目錄／交互參照）",
    "footnoteReference": "含註腳參照", "endnoteReference": "含註腳參照", "hyperlink": "含超連結", "sdt": "含內容控制項",
    "commentReference": "含註解標記", "oMath": "含公式", "oMathPara": "含公式",
}


def _blocker(p) -> Optional[str]:
    for el in p.iter():
        if isinstance(el.tag, str):
            name = etree.QName(el).localname
            if name in _BLOCKERS:
                return "%s，請在 Word 手動修改" % _BLOCKERS[name]
    return None


def _text_runs(p) -> List:
    return [r for r in p.iter(W + "r") if r.find(W + "t") is not None]


def _rpr_sig(r) -> str:
    rpr = r.find(W + "rPr")
    return etree.tostring(rpr).decode() if rpr is not None else ""


def export_edits(path: str, out_json: str, skip_headings=DEFAULT_SKIP, min_conf: str = "低") -> int:
    rv = analyze(path, tuple(skip_headings))
    order = {"低": 0, "中": 1, "高": 2}
    by_para: Dict[int, List] = {}
    for f in rv.flags:
        if order[f.conf] >= order[min_conf]:
            by_para.setdefault(f.para, []).append(f)
    items = []
    for p in rv.paras:
        if p.idx not in by_para or p.is_heading:
            continue
        why = _blocker(p.element)
        runs = _text_runs(p.element)
        seen, flags = set(), []
        for f in by_para[p.idx]:
            k = (f.cat, f.snippet)
            if k in seen:
                continue
            seen.add(k)
            flags.append({"類別": f.label, "片段": f.snippet, "說明": CATEGORIES[f.cat][4]})
        items.append({
            "id": p.idx, "heading": p.heading_path, "text": p.text, "flags": flags,
            "editable": why is None, "why_not": why or "",
            "mixed_format": len({_rpr_sig(r) for r in runs}) > 1, "new_text": None,
        })
    data = {"source": os.path.basename(path), "skip_headings": list(skip_headings),
            "instructions": INSTRUCTIONS, "edits": items}
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return len(items)


def _mk_run(base_rpr, text: str, bold: Optional[bool]) -> etree._Element:
    r = etree.Element(W + "r")
    if base_rpr is not None:
        rpr = copy.deepcopy(base_rpr)
        if bold is not None:
            for tag in ("b", "bCs"):
                for x in rpr.findall(W + tag):
                    rpr.remove(x)
            if bold:
                b = etree.Element(W + "b")
                # b 在 rPr 中位於 rStyle / rFonts 之後
                pos = 0
                for i, c in enumerate(rpr):
                    if etree.QName(c).localname in ("rStyle", "rFonts"):
                        pos = i + 1
                rpr.insert(pos, b)
        r.append(rpr)
    elif bold:
        rpr = etree.SubElement(r, W + "rPr")
        etree.SubElement(rpr, W + "b")
    parts = text.split("\t")
    for i, part in enumerate(parts):
        if i:
            etree.SubElement(r, W + "tab")
        if part:
            t = etree.SubElement(r, W + "t")
            t.text = part
            t.set(XML_SPACE, "preserve")
    return r


def _replace_paragraph_text(p, new_text: str) -> None:
    runs = _text_runs(p)
    base = max(runs, key=lambda r: sum(len(t.text or "") for t in r.iter(W + "t")))
    base_rpr = base.find(W + "rPr")
    first = runs[0]
    parent = first.getparent()
    pos = parent.index(first)
    for r in runs:
        r.getparent().remove(r)
    for x in list(p.iter(W + "proofErr")):
        x.getparent().remove(x)
    has_marks = "**" in new_text
    segs = re.split(r"\*\*(.+?)\*\*", new_text) if has_marks else [new_text]
    new_runs = []
    for i, seg in enumerate(segs):
        if not seg:
            continue
        new_runs.append(_mk_run(base_rpr, seg, bool(i % 2) if has_marks else None))
    for k, r in enumerate(new_runs):
        parent.insert(pos + k, r)


def apply_edits(src: str, edits_json: str, dst: str, force: bool = False) -> Dict:
    data = json.load(open(edits_json, encoding="utf-8"))
    paras = extract_paragraphs(src, tuple(data.get("skip_headings", DEFAULT_SKIP)))
    by_id = {p.idx: p for p in paras}
    report = {"applied": 0, "skipped": [], "unchanged": 0}
    for e in data["edits"]:
        nt = e.get("new_text")
        if nt is None or nt == e["text"]:
            report["unchanged"] += 1
            continue
        p = by_id.get(e["id"])
        if p is None or p.text != e["text"]:
            report["skipped"].append((e["id"], "原文不符（原稿在匯出後已被更動，或段落編號對不上）"))
            continue
        why = _blocker(p.element)
        if why and not force:
            report["skipped"].append((e["id"], why))
            continue
        if not _text_runs(p.element):
            report["skipped"].append((e["id"], "找不到可替換的文字 run"))
            continue
        _replace_paragraph_text(p.element, nt.strip())
        report["applied"] += 1
    root = paras[0].element.getroottree().getroot() if paras else None
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zo:
        for info in zin.infolist():
            blob = _ser(root) if (info.filename == "word/document.xml" and root is not None) else zin.read(info.filename)
            zi = zipfile.ZipInfo(info.filename, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.create_system = 0
            zi.external_attr = 0
            zo.writestr(zi, blob)
    return report
