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
from . import finalize, rebuild as rb, report, scan as sc, suspect, verify as vf

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
            "defaults": {"author": AUTHOR, "company": COMPANY}}


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
    return {"dropped": rep["dropped"], "after": sc.as_dicts(after), "diffs": diffs, "style_log": log}


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
            m = re.match(r"^/(report|download)/([0-9a-f]{16})$", path)
            if m:
                d = _dir(m.group(2))
                if m.group(1) == "report":
                    return self._send(200, open(os.path.join(d, "review.html"), "rb").read())
                name = open(os.path.join(d, "name.txt"), encoding="utf-8").read()
                base = re.sub(r"\.docx$", "", name, flags=re.I) + "_clean.docx"
                body = open(os.path.join(d, "clean.docx"), "rb").read()
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


PAGE = r"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Watermark Remover</title>
<style>
:root{--bg:#fafaf7;--fg:#222;--mut:#777;--card:#fff;--bd:#ddd;--acc:#2f5d8a;--hi:#b3261e;--md:#a86400;--lo:#666;--ok:#1b7a3d}
@media(prefers-color-scheme:dark){:root{--bg:#1b1b1a;--fg:#e8e6df;--mut:#9a9890;--card:#252523;--bd:#3a3a37;--acc:#7fb0e0;--hi:#ff8a80;--md:#ffc266;--lo:#aaa;--ok:#6fd08c}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"PingFang TC",sans-serif}
main{max-width:860px;margin:0 auto;padding:24px 16px 64px}h1{font-size:20px;margin:0 0 4px}.mut{color:var(--mut);font-size:13px}
.card{background:var(--card);border:1px solid var(--bd);border-radius:8px;padding:16px;margin:16px 0}
#drop{border:2px dashed var(--bd);text-align:center;padding:36px 12px;cursor:pointer}#drop.over{border-color:var(--acc)}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{border-bottom:1px solid var(--bd);padding:4px 8px;text-align:left;vertical-align:top}
.high{color:var(--hi)}.medium{color:var(--md)}.low{color:var(--lo)}.ok{color:var(--ok)}
button,a.btn{background:var(--acc);color:#fff;border:0;border-radius:6px;padding:8px 16px;font-size:14px;cursor:pointer;text-decoration:none;display:inline-block}
button:disabled{opacity:.5}label{display:block;margin:6px 0}h2{font-size:16px;margin:0 0 8px}.hide{display:none}
</style></head><body><main>
<h1>Watermark Remover</h1><div class="mut">本機自用 · 檔案只在這台電腦處理 · 清理步驟為純本地腳本，不呼叫 AI</div>
<div class="card" id="drop">拖曳 .docx 到這裡，或點此選擇檔案<input id="file" type="file" accept=".docx" class="hide"></div>
<div id="msg" class="mut"></div>
<div id="step2" class="hide">
 <div class="card"><h2>1. 檔案層檢測 <span id="fname" class="mut"></span></h2><div id="findings"></div></div>
 <div class="card"><h2>2. 可疑部位（文字層）</h2><div id="cats"></div>
  <p><a id="rep" target="_blank" class="btn">開啟完整報告（含位置與 Word 搜尋字串）</a></p>
  <div class="mut">信心等級是啟發式；「需人工」的類型工具不會自動改，請依報告逐一處理。</div></div>
 <div class="card"><h2>3. 清理</h2>
  <label><input type="checkbox" id="style"> 啟用文字風格層（規則式替換，選用；不處理參考文獻）</label>
  <label><input type="checkbox" id="keep"> 保留隱藏文字（預設會刪除）</label>
  <label>作者／最後修改者 <input id="author" type="text" maxlength="100" style="width:16em"></label>
  <label>公司 <input id="company" type="text" maxlength="100" style="width:16em"></label>
  <div class="mut">留空則使用預設值（即欄位中的初始內容）。</div>
  <button id="go">清理並產生新檔</button></div>
</div>
<div id="step3" class="hide"><div class="card"><h2>結果</h2><div id="res"></div></div></div>
<script>
const $=id=>document.getElementById(id);let jid=null;
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
function ftable(a){if(!a.length)return '<span class="ok">未發現痕跡</span>';
 return '<table><tr><th>嚴重度</th><th>層</th><th>項目</th><th>位置／說明</th></tr>'+a.map(f=>`<tr><td class="${f.severity}">${f.severity}</td><td>${esc(f.layer)}</td><td>${esc(f.code)}</td><td>${esc(f.where)} ${esc(f.detail)}</td></tr>`).join('')+'</table>'}
async function upload(file){
 $('msg').textContent='分析中…（大檔可能要數十秒）';$('step3').classList.add('hide');
 const r=await fetch('/api/upload?name='+encodeURIComponent(file.name),{method:'POST',body:file});const j=await r.json();
 if(j.error){$('msg').textContent='錯誤：'+j.error;return}
 jid=j.id;if(!$('author').value)$('author').value=j.defaults.author;if(!$('company').value)$('company').value=j.defaults.company;$('msg').textContent='';$('fname').textContent=j.name;$('findings').innerHTML=ftable(j.findings);
 const tot=j.categories.reduce((s,c)=>s+c.count,0);
 $('cats').innerHTML=j.categories.length?`<p>共 ${tot} 處</p><table><tr><th>信心</th><th>類型</th><th>數量</th><th>處理</th></tr>`+j.categories.map(c=>`<tr><td>${c.conf}</td><td>${esc(c.label)}</td><td>${c.count}</td><td>${c.auto?'可自動修':'需人工'}</td></tr>`).join('')+'</table>':'<span class="ok">沒有可疑部位</span>';
 $('rep').href='/report/'+jid;$('step2').classList.remove('hide')}
async function clean(){
 $('go').disabled=true;$('go').textContent='處理中…';
 const r=await fetch(`/api/clean?id=${jid}&style=${$('style').checked?1:0}&keep_hidden=${$('keep').checked?1:0}&author=${encodeURIComponent($('author').value)}&company=${encodeURIComponent($('company').value)}`,{method:'POST'});const j=await r.json();
 $('go').disabled=false;$('go').textContent='清理並產生新檔';
 if(j.error){$('msg').textContent='錯誤：'+j.error;return}
 let h=`<p><a class="btn" href="/download/${jid}">下載清理後的 .docx</a></p><h2>清理後複掃</h2>`+ftable(j.after);
 h+='<h2>保真比對（原檔 vs 輸出）</h2>'+(j.diffs.length?'<ul>'+j.diffs.map(d=>`<li>${esc(d)}</li>`).join('')+'</ul>'+($('style').checked?'<div class="mut">已啟用風格層：文字差異為預期，請只看表格、圖片。</div>':''):'<span class="ok">段落／樣式／表格／圖片一致</span>');
 const sl=Object.entries(j.style_log||{});if(sl.length)h+='<h2>風格層變更</h2><table>'+sl.map(([g,v])=>`<tr><td>${esc(g)}</td><td>${v.count} 處</td><td>${esc((v.examples||[]).join('；'))}</td></tr>`).join('')+'</table>';
 h+=`<p class="mut">已丟棄部件：${esc(j.dropped.join(', ')||'無')}。作者／公司已設為 ${esc($('author').value||'預設')}／${esc($('company').value||'預設')}。建議用 Word 開啟確認版面。</p>`;
 $('res').innerHTML=h;$('step3').classList.remove('hide')}
$('drop').onclick=()=>$('file').click();$('file').onchange=e=>e.target.files[0]&&upload(e.target.files[0]);
$('drop').ondragover=e=>{e.preventDefault();$('drop').classList.add('over')};$('drop').ondragleave=()=>$('drop').classList.remove('over');
$('drop').ondrop=e=>{e.preventDefault();$('drop').classList.remove('over');e.dataTransfer.files[0]&&upload(e.dataTransfer.files[0])};
$('go').onclick=clean;
</script></main></body></html>"""
