"""最後一步（純本地、無 AI）：重寫文件屬性，並整理 ZIP 外觀。"""
from __future__ import annotations

import datetime as _dt
import zipfile

from lxml import etree

from .painmeta import pain_to_props
from .common import AUTHOR, COMPANY, NS_CP, NS_DC, NS_DCTERMS, NS_EP
from .rebuild import DECL, _ser


def _set(root, tag, text):
    e = root.find(tag)
    if e is None:
        e = etree.SubElement(root, tag)
    e.text = text


def finalize_props(path: str, author: str = AUTHOR, company: str = COMPANY, pain: float | None = None) -> None:
    with zipfile.ZipFile(path) as z:
        items = [(i, z.read(i.filename)) for i in z.infolist()]
    now_dt = _dt.datetime.utcnow().replace(microsecond=0)
    now = now_dt.isoformat() + "Z"
    pm = pain_to_props(pain) if pain is not None else None
    created = (now_dt - _dt.timedelta(minutes=pm["a"])).isoformat() + "Z" if pm else None
    out = []
    for info, data in items:
        if info.filename == "docProps/core.xml":
            r = etree.fromstring(data)
            _set(r, "{%s}creator" % NS_DC, author)
            _set(r, "{%s}lastModifiedBy" % NS_CP, author)
            for t in ("title", "subject", "description"):
                _set(r, "{%s}%s" % (NS_DC, t), "")
            _set(r, "{%s}keywords" % NS_CP, "")
            if pm:
                _set(r, "{%s}revision" % NS_CP, str(pm["revision"]))
                for tag, val in (("created", created), ("modified", now)):
                    e = r.find("{%s}%s" % (NS_DCTERMS, tag))
                    if e is None:
                        e = etree.SubElement(r, "{%s}%s" % (NS_DCTERMS, tag))
                    e.set("{http://www.w3.org/2001/XMLSchema-instance}type", "dcterms:W3CDTF")
                    e.text = val
            data = _ser(r)
        elif info.filename == "docProps/app.xml":
            r = etree.fromstring(data)
            _set(r, "{%s}Company" % NS_EP, company)
            if pm:
                _set(r, "{%s}TotalTime" % NS_EP, str(pm["h"]))
            data = _ser(r)
        out.append((info.filename, data))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zo:
        for n, d in out:
            zi = zipfile.ZipInfo(n, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.create_system = 0
            zi.external_attr = 0
            zo.writestr(zi, d)
