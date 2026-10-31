import zipfile
from docx import Document
from wmr.scan import scan
from wmr.rebuild import rebuild, strip_png
from wmr.common import AUTHOR, COMPANY


def codes(path):
    return {f.code for f in scan(path)}


def test_scan_detects_traces(dirty_docx):
    c = codes(dirty_docx)
    assert {"creator-signature", "description-signature", "extra-part", "invisible-chars",
            "exotic-spaces", "hidden-text", "rsid-attrs", "xml-declaration-single-quote"} <= c


def test_rebuild_removes_all_traces(dirty_docx, tmp_path):
    out = str(tmp_path / "clean.docx")
    rebuild(dirty_docx, out)
    left = [f for f in scan(out) if f.severity in ("high", "medium")]
    assert left == [], left


def test_rebuild_properties_and_text(dirty_docx, tmp_path):
    out = str(tmp_path / "clean.docx")
    rebuild(dirty_docx, out)
    d = Document(out)
    assert d.core_properties.author == AUTHOR
    assert d.core_properties.last_modified_by == AUTHOR
    assert COMPANY in zipfile.ZipFile(out).read("docProps/app.xml").decode()
    texts = [p.text for p in d.paragraphs]
    assert "正文 內容" in texts
    assert all("藏起來" not in t for t in texts)


def test_rebuild_preserves_format(dirty_docx, tmp_path):
    out = str(tmp_path / "clean.docx")
    rebuild(dirty_docx, out)
    r = Document(out).paragraphs[0].runs[0]
    assert r.bold and r.font.size.pt == 14 and str(r.font.color.rgb) == "123456"
    assert len(Document(out).inline_shapes) == 1


def test_png_text_chunks_stripped(dirty_docx, tmp_path):
    out = str(tmp_path / "clean.docx")
    rebuild(dirty_docx, out)
    z = zipfile.ZipFile(out)
    media = [n for n in z.namelist() if n.endswith(".png")]
    assert media and all(b"Matplotlib" not in z.read(n) for n in media)


def test_zip_layout(dirty_docx, tmp_path):
    out = str(tmp_path / "clean.docx")
    rebuild(dirty_docx, out)
    z = zipfile.ZipFile(out)
    assert z.namelist()[0] == "[Content_Types].xml"
    assert all(i.date_time[:3] == (1980, 1, 1) and i.create_system == 0 for i in z.infolist())


def test_package_is_strictly_valid(dirty_docx, tmp_path):
    """Word 比 python-docx 嚴格：PartName 不可重複、關聯目標必須存在。"""
    import posixpath
    from lxml import etree
    out = str(tmp_path / "clean.docx")
    rebuild(dirty_docx, out)
    z = zipfile.ZipFile(out)
    names = set(z.namelist())
    assert len(z.namelist()) == len(names)
    ct = etree.fromstring(z.read("[Content_Types].xml"))
    parts = [e.get("PartName") for e in ct if e.tag.endswith("Override")]
    assert len(parts) == len(set(parts)), "重複的 Override"
    assert all(p.lstrip("/") in names for p in parts)
    for n in names:
        if not n.endswith(".rels"):
            continue
        base = posixpath.dirname(posixpath.dirname(n))
        for rel in etree.fromstring(z.read(n)):
            if rel.get("TargetMode") != "External":
                assert posixpath.normpath(posixpath.join(base, rel.get("Target"))).lstrip("/") in names, (n, rel.get("Target"))
