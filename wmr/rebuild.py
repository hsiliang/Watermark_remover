"""純本地、確定性（無 AI）的白名單重建。

只搬運內容與可見格式；識別性屬性、額外部件、媒體 metadata 全部剝除，
再以全新的 ZIP 封包寫出，作者／公司改為指定值。
"""
from __future__ import annotations

import datetime as _dt
import posixpath
import re
import struct
import zipfile
from typing import Dict, List, Optional

from lxml import etree

from .common import (
    AUTHOR, COMPANY, EXOTIC_SPACE_RE, INVISIBLE_RE, NS_CT, NS_REL, NS_W,
    PARTS_DROPPED_PREFIXES,
)

W = "{%s}" % NS_W
DECL = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\r\n'
REL_OFFICEDOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"
REL_CORE = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties"
REL_APP = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties"
CT_CORE = "application/vnd.openxmlformats-package.core-properties+xml"
CT_APP = "application/vnd.openxmlformats-officedocument.extended-properties+xml"
RELS_CT = "application/vnd.openxmlformats-package.relationships+xml"
DROP_REL_TYPES = ("attachedTemplate", "customXml", "webExtension", "people", "printerSettings", "thumbnail", "vbaProject", "oleObject")


# ---------- 媒體 metadata 剝除 ----------
def strip_png(data: bytes) -> bytes:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return data
    keep_critical = {b"IHDR", b"PLTE", b"IDAT", b"IEND", b"tRNS", b"gAMA", b"cHRM", b"sRGB", b"iCCP", b"sBIT", b"pHYs", b"bKGD", b"acTL", b"fcTL", b"fdAT"}
    out, i = [data[:8]], 8
    while i + 8 <= len(data):
        ln, = struct.unpack(">I", data[i:i + 4])
        typ = data[i + 4:i + 8]
        chunk = data[i:i + 12 + ln]
        if typ in keep_critical:
            out.append(chunk)
        i += 12 + ln
    return b"".join(out)


def strip_jpeg(data: bytes) -> bytes:
    if data[:2] != b"\xff\xd8":
        return data
    out, i = [data[:2]], 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            out.append(data[i:])
            break
        m = data[i + 1]
        if m == 0xDA:  # SOS: 之後為影像資料
            out.append(data[i:])
            break
        ln, = struct.unpack(">H", data[i + 2:i + 4])
        seg = data[i:i + 2 + ln]
        # 丟棄 APP1-APP15（EXIF/XMP/ICC 以外）與 COM；保留 APP0(JFIF) 與 APP2(ICC)
        if m in (0xE0, 0xE2) or not (0xE1 <= m <= 0xEF or m == 0xFE):
            out.append(seg)
        i += 2 + ln
    return b"".join(out)


# ---------- XML 清理 ----------
def clean_text(s: str) -> str:
    s = INVISIBLE_RE.sub("", s)
    return EXOTIC_SPACE_RE.sub(" ", s)


def sanitize_xml(root: etree._Element, partname: str, drop_hidden: bool = True) -> None:
    # 屬性：rsid*、paraId、textId
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for k in list(el.attrib):
            ln = etree.QName(k).localname
            if ln.startswith("rsid") or ln in ("paraId", "textId"):
                del el.attrib[k]
        if el.tag in (W + "t", W + "instrText", W + "delText") and el.text:
            el.text = clean_text(el.text)
        if el.tag in (W + "ins", W + "del", W + "comment", W + "moveFrom", W + "moveTo") and el.get(W + "author") is not None:
            el.set(W + "author", AUTHOR)
        if el.tag == W + "comment" and el.get(W + "initials") is not None:
            el.set(W + "initials", AUTHOR[0])

    # 移除節點
    gobk_ids = {b.get(W + "id") for b in root.iter(W + "bookmarkStart") if b.get(W + "name") == "_GoBack"}
    doomed = []
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        if el.tag == W + "proofErr":
            doomed.append(el)
        elif el.tag == W + "bookmarkStart" and el.get(W + "id") in gobk_ids:
            doomed.append(el)
        elif el.tag == W + "bookmarkEnd" and el.get(W + "id") in gobk_ids:
            doomed.append(el)
        elif partname.endswith("settings.xml") and el.tag in (W + "rsids", W + "docVars", W + "attachedTemplate"):
            doomed.append(el)
        elif drop_hidden and el.tag == W + "r" and el.find("%srPr/%svanish" % (W, W)) is not None:
            doomed.append(el)
    for el in doomed:
        p = el.getparent()
        if p is None:
            continue
        # 保留 tail 文字
        if el.tail:
            prev = el.getprevious()
            if prev is not None:
                prev.tail = (prev.tail or "") + el.tail
            else:
                p.text = (p.text or "") + el.tail
        p.remove(el)


def _ser(root: etree._Element) -> bytes:
    return DECL + etree.tostring(root, encoding="UTF-8", xml_declaration=False)


def _core_xml(now: str) -> bytes:
    s = (
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        '<dc:title></dc:title><dc:subject></dc:subject><dc:creator>%s</dc:creator><cp:keywords></cp:keywords>'
        '<dc:description></dc:description><cp:lastModifiedBy>%s</cp:lastModifiedBy><cp:revision>1</cp:revision>'
        '<dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created>'
        '<dcterms:modified xsi:type="dcterms:W3CDTF">%s</dcterms:modified></cp:coreProperties>'
    ) % (AUTHOR, AUTHOR, now, now)
    return DECL + s.encode("utf-8")


