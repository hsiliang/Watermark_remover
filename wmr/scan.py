from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, asdict
from typing import List

from lxml import etree

from .common import (
    AUTHOR, COMPANY, INVISIBLE_RE, EXOTIC_SPACE_RE, NS_W, NS_CP, NS_DC, NS_EP,
    PARTS_DROPPED_PREFIXES, SIGNATURE_APPS, SIGNATURE_CREATORS,
    SIGNATURE_DESCRIPTIONS, SIGNATURE_TITLES,
)

W = "{%s}" % NS_W


@dataclass
class Finding:
    layer: str      # properties / package / xml / content / zip
    code: str
    severity: str   # high / medium / low
    where: str
    detail: str = ""


def _parse(z: zipfile.ZipFile, name: str):
    try:
        return etree.fromstring(z.read(name))
    except (KeyError, etree.XMLSyntaxError):
        return None


def scan(path: str, style: bool = False) -> List[Finding]:
    out: List[Finding] = []
    add = lambda *a, **k: out.append(Finding(*a, **k))
    with zipfile.ZipFile(path) as z:
        names = z.namelist()

        # ---- zip 層 ----
        if z.comment:
            add("zip", "zip-comment", "medium", "archive", repr(z.comment[:60]))
        infos = z.infolist()
        if names and names[0] != "[Content_Types].xml":
            add("zip", "content-types-not-first", "low", "archive", names[0])
        if any(i.create_system != 0 for i in infos):
            add("zip", "create-system-unix", "low", "archive", "非 Word 風格的 ZIP 建立系統")
        if any(i.extra for i in infos):
            add("zip", "zip-extra-fields", "low", "archive", "含 extra field")
        if any(i.date_time[:3] != (1980, 1, 1) for i in infos):
            add("zip", "zip-timestamps-not-1980", "low", "archive", "Word 產物的 ZIP 時間戳為 1980-01-01，此檔為程式庫寫入時間")

        # ---- package 層 ----
        for n in names:
            if n.endswith("/"):
                continue
            if n in ("docProps/app.xml", "docProps/core.xml"):
                continue
            if any(n.startswith(p) for p in PARTS_DROPPED_PREFIXES):
                add("package", "extra-part", "high" if n.startswith(("customXml", "word/webext", "docProps/custom")) else "medium", n)

        # ---- properties 層 ----
        core = _parse(z, "docProps/core.xml")
        if core is None:
            add("properties", "no-core", "medium", "docProps/core.xml", "缺少 core.xml（非 Word 產物特徵）")
        else:
            def t(tag):
                e = core.find(tag)
                return (e.text or "") if e is not None else ""
            c = t("{%s}creator" % NS_DC)
            lm = t("{%s}lastModifiedBy" % NS_CP)
            if c in SIGNATURE_CREATORS:
                add("properties", "creator-signature", "high", "core.creator", c)
            elif c != AUTHOR:
                add("properties", "creator-not-owner", "medium", "core.creator", c)
            if lm in SIGNATURE_CREATORS:
                add("properties", "lastmodifiedby-signature", "high", "core.lastModifiedBy", lm)
            elif lm and lm != AUTHOR:
                add("properties", "lastmodifiedby-not-owner", "medium", "core.lastModifiedBy", lm)
            if t("{%s}description" % NS_DC) in SIGNATURE_DESCRIPTIONS:
                add("properties", "description-signature", "high", "core.description", t("{%s}description" % NS_DC))
            if t("{%s}title" % NS_DC) in SIGNATURE_TITLES:
                add("properties", "title-signature", "medium", "core.title", t("{%s}title" % NS_DC))
        app = _parse(z, "docProps/app.xml")
        if app is None:
            add("properties", "no-app", "medium", "docProps/app.xml", "缺少 app.xml")
        else:
            def a(tag):
                e = app.find("{%s}%s" % (NS_EP, tag))
                return (e.text or "") if e is not None else ""
            if a("Application") in SIGNATURE_APPS or not a("Application"):
                add("properties", "application-signature", "high", "app.Application", a("Application") or "(空)")
            if a("Company") != COMPANY:
                add("properties", "company-not-owner", "medium", "app.Company", a("Company") or "(空)")

        word_saved = bool(app is not None and app.find("{%s}Pages" % NS_EP) is not None
                          and (app.findtext("{%s}Application" % NS_EP) or "").startswith("Microsoft Office Word"))
        # ---- xml / content 層 ----
        parts = [n for n in names if n.startswith("word/") and n.endswith(".xml")]
        rsid_attrs = 0
        proof = 0
        gobk = 0
        pid = 0
        for n in parts:
            root = _parse(z, n)
            if root is None:
                continue
            for el in root.iter():
                if not isinstance(el.tag, str):
                    continue
                for k in el.attrib:
                    ln = etree.QName(k).localname
                    if ln.startswith("rsid"):
                        rsid_attrs += 1
                    elif ln in ("paraId", "textId"):
                        pid += 1
                if el.tag == W + "proofErr":
                    proof += 1
                elif el.tag == W + "bookmarkStart" and el.get(W + "name") == "_GoBack":
                    gobk += 1
                elif el.tag == W + "rsids":
                    add("xml", "settings-rsids", "low" if word_saved else "medium", n, "settings 內 rsid 清單")
                elif el.tag == W + "vanish":
                    add("content", "hidden-text", "high", n, "w:vanish 隱藏文字")
                elif el.tag == W + "sz" and el.get(W + "val", "").isdigit() and int(el.get(W + "val")) <= 2:
                    add("content", "tiny-font", "high", n, "字級 ≤1pt")
                elif el.tag == W + "color" and "styles" not in n and "numbering" not in n and "settings" not in n and (el.get(W + "val") or "").upper() == "FFFFFF":
                    add("content", "white-text", "medium", n, "白色文字（可能為隱藏內容）")
                elif el.tag in (W + "comment",) and (el.get(W + "author") or "") not in ("", AUTHOR):
                    add("package", "comment-author", "high", n, el.get(W + "author"))
                if style and el.tag == W + "t" and el.text:
                    from .style import scan_text
                    for kind, what in scan_text(el.text):
                        add("style", "style-" + kind, "low", n, what)
                if el.tag in (W + "t", W + "instrText") and el.text:
                    if INVISIBLE_RE.search(el.text):
                        add("content", "invisible-chars", "high", n, "零寬／bidi／tag 字元 × %d" % len(INVISIBLE_RE.findall(el.text)))
                    if EXOTIC_SPACE_RE.search(el.text):
                        add("content", "exotic-spaces", "medium", n, "特殊空白")
        if rsid_attrs:
            add("xml", "rsid-attrs", "low" if word_saved else "medium", "word/*.xml", "rsid 屬性 × %d%s" % (rsid_attrs, "（Word 原生）" if word_saved else ""))
        if proof:
            add("xml", "proofErr", "low", "word/*.xml", "proofErr × %d" % proof)
        if gobk:
            add("xml", "_GoBack", "low", "word/document.xml", "_GoBack 書籤")
        if pid:
            add("xml", "paraId", "low", "word/*.xml", "w14:paraId/textId × %d" % pid)

        # XML 宣告風格
        raw = z.read("word/document.xml") if "word/document.xml" in names else b""
        if raw.startswith(b"<?xml version='"):
            add("zip", "xml-declaration-single-quote", "medium", "word/document.xml", "單引號 XML 宣告（lxml/python 寫出的特徵，Word 為雙引號）")
        elif raw.startswith(b"<?xml") and b"\r\n" not in raw[:80]:
            add("zip", "xml-declaration-style", "low", "word/document.xml", "宣告後無 CRLF（Word 產物為 CRLF）")
    seen, uniq = set(), []
    for f in out:
        k = (f.layer, f.code, f.where, f.detail)
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    return uniq


def summarize(findings: List[Finding]) -> str:
    if not findings:
        return "未發現痕跡。"
    order = {"high": 0, "medium": 1, "low": 2}
    lines = []
    for f in sorted(findings, key=lambda x: (order[x.severity], x.layer, x.code)):
        lines.append("[%-6s] %-10s %-26s %s %s" % (f.severity, f.layer, f.code, f.where, f.detail))
    return "\n".join(lines)


def as_dicts(findings):
    return [asdict(f) for f in findings]
