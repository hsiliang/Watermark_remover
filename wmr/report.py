"""把 Review 轉成自包含的 HTML 報告（本機開啟即可，無外部資源）。"""
from __future__ import annotations

import datetime as _dt
import html
import json
from collections import defaultdict

from .suspect import CATEGORIES, CONF_WEIGHT, Review

COLORS = {"yellow": ("黃", "#fff176"), "cyan": ("青", "#80deea"), "green": ("綠", "#a5d6a7"),
          "magenta": ("粉紅", "#f48fb1"), "red": ("紅", "#ef9a9a"), "blue": ("藍", "#90caf9"),
          "darkYellow": ("深黃", "#c9a400"), "lightGray": ("淺灰", "#e0e0e0"), "darkCyan": ("深青", "#26a69a")}

CSS = """
:root{--bg:#fff;--fg:#1d2127;--mut:#5d6674;--line:#dde1e7;--card:#f6f8fa;--acc:#0b5cad}
@media(prefers-color-scheme:dark){:root{--bg:#14171c;--fg:#e6e9ee;--mut:#9aa5b4;--line:#2b323c;--card:#1b2027;--acc:#6ea8fe}}
body{background:var(--bg);color:var(--fg);font:15px/1.6 -apple-system,"PingFang TC","Noto Sans TC",sans-serif;margin:0;padding:24px 16px}
main{max-width:980px;margin:0 auto}h1{font-size:1.5rem;margin:.2em 0}h2{font-size:1.15rem;margin:1.6em 0 .5em;border-bottom:1px solid var(--line);padding-bottom:.3em}
.mut{color:var(--mut);font-size:.9rem}table{border-collapse:collapse;width:100%;font-size:.92rem}
th,td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}th{color:var(--mut);font-weight:600}
.warn{background:var(--card);border-left:4px solid var(--acc);padding:10px 14px;border-radius:4px}
.chip{display:inline-block;padding:0 8px;border-radius:10px;font-size:.8rem;border:1px solid var(--line)}
.c-高{background:#ffcdd2;color:#7f0000}.c-中{background:#ffe0b2;color:#5d3500}.c-低{background:#e0e0e0;color:#333}
details{background:var(--card);border:1px solid var(--line);border-radius:6px;margin:.6em 0;padding:.2em .8em}
summary{cursor:pointer;padding:.5em 0;font-weight:600}.item{border-top:1px solid var(--line);padding:.5em 0}
.where{color:var(--mut);font-size:.85rem}mark{padding:0 2px;border-radius:2px;color:#000}
code.key{background:var(--bg);border:1px solid var(--line);padding:1px 6px;border-radius:4px;cursor:pointer;font-size:.85rem}
code.key:hover{border-color:var(--acc)}.hint{color:var(--mut);font-size:.88rem;margin:.2em 0 .6em}
.sw{display:inline-block;width:14px;height:14px;border-radius:3px;vertical-align:-2px;margin-right:4px}
"""

JS = """
document.addEventListener('click',function(e){var k=e.target.closest('code.key');if(!k)return;
var t=k.getAttribute('data-k');(navigator.clipboard?navigator.clipboard.writeText(t):Promise.reject()).then(function(){
var o=k.textContent;k.textContent='已複製 ✓';setTimeout(function(){k.textContent=o},900)}).catch(function(){
var r=document.createRange();r.selectNodeContents(k);var s=getSelection();s.removeAllRanges();s.addRange(r)})});
"""


def _e(s):
    return html.escape(str(s))


def _mark(snippet: str, key: str, color: str) -> str:
    """在片段中標出命中字詞（以 key 去掉前後補字的核心近似定位）。"""
    return _e(snippet)


