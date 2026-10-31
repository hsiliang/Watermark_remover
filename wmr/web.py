"""本機網頁介面（僅綁 127.0.0.1；標準庫，無額外相依）。

流程：上傳 docx → 檢測＋可疑部位統計 → 按下清理 → 下載。
清理使用的仍是純本地的 rebuild／finalize，不呼叫任何 AI 或 Word。
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .common import AUTHOR, COMPANY, clean_identity
from . import comments as cm, finalize, rebuild as rb, report, scan as sc, suspect, verify as vf

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out", "web")
MAX_BYTES = 100 * 1024 * 1024
_ID = re.compile(r"^[0-9a-f]{16}$")
SKIP = ("參考文獻", "參考資料", "References", "Bibliography")


def _dir(jid: str) -> str:
    if not _ID.match(jid or ""):
        raise ValueError("bad id")
    return os.path.join(ROOT, jid)


def do_upload(data: bytes, name: str) -> dict:
    jid = secrets.token_hex(8)
    d = _dir(jid)
    os.makedirs(d)
    src = os.path.join(d, "in.docx")
    with open(src, "wb") as f:
        f.write(data)
    with open(os.path.join(d, "name.txt"), "w", encoding="utf-8") as f:
        f.write(os.path.basename(name) or "document.docx")
    findings = sc.scan(src)
    rv = suspect.analyze(src, SKIP)
    with open(os.path.join(d, "review.html"), "w", encoding="utf-8") as f:
        f.write(report.build_html(rv, os.path.basename(name)))
    return {"id": jid, "name": os.path.basename(name), "findings": sc.as_dicts(findings),
            "categories": _cats(rv), "metrics": rv.metrics,
            "defaults": {"author": AUTHOR, "company": COMPANY},
            "risk": _risk(findings, rv)}


def _risk(findings, rv=None) -> dict:
    """整體風險等級（高／中／低）：檔案層嚴重度與文字層信心取最高。僅為排序用的啟發式，不是機率。"""
    sev = {"high": 0, "medium": 0, "low": 0}
    for f in findings:
        sev[f.severity] += 1
    conf = {"高": 0, "中": 0, "低": 0}
    if rv is not None:
        for f in rv.flags:
            conf[f.conf] += 1
    level = "high" if (sev["high"] or conf["高"]) else "medium" if (sev["medium"] or conf["中"]) else "low"
    return {"level": level, "file": sev, "text": {"高": conf["高"], "中": conf["中"], "低": conf["低"]}}


def _cats(rv) -> list:
    out = []
    for c, n in sorted(rv.by_cat.items(), key=lambda kv: (-suspect.CONF_WEIGHT[suspect.CATEGORIES[kv[0]][1]], -kv[1])):
        lab, conf, fix, _, _ = suspect.CATEGORIES[c]
        out.append({"label": lab, "conf": conf, "count": n, "auto": bool(fix)})
    return out


def do_clean(jid: str, style: bool, keep_hidden: bool, author: str = "", company: str = "") -> dict:
    author, company = clean_identity(author, company)
    d = _dir(jid)
    src = os.path.join(d, "in.docx")
    if not os.path.exists(src):
        raise FileNotFoundError("找不到上傳的檔案")
    out = os.path.join(d, "clean.docx")
    rep = rb.rebuild(src, out, drop_hidden=not keep_hidden, style=style, author=author, company=company)
    finalize.finalize_props(out, author, company)
    after = sc.scan(out, style=style, author=author, company=company)
    diffs = vf.compare(src, out)
    log = {}
    ctx = rep.get("style_ctx")
    if ctx is not None:
        log = {g: {"count": len(items), "examples": ctx.examples(g)} for g, items in ctx.log.items()}
    rv = suspect.analyze(out, SKIP)
    return {"dropped": rep["dropped"], "after": sc.as_dicts(after), "diffs": diffs, "style_log": log,
            "risk_before": _risk(sc.scan(src), suspect.analyze(src, SKIP)), "risk_after": _risk(after, rv)}


def do_review(jid: str, min_conf: str = "中") -> dict:
    """待修版：在 clean.docx 上為需人工處理的段落加註解。"""
    if min_conf not in ("低", "中", "高"):
        raise ValueError("min_conf 不合法")
    d = _dir(jid)
    clean = os.path.join(d, "clean.docx")
    if not os.path.exists(clean):
        raise FileNotFoundError("請先清理")
    return cm.add_comments(clean, os.path.join(d, "review.docx"), min_conf=min_conf, skip_headings=SKIP)


def do_final(jid: str, edited: bytes, style: bool, keep_hidden: bool, author: str = "", company: str = "") -> dict:
    """最終乾淨版：以（人工修改後的）待修版重建，註解整批移除；沒上傳則直接以 clean.docx 重建。"""
    import zipfile
    author, company = clean_identity(author, company)
    d = _dir(jid)
    if edited:
        src = os.path.join(d, "edited.docx")
        with open(src, "wb") as f:
            f.write(edited)
    else:
        src = os.path.join(d, "clean.docx")
        if not os.path.exists(src):
            raise FileNotFoundError("請先清理")
    removed = 0
    with zipfile.ZipFile(src) as z:
        if "word/comments.xml" in z.namelist():
            from lxml import etree
            from .common import TOOL_COMMENT_AUTHOR, NS_W
            removed = sum(1 for c in etree.fromstring(z.read("word/comments.xml"))
                          if c.get("{%s}author" % NS_W) == TOOL_COMMENT_AUTHOR)
    out = os.path.join(d, "final.docx")
    rep = rb.rebuild(src, out, drop_hidden=not keep_hidden, style=style, author=author, company=company)
    finalize.finalize_props(out, author, company)
    after = sc.scan(out, style=style, author=author, company=company)
    rv = suspect.analyze(out, SKIP)
    ctx = rep.get("style_ctx")
    log = {g: {"count": len(items), "examples": ctx.examples(g)} for g, items in ctx.log.items()} if ctx is not None else {}
    return {"dropped": rep["dropped"], "after": sc.as_dicts(after), "diffs": vf.compare(src, out), "style_log": log,
            "removed_comments": removed, "risk_after": _risk(after, rv)}


class Handler(BaseHTTPRequestHandler):
    server_version = "wmr-web"

    def log_message(self, *a):  # 安靜
        pass

    def _host_ok(self) -> bool:
        # 擋 DNS rebinding：只接受 localhost／127.0.0.1
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _send(self, code, body: bytes, ctype="text/html; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, b"forbidden")
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/":
                return self._send(200, PAGE.encode("utf-8"))
            m = re.match(r"^/(report|download)/([0-9a-f]{16})(?:/(clean|review|final))?$", path)
            if m:
                d = _dir(m.group(2))
                if m.group(1) == "report":
                    return self._send(200, open(os.path.join(d, "review.html"), "rb").read())
                kind = m.group(3) or ("final" if os.path.exists(os.path.join(d, "final.docx")) else "clean")
                name = open(os.path.join(d, "name.txt"), encoding="utf-8").read()
                suffix = {"clean": "_clean", "final": "_clean", "review": "_待修版"}[kind]
                base = re.sub(r"\.docx$", "", name, flags=re.I) + suffix + ".docx"
                body = open(os.path.join(d, kind + ".docx"), "rb").read()
                return self._send(200, body, "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                  {"Content-Disposition": "attachment; filename*=UTF-8''" + urllib.parse.quote(base)})
        except (OSError, ValueError):
            return self._send(404, b"not found")
        self._send(404, b"not found")

    def do_POST(self):
        if not self._host_ok():
            return self._send(403, b"forbidden")
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BYTES:
                return self._json({"error": "檔案過大（上限 100MB）"}, 413)
            body = self.rfile.read(n)
            if u.path == "/api/upload":
                name = q.get("name", ["document.docx"])[0]
                if not body[:2] == b"PK":
                    return self._json({"error": "這不是 .docx 檔（不是 zip 格式）"}, 400)
                return self._json(do_upload(body, name))
            if u.path == "/api/clean":
                flag = lambda k: q.get(k, ["0"])[0] == "1"
                return self._json(do_clean(q.get("id", [""])[0], flag("style"), flag("keep_hidden"),
                                           q.get("author", [""])[0], q.get("company", [""])[0]))
            if u.path == "/api/review":
                return self._json(do_review(q.get("id", [""])[0], q.get("min_conf", ["中"])[0]))
            if u.path == "/api/final":
                flag = lambda k: q.get(k, ["0"])[0] == "1"
                if body and body[:2] != b"PK":
                    return self._json({"error": "這不是 .docx 檔（不是 zip 格式）"}, 400)
                return self._json(do_final(q.get("id", [""])[0], body, flag("style"), flag("keep_hidden"),
                                           q.get("author", [""])[0], q.get("company", [""])[0]))
        except Exception as e:  # 回報給使用者而不是讓連線斷掉
            return self._json({"error": "%s: %s" % (type(e).__name__, e)}, 500)
        self._send(404, b"not found")


def serve(port: int = 8765, clear: bool = True) -> None:
    if clear and os.path.isdir(ROOT):
        shutil.rmtree(ROOT)       # 上次的暫存檔含使用者文件，啟動時清掉
    os.makedirs(ROOT, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print("本機網頁介面：http://127.0.0.1:%d/ （僅本機可連；Ctrl+C 結束）" % port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


PAGE = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html"), encoding="utf-8").read()