def _app_xml() -> bytes:
    s = (
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        '<Template>Normal.dotm</Template><TotalTime>0</TotalTime><Application>Microsoft Office Word</Application>'
        '<DocSecurity>0</DocSecurity><ScaleCrop>false</ScaleCrop><Company>%s</Company>'
        '<LinksUpToDate>false</LinksUpToDate><SharedDoc>false</SharedDoc><HyperlinksChanged>false</HyperlinksChanged>'
        '<AppVersion>16.0000</AppVersion></Properties>'
    ) % COMPANY
    return DECL + s.encode("utf-8")


def _dropped(name: str) -> bool:
    return any(name.startswith(p) for p in PARTS_DROPPED_PREFIXES)


def _resolve(base_part: str, target: str) -> str:
    base_dir = posixpath.dirname(posixpath.dirname(base_part))  # .../_rels/x.rels -> 目錄
    return posixpath.normpath(posixpath.join(base_dir, target)).lstrip("/")


def rebuild(src: str, dst: str, drop_hidden: bool = True, now: Optional[str] = None) -> Dict[str, List[str]]:
    now = now or _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    report = {"dropped": [], "kept": []}
    with zipfile.ZipFile(src) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        ct_root = etree.fromstring(z.read("[Content_Types].xml"))
        defaults = {e.get("Extension").lower(): e.get("ContentType") for e in ct_root if e.tag == "{%s}Default" % NS_CT}
        overrides = {e.get("PartName").lstrip("/"): e.get("ContentType") for e in ct_root if e.tag == "{%s}Override" % NS_CT}

        kept = [n for n in names if n != "[Content_Types].xml" and n != "_rels/.rels" and not _dropped(n)]
        kept_set = set(kept)
        report["dropped"] = [n for n in names if n not in kept_set and n not in ("[Content_Types].xml", "_rels/.rels")]

        files: Dict[str, bytes] = {}
        for n in kept:
            data = z.read(n)
            low = n.lower()
            if n.endswith(".rels"):
                root = etree.fromstring(data)
                for rel in list(root):
                    typ = (rel.get("Type") or "").rsplit("/", 1)[-1]
                    tgt = rel.get("Target") or ""
                    drop = typ in DROP_REL_TYPES
                    if not drop and rel.get("TargetMode") != "External":
                        drop = _resolve(n, tgt) not in kept_set
                    if drop:
                        root.remove(rel)
                data = _ser(root)
            elif low.endswith(".xml") and n.startswith("word/"):
                root = etree.fromstring(data)
                sanitize_xml(root, n, drop_hidden)
                data = _ser(root)
            elif low.endswith(".png"):
                data = strip_png(data)
            elif low.endswith((".jpg", ".jpeg")):
                data = strip_jpeg(data)
            files[n] = data

        # 根關聯與內容型別
        root_rels = (
            '<Relationships xmlns="%s">'
            '<Relationship Id="rId1" Type="%s" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="%s" Target="docProps/core.xml"/>'
            '<Relationship Id="rId3" Type="%s" Target="docProps/app.xml"/></Relationships>'
        ) % (NS_REL, REL_OFFICEDOC, REL_CORE, REL_APP)
        files["_rels/.rels"] = DECL + root_rels.encode()
        files["docProps/core.xml"] = _core_xml(now)
        files["docProps/app.xml"] = _app_xml()

        ct = ['<Types xmlns="%s">' % NS_CT]
        used_ext = {n.rsplit(".", 1)[-1].lower() for n in files if "." in n.rsplit("/", 1)[-1]}
        ct.append('<Default Extension="rels" ContentType="%s"/>' % RELS_CT)
        ct.append('<Default Extension="xml" ContentType="application/xml"/>')
        for ext in sorted(used_ext - {"rels", "xml"}):
            if ext in defaults:
                ct.append('<Default Extension="%s" ContentType="%s"/>' % (ext, defaults[ext]))
        for n in sorted(files):
            if n in overrides:
                ct.append('<Override PartName="/%s" ContentType="%s"/>' % (n, overrides[n]))
        ct.append('<Override PartName="/docProps/core.xml" ContentType="%s"/>' % CT_CORE)
        ct.append('<Override PartName="/docProps/app.xml" ContentType="%s"/>' % CT_APP)
        ct.append("</Types>")
        files["[Content_Types].xml"] = DECL + "".join(ct).encode()

    # Word 風格的寫入順序
    def order(n: str):
        head = ["[Content_Types].xml", "_rels/.rels", "word/_rels/document.xml.rels", "word/document.xml"]
        tail = ["docProps/core.xml", "docProps/app.xml"]
        if n in head:
            return (0, head.index(n), n)
        if n in tail:
            return (2, tail.index(n), n)
        return (1, 0, n)

    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as out:
        for n in sorted(files, key=order):
            zi = zipfile.ZipInfo(n, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.create_system = 0
            zi.external_attr = 0
            out.writestr(zi, files[n])
            report["kept"].append(n)
    return report