def build_html(rv: Review, title: str) -> str:
    rows = []
    para_by = defaultdict(set)
    for f in rv.flags:
        para_by[f.cat].add(f.para)
    order = sorted(rv.by_cat, key=lambda c: (-CONF_WEIGHT[CATEGORIES[c][1]], -rv.by_cat[c]))
    for c in order:
        label, conf, fix, _c, _ = CATEGORIES[c]
        color = next((f.color for f in rv.flags if f.cat == c), _c)
        rows.append("<tr><td><span class='sw' style='background:%s'></span>%s</td><td><span class='chip c-%s'>%s</span></td>"
                    "<td><b>%d</b></td><td>%d</td><td>%s</td></tr>" % (
                        COLORS[color][1], _e(label), conf, conf, rv.by_cat[c], len(para_by[c]), "是" if fix else "否，需人工"))
    total = sum(rv.by_cat.values())
    unfixable = sum(n for c, n in rv.by_cat.items() if not CATEGORIES[c][2])
    metrics = "".join("<tr><td>%s</td><td>%s</td></tr>" % (_e(k), _e(v)) for k, v in rv.metrics.items())
    hot = "".join(
        "<tr><td>%d</td><td class='where'>%s</td><td>%s</td><td>%s</td><td><code class='key' data-k=\"%s\" title='點一下複製'>%s</code></td></tr>" % (
            h["para"], _e(h["heading"] or "—"), _e("、".join(h["cats"])), _e(h["preview"]), _e(h["key"]), _e(h["key"]))
        for h in rv.hotspots)
    by_color = defaultdict(list)
    for c in order:
        col = next((f.color for f in rv.flags if f.cat == c), None)
        if col:
            by_color[col].append(CATEGORIES[c][0])
    legend = "".join("<div><span class='sw' style='background:%s'></span><b>%s</b>：%s</div>" % (
        COLORS[col][1], COLORS[col][0], _e("、".join(labels))) for col, labels in by_color.items())
    details = []
    for c in order:
        label, conf, fix, color, hint = CATEGORIES[c]
        items = []
        for f in [x for x in rv.flags if x.cat == c]:
            items.append(
                "<div class='item'><div class='where'>%s · 第 %d 段</div><div>%s</div>"
                "<div class='where'>Word 搜尋字串：<code class='key' data-k=\"%s\" title='點一下複製'>%s</code>%s</div></div>" % (
                    _e(f.heading or "（章節外）"), f.para, _mark(f.snippet, f.key, f.color), _e(f.key), _e(f.key),
                    (" · " + _e(f.note.replace(hint, "").strip())) if f.note.replace(hint, "").strip() else ""))
        details.append("<details><summary><span class='chip c-%s'>%s</span> %s　%d 處</summary><div class='hint'>%s</div>%s</details>" % (
            conf, conf, _e(label), rv.by_cat[c], _e(hint), "".join(items)))
    now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    return ("<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>可疑部位報告</title><style>%s</style></head><body><main>"
            "<h1>可疑部位報告</h1><div class='mut'>%s · 產生於 %s</div>"
            "<p class='warn'>這些是<b>啟發式訊號</b>，不是判決。研究與維基百科指南都指出：單一特徵不能定性為 AI 寫作，"
            "且學術、非母語或公文式寫作本來就容易出現類似特徵；要多項密集同時出現才有意義。"
            "「信心」是指該訊號像 AI 的程度，不是工具對該段的確定度。</p>"
            "<h2>總覽：共 %d 處，其中 %d 處需要人工判斷</h2>"
            "<table><tr><th>類別</th><th>信心</th><th>數量</th><th>涉及段落</th><th>可自動修</th></tr>%s</table>"
            "<h2>熱點段落（先改這些）</h2><p class='mut'>依各類訊號加權排序；點「搜尋字串」可複製，到 Word 以 ⌘F 貼上即可定位。</p>"
            "<table><tr><th>段</th><th>章節</th><th>類別</th><th>內容開頭</th><th>搜尋字串</th></tr>%s</table>"
            "<h2>逐項清單</h2><p class='mut'>標註副本的螢光筆顏色對照（已避開原文件既有的顏色）：</p><div class='mut'>%s</div>%s"
            "<h2>全文指標</h2><table>%s</table>"
            "<p class='mut'>依據：維基百科〈Signs of AI writing〉、Kobak 等人 2024（arXiv:2406.07016）、burstiness 相關偵測研究、"
            "中文經驗性文章。門檻皆為啟發式；句長均勻度採「本文內相對排名」而非絕對數值。</p>"
            "<script>%s</script></main></body></html>") % (
        CSS, _e(title), now, total, unfixable, "".join(rows), hot, legend, "".join(details), metrics, JS)


def build_json(rv: Review) -> str:
    return json.dumps(rv.to_dict(), ensure_ascii=False, indent=2)
