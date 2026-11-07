import os
import re
import time
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
    assert "Watermark Remover" in page and "斧大醫院" in page and "本機自用" not in page and "只在這台電腦" not in page
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
    assert j["defaults"] == {"author": "一隻叫獸", "company": "獅業中..."}
    q = "&author=%s&company=%s" % (up.quote("王 & <小明>"), up.quote("某某大學"))
    st, c = _post(server + "/api/clean?id=%s&style=0&keep_hidden=0%s" % (j["id"], q))
    assert st == 200 and not [f for f in c["after"] if f["code"].endswith("not-owner")]
    core, app = _props(urllib.request.urlopen(server + "/download/" + j["id"]).read())
    assert "<dc:creator>王 &amp; &lt;小明&gt;</dc:creator>" in core and "<cp:lastModifiedBy>王 &amp; &lt;小明&gt;</cp:lastModifiedBy>" in core
    assert "<Company>某某大學</Company>" in app
    # 留空 → 預設值
    st, c = _post(server + "/api/clean?id=%s&style=0&keep_hidden=0&author=&company=" % j["id"])
    core, app = _props(urllib.request.urlopen(server + "/download/" + j["id"]).read())
    assert "一隻叫獸" in core and "獅業中..." in app


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


def test_allowed_hosts_env_list(server, monkeypatch):
    monkeypatch.setattr(web, "ALLOWED_HOSTS", {"127.0.0.1", "localhost", "my.example.net"})
    req = urllib.request.Request(server + "/", headers={"Host": "my.example.net"})
    assert urllib.request.urlopen(req).status == 200
    req = urllib.request.Request(server + "/", headers={"Host": "evil.example.com"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 403


def test_password_gate(server, monkeypatch, dirty_docx):
    monkeypatch.setattr(web, "PASSWORD", "s3cret")
    page = urllib.request.urlopen(server + "/").read().decode()
    assert "密碼" in page and "Watermark Remover" not in page          # 未登入只看到登入頁
    st, j = _post(server + "/api/upload?name=t.docx", open(dirty_docx, "rb").read())
    assert st == 401
    data = urllib.parse.urlencode({"password": "wrong"}).encode()
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(urllib.request.Request(server + "/login", data=data, method="POST"))
    assert e.value.code == 401
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    op = urllib.request.build_opener(NoRedirect)
    good = urllib.parse.urlencode({"password": "s3cret"}).encode()
    with pytest.raises(urllib.error.HTTPError) as e:
        op.open(urllib.request.Request(server + "/login", data=good, method="POST"))
    assert e.value.code == 303
    cookie = e.value.headers["Set-Cookie"].split(";")[0]
    assert "HttpOnly" in e.value.headers["Set-Cookie"] and "SameSite=Strict" in e.value.headers["Set-Cookie"]
    req = urllib.request.Request(server + "/", headers={"Cookie": cookie})
    assert b"Watermark Remover" in urllib.request.urlopen(req).read()
    req = urllib.request.Request(server + "/api/upload?name=t.docx", data=open(dirty_docx, "rb").read(), method="POST", headers={"Cookie": cookie})
    assert urllib.request.urlopen(req).status == 200


def test_purge_expired_and_headers(server, monkeypatch, tmp_path):
    import os, time
    monkeypatch.setattr(web, "TTL_SECONDS", 60)
    os.makedirs(web.ROOT, exist_ok=True)
    old, new = os.path.join(web.ROOT, "a" * 16), os.path.join(web.ROOT, "b" * 16)
    os.makedirs(old); os.makedirs(new)
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    assert web.purge_expired() == 1 and not os.path.exists(old) and os.path.exists(new)
    r = urllib.request.urlopen(server + "/")
    assert r.headers["X-Robots-Tag"].startswith("noindex") and r.headers["X-Content-Type-Options"] == "nosniff"
    assert b"Disallow: /" in urllib.request.urlopen(server + "/robots.txt").read()


def test_busy_returns_503(server, monkeypatch):
    import threading as th
    monkeypatch.setattr(web, "_BUSY", th.BoundedSemaphore(1))
    web._BUSY.acquire()
    st, j = _post(server + "/api/upload?name=a.docx", b"PK")
    assert st == 503


def test_final_without_prior_job(server, tmp_path):
    """關掉網頁後，只憑修改後的待修版就能出最終版（編號空白或已過期都行）。"""
    from docx import Document
    d = Document()
    d.add_paragraph("內容。")
    p = str(tmp_path / "e.docx")
    d.save(p)
    body = open(p, "rb").read()
    st, f = _post(server + "/api/final?id=&style=0&keep_hidden=0&name=" + urllib.parse.quote("論文.docx"), body)
    assert st == 200 and re.fullmatch(r"[0-9a-f]{16}", f["id"])
    fin = urllib.request.urlopen(server + "/download/%s/final" % f["id"])
    assert "filename*=UTF-8''" + urllib.parse.quote("論文_clean.docx") in fin.headers["Content-Disposition"]
    st, f2 = _post(server + "/api/final?id=%s&style=0&keep_hidden=0" % ("0" * 16), body)   # 已過期的舊編號
    assert st == 200 and f2["id"] == "0" * 16
    st, bad = _post(server + "/api/final?id=&style=0&keep_hidden=0")                           # 沒檔案也沒編號
    assert st >= 400


def test_access_renews_ttl(server, monkeypatch, tmp_path):
    from docx import Document
    d = Document(); d.add_paragraph("x")
    p = str(tmp_path / "t.docx"); d.save(p)
    st, j = _post(server + "/api/upload?name=t.docx", open(p, "rb").read())
    jd = os.path.join(web.ROOT, j["id"])
    old = time.time() - 3 * 3600
    os.utime(jd, (old, old))
    _post(server + "/api/clean?id=%s&style=0&keep_hidden=0" % j["id"])
    monkeypatch.setattr(web, "TTL_SECONDS", 4 * 3600)
    assert time.time() - os.path.getmtime(jd) < 60
    assert web.purge_expired() == 0
