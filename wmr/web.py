"""網頁介面（預設只綁 127.0.0.1；標準庫，無額外相依）。

部署時以環境變數設定（皆可選）：
  WMR_ALLOWED_HOSTS  額外允許的 Host（逗號分隔，例如 myapp.azurewebsites.net）
  WMR_MAX_MB         單檔上限（預設 100）
  WMR_TTL_MIN        暫存檔保留分鐘數（預設 0＝不自動清除）
  WMR_MAX_CONCURRENT 同時處理上限（預設 2）
  WMR_DATA_DIR       暫存資料夾（預設 out/web）
  WMR_PASSWORD       設定後啟用共用密碼登入（未設定＝不需登入）
  WMR_SECRET         簽 cookie 的密鑰（預設由密碼衍生）

流程：上傳 docx → 檢測＋可疑部位統計 → 按下清理 → 下載。
清理使用的仍是純本地的 rebuild／finalize，不呼叫任何 AI 或 Word，文件不會離開伺服器。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .common import AUTHOR, COMPANY, clean_identity
from . import comments as cm, finalize, rebuild as rb, report, scan as sc, suspect, verify as vf

def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, default)))
    except ValueError:
        return default


ROOT = os.environ.get("WMR_DATA_DIR") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out", "web")
MAX_BYTES = _env_int("WMR_MAX_MB", 100) * 1024 * 1024
TTL_SECONDS = _env_int("WMR_TTL_MIN", 0) * 60
ALLOWED_HOSTS = {"127.0.0.1", "localhost"} | {h.strip().lower() for h in os.environ.get("WMR_ALLOWED_HOSTS", "").split(",") if h.strip()}
PASSWORD = os.environ.get("WMR_PASSWORD", "")
_BUSY = threading.BoundedSemaphore(max(1, _env_int("WMR_MAX_CONCURRENT", 2)))
SAFE_ERRORS = (ValueError, FileNotFoundError)
_ID = re.compile(r"^[0-9a-f]{16}$")
SKIP = ("參考文獻", "參考資料", "References", "Bibliography")


def _dir(jid: str) -> str:
    if not _ID.match(jid or ""):
        raise ValueError("bad id")
    d = os.path.join(ROOT, jid)
    if os.path.isdir(d):
        try:
            os.utime(d, None)        # 每次存取都續期：TTL 從「最後一次操作」起算
        except OSError:
            pass
    return d


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


def _pain(q):
    v = q.get("pain", [""])[0]
    try:
        return float(v) if v != "" else None
    except ValueError:
        raise ValueError("認捐額度不合法")


def do_clean(jid: str, style: bool, keep_hidden: bool, author: str = "", company: str = "", pain=None) -> dict:
    author, company = clean_identity(author, company)
    d = _dir(jid)
    src = os.path.join(d, "in.docx")
    if not os.path.exists(src):
        raise FileNotFoundError("找不到上傳的檔案")
    out = os.path.join(d, "clean.docx")
    rep = rb.rebuild(src, out, drop_hidden=not keep_hidden, style=style, author=author, company=company)
    finalize.finalize_props(out, author, company, pain)
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


def do_final(jid: str, edited: bytes, style: bool, keep_hidden: bool, author: str = "", company: str = "",
             name: str = "", pain=None) -> dict:
    """最終乾淨版：以（人工修改後的）待修版重建，註解整批移除；沒上傳則直接以 clean.docx 重建。

    有上傳修改版時不依賴先前的工作資料夾：編號空白或已過期，就建立新的資料夾（關掉網頁後也能從這一步接續）。"""
    import zipfile
    author, company = clean_identity(author, company)
    if edited and not _ID.match(jid or ""):
        jid = secrets.token_hex(8)
    d = _dir(jid)
    if edited:
        os.makedirs(d, exist_ok=True)
        nm = os.path.join(d, "name.txt")
        if not os.path.exists(nm):
            with open(nm, "w", encoding="utf-8") as f:
                f.write((os.path.basename(name) or "document.docx")[:200])
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
    finalize.finalize_props(out, author, company, pain)
    after = sc.scan(out, style=style, author=author, company=company)
    rv = suspect.analyze(out, SKIP)
    ctx = rep.get("style_ctx")
    log = {g: {"count": len(items), "examples": ctx.examples(g)} for g, items in ctx.log.items()} if ctx is not None else {}
    return {"id": jid, "dropped": rep["dropped"], "after": sc.as_dicts(after), "diffs": vf.compare(src, out), "style_log": log,
            "removed_comments": removed, "risk_after": _risk(after, rv)}


def _token() -> str:
    key = (os.environ.get("WMR_SECRET") or PASSWORD).encode("utf-8")
    return hmac.new(hashlib.sha256(key).digest(), b"wmr-auth-v1", hashlib.sha256).hexdigest()


LOGIN_PAGE = ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'><title>登入</title>"
              "<body style='font:16px -apple-system,sans-serif;background:#2b2d42;color:#fff;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0'>"
              "<form method=post action=/login style='background:#fff;color:#222;padding:28px;border-radius:12px;width:280px'>"
              "<h2 style='margin:0 0 12px'>斧大醫院・掛號前驗證</h2>%s<input type=password name=password autofocus placeholder='密碼' "
              "style='width:100%%;padding:8px;box-sizing:border-box;margin:8px 0'><button style='width:100%%;padding:8px'>進入</button></form>")


def purge_expired(now=None) -> int:
    """刪除超過 TTL 的暫存工作資料夾；TTL=0 時不做事。回傳刪除數。"""
    if not TTL_SECONDS or not os.path.isdir(ROOT):
        return 0
    now = now or time.time()
    n = 0
    for name in os.listdir(ROOT):
        d = os.path.join(ROOT, name)
        if _ID.match(name) and os.path.isdir(d) and now - os.path.getmtime(d) > TTL_SECONDS:
            shutil.rmtree(d, ignore_errors=True)
            n += 1
    return n


class Handler(BaseHTTPRequestHandler):
    server_version = "wmr-web"

    def log_message(self, *a):  # 安靜
        pass

    def _host_ok(self) -> bool:
        # 擋 DNS rebinding：只接受 localhost／127.0.0.1 與 WMR_ALLOWED_HOSTS
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        return host in ALLOWED_HOSTS

    def _authed(self) -> bool:
        if not PASSWORD:
            return True
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "wmr_auth" and hmac.compare_digest(v, _token()):
                return True
        return False

    def _send(self, code, body: bytes, ctype="text/html; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Robots-Tag", "noindex, nofollow")
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
        if path == "/robots.txt":
            return self._send(200, b"User-agent: *\nDisallow: /\n", "text/plain; charset=utf-8")
        if not self._authed():
            if path.startswith("/api/"):
                return self._json({"error": "請先登入"}, 401)
            return self._send(200, (LOGIN_PAGE % "").encode("utf-8"))
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
        if u.path == "/login":
            return self._login()
        if not self._authed():
            return self._json({"error": "請先登入"}, 401)
        if not _BUSY.acquire(blocking=False):
            return self._json({"error": "伺服器忙碌中，請稍後再試"}, 503)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BYTES:
                return self._json({"error": "檔案過大（上限 %d MB）" % (MAX_BYTES // 1048576)}, 413)
            body = self.rfile.read(n)
            purge_expired()
            if u.path == "/api/upload":
                name = q.get("name", ["document.docx"])[0]
                if not body[:2] == b"PK":
                    return self._json({"error": "這不是 .docx 檔（不是 zip 格式）"}, 400)
                return self._json(do_upload(body, name))
            if u.path == "/api/clean":
                flag = lambda k: q.get(k, ["0"])[0] == "1"
                return self._json(do_clean(q.get("id", [""])[0], flag("style"), flag("keep_hidden"),
                                           q.get("author", [""])[0], q.get("company", [""])[0], pain=_pain(q)))
            if u.path == "/api/review":
                return self._json(do_review(q.get("id", [""])[0], q.get("min_conf", ["中"])[0]))
            if u.path == "/api/final":
                flag = lambda k: q.get(k, ["0"])[0] == "1"
                if body and body[:2] != b"PK":
                    return self._json({"error": "這不是 .docx 檔（不是 zip 格式）"}, 400)
                return self._json(do_final(q.get("id", [""])[0], body, flag("style"), flag("keep_hidden"),
                                           q.get("author", [""])[0], q.get("company", [""])[0], q.get("name", [""])[0], _pain(q)))
        except SAFE_ERRORS as e:
            msg = str(e)
            if isinstance(e, FileNotFoundError) and TTL_SECONDS:
                msg = "工作已過期（閒置超過 %d 分鐘會清除暫存）。若已有改好的待修版，可回首頁點「直接去收費處」" % (TTL_SECONDS // 60)
            return self._json({"error": msg}, 500)
        except Exception as e:  # 不把內部路徑等細節回給使用者
            print("wmr web error:", type(e).__name__, e, file=sys.stderr)
            return self._json({"error": "處理失敗（%s）" % type(e).__name__}, 500)
        finally:
            _BUSY.release()
        self._send(404, b"not found")

    def _login(self):
        if not PASSWORD:
            return self._send(404, b"not found")
        n = min(int(self.headers.get("Content-Length") or 0), 4096)
        form = urllib.parse.parse_qs(self.rfile.read(n).decode("utf-8", "ignore"))
        pw = (form.get("password") or [""])[0]
        if hmac.compare_digest(hashlib.sha256(pw.encode()).digest(), hashlib.sha256(PASSWORD.encode()).digest()):
            secure = "; Secure" if (self.headers.get("X-Forwarded-Proto") or "").lower() == "https" else ""
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", "wmr_auth=%s; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800%s" % (_token(), secure))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        time.sleep(1)   # 減緩暴力猜測
        self._send(401, (LOGIN_PAGE % "<p style='color:#c00'>密碼不正確</p>").encode("utf-8"))


def serve(port: int = 8765, clear: bool = True, host: str = "127.0.0.1") -> None:
    if clear and os.path.isdir(ROOT):
        shutil.rmtree(ROOT)       # 上次的暫存檔含使用者文件，啟動時清掉
    os.makedirs(ROOT, exist_ok=True)
    if TTL_SECONDS:
        def _loop():
            while True:
                time.sleep(60)
                try:
                    purge_expired()
                except OSError:
                    pass
        threading.Thread(target=_loop, daemon=True).start()
    srv = ThreadingHTTPServer((host, port), Handler)
    loop = host in ("127.0.0.1", "localhost")
    print("網頁介面：http://%s:%d/ （%s；Ctrl+C 結束）" % (host, port, "僅本機可連" if loop else "對外開放"))
    if not loop and not PASSWORD:
        print("警告：未設定 WMR_PASSWORD，任何知道網址的人都能上傳與下載文件。", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


PAGE = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "index.html"), encoding="utf-8").read()
