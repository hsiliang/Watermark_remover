import json
import threading
import urllib.parse
import urllib.request
import urllib.error
import zipfile
import io
from http.server import ThreadingHTTPServer

import pytest

from wmr import web


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "ROOT", str(tmp_path / "web"))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield "http://127.0.0.1:%d" % srv.server_address[1]
    srv.shutdown()


def _post(url, data=b""):
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_full_flow(server, dirty_docx):
    page = urllib.request.urlopen(server + "/").read().decode()
    assert "Watermark Remover" in page and "文件醫院" in page and "本機自用" not in page and "只在這台電腦" not in page
    st, j = _post(server + "/api/upload?name=t.docx", open(dirty_docx, "rb").read())
    assert st == 200 and j["findings"]
    assert b"<html" in urllib.request.urlopen(server + "/report/" + j["id"]).read()
    st, c = _post(server + "/api/clean?id=%s&style=0&keep_hidden=0" % j["id"])
    assert st == 200 and not [f for f in c["after"] if f["severity"] == "high"]
    body = urllib.request.urlopen(server + "/download/" + j["id"]).read()
    assert zipfile.ZipFile(io.BytesIO(body)).testzip() is None


def test_rejects_non_docx_and_bad_ids(server):
    st, j = _post(server + "/api/upload?name=x.docx", b"hello")
    assert st == 400 and "error" in j
    st, j = _post(server + "/api/clean?id=../../etc&style=0")
    assert st == 500
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(server + "/download/..%2f..%2fetc")
    assert e.value.code == 404


def test_rejects_foreign_host_header(server):
    req = urllib.request.Request(server + "/", headers={"Host": "evil.example.com"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 403


def _props(body):
    z = zipfile.ZipFile(io.BytesIO(body))
    return z.read("docProps/core.xml").decode(), z.read("docProps/app.xml").decode()


def test_custom_author_company_and_default(server, dirty_docx):
    import urllib.parse as up
    raw = open(dirty_docx, "rb").read()
    st, j = _post(server + "/api/upload?name=t.docx", raw)
    assert j["defaults"] == {"author": "Hsi Liang", "company": "https://github.com/hsiliang"}
    q = "&author=%s&company=%s" % (up.quote("王 & <小明>"), up.quote("某某大學"))
    st, c = _post(server + "/api/clean?id=%s&style=0&keep_hidden=0%s" % (j["id"], q))
    assert st == 200 and not [f for f in c["after"] if f["code"].endswith("not-owner")]
    core, app = _props(urllib.request.urlopen(server + "/download/" + j["id"]).read())
    assert "<dc:creator>王 &amp; &lt;小明&gt;</dc:creator>" in core and "<cp:lastModifiedBy>王 &amp; &lt;小明&gt;</cp:lastModifiedBy>" in core
    assert "<Company>某某大學</Company>" in app
    # 留空 → 預設值
    st, c = _post(server + "/api/clean?id=%s&style=0&keep_hidden=0&author=&company=" % j["id"])
    core, app = _props(urllib.request.urlopen(server + "/download/" + j["id"]).read())
    assert "Hsi Liang" in core and "https://github.com/hsiliang" in app


def test_invalid_identity_rejected(server, dirty_docx):
    st, j = _post(server + "/api/upload?name=t.docx", open(dirty_docx, "rb").read())
    st, c = _post(server + "/api/clean?id=%s&author=%s" % (j["id"], "x" * 101))
    assert st == 500 and "作者" in c["error"]


def test_review_copy_and_final_flow(server, tmp_path):
    from docx import Document
    d = Document()
    d.add_paragraph("此外，首先要說明甲。其次要說明乙。最後要說明丙。")
    for _ in range(6):
        d.add_paragraph("不是甲，而是乙。這件事很重要。" * 3)
    p = str(tmp_path / "a.docx")
    d.save(p)
    st, j = _post(server + "/api/upload?name=a.docx", open(p, "rb").read())
    assert j["risk"]["level"] in ("high", "medium", "low")
    st, c = _post(server + "/api/clean?id=%s&style=0&keep_hidden=0" % j["id"])
    assert c["risk_before"]["level"] and c["risk_after"]["level"]
    st, r = _post(server + "/api/review?id=" + j["id"] + "&min_conf=" + urllib.parse.quote("低"))
    assert st == 200 and r["comments"] >= 1
    rev = urllib.request.urlopen(server + "/download/%s/review" % j["id"]).read()
    assert "word/comments.xml" in zipfile.ZipFile(io.BytesIO(rev)).namelist()
    st, f = _post(server + "/api/final?id=%s&style=0&keep_hidden=0" % j["id"], rev)
    assert st == 200 and f["removed_comments"] == r["comments"] and f["diffs"] == []
    fin = urllib.request.urlopen(server + "/download/%s/final" % j["id"]).read()
    names = zipfile.ZipFile(io.BytesIO(fin)).namelist()
    assert not any("comments" in n for n in names)
    # 沒上傳待修版（跳過人工修改）也能出最終版
    st, f2 = _post(server + "/api/final?id=%s&style=0&keep_hidden=0" % j["id"])
    assert st == 200 and f2["removed_comments"] == 0
