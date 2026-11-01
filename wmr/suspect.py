"""可疑部位分析（review）。

與 style.py 不同：這裡的訊號「可能」是 AI 寫作的痕跡，但無法確定、也無法機械式改寫。
工具只統計、分類、標出位置，交給人判斷與修改。

依據（皆為經驗性訊號，單一項目都不能定性，須多項同時密集才有意義）：
- 維基百科 Signs of AI writing：否定排比、三段式、模糊引用、套話、粗體標籤、標題公式
- Kobak et al. 2024（arXiv:2406.07016）：LLM 過度用詞（delves、underscores、showcasing…）
- 偵測研究常用的 burstiness（句長變異）與標點／連接詞節奏
- 中文經驗文章：連接詞堆積、排比、空洞大詞、翻譯腔、簡繁／兩岸用語混用
門檻皆為啟發式，非經驗證的標準；句長均勻度採「文件內相對排名」以避免依賴未經證實的絕對數值。
"""
from __future__ import annotations

import re
import statistics
import zipfile
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

from lxml import etree

from .common import NS_W
from .style import (
    SIMPLIFIED_CHARS, REGION, _ART_RE, _DASH_CJK, _EMOJI_RE, _FILLER_RE, _LEX_RE, _MD_BOLD, _MD_HEAD,
    _REGION_RE, is_traditional,
)

W = "{%s}" % NS_W
CONF_WEIGHT = {"高": 3, "中": 2, "低": 1}

# 類別代碼 → (中文名稱, 預設信心, 可自動修, 標註色, 修改建議)
CATEGORIES: Dict[str, tuple] = {
    "artefact":      ("AI 工具殘留標記", "高", True,  "red",       "已由 --style 自動移除；若仍出現請手動刪除。"),
    "simplified":    ("簡體字混入", "高", False, "magenta",   "繁體文件中出現簡體字，請手動轉換（可用 Word 校閱→中文繁簡轉換）。"),
    "region":        ("兩岸用語混用", "中", True,  "magenta",   "已由 --style 自動轉成台灣用語。"),
    "filler":        ("套話開頭", "中", True,  "yellow",    "多半可直接刪除，不影響句意。"),
    "lexicon":       ("AI 慣用語", "中", True,  "yellow",    "已由 --style 替換；亦可依上下文改用你自己的說法。"),
    "bigword":       ("空洞大詞", "中", False, "yellow",    "試著刪掉這個詞，句意若不變就代表它只是裝飾；或換成具體的做法／對象／數據。"),
    "dash":          ("破折號", "低", True,  "yellow",    "已由 --style 改為逗號；中文學術文較少用破折號。"),
    "neg_parallel":  ("否定排比（不是…而是…）", "中", False, "cyan",      "句式常是 AI 的招牌。改成直述句，或只保留真正需要對比的那一次。"),
    "triad":         ("首先／其次／最後 三段式", "低", False, "cyan",      "若不是真有三個並列論點，改成自然的段落銜接；不必每次都列點。"),
    "vague_attr":    ("模糊引用（無出處）", "中", False, "green",     "『研究顯示／相關研究指出』沒有指名來源：補上具體作者與年份，或刪掉這句主張。"),
    "connector":     ("連接詞密集", "中", False, "cyan",      "多數句子都以連接詞開頭。刪掉其中幾個，讓句子靠內容銜接，或合併相鄰句。"),
    "summary_tail":  ("段尾總結句", "低", False, "cyan",      "段落最後一句只是在重複前文（綜上所述…）。可刪除或改成引出下一段的句子。"),
    "bracket_eg":    ("括號舉例（如…）密集", "低", False, "cyan",      "同一段多次用（如…）舉例，改成正文敘述或只留一個最好的例子。"),
    "uniform_sent":  ("句長過於均勻", "中", False, "cyan",      "句子長度太一致。把兩個短句合併，或把一個長句拆開，製造長短交錯。"),
    "de_heavy":      ("「的」字密度偏高", "低", False, "cyan",      "可能是翻譯腔：長串「…的…的…的」可拆句或改用動詞。"),
    "nominal":       ("名詞化動詞（進行／予以／加以）", "低", False, "cyan",      "『進行分析』→『分析』。台灣學術文常見，但密集時像翻譯腔。"),
    "para_open":     ("連續段落開頭雷同", "低", False, "cyan",      "連續幾段用同樣的字開頭，改變其中幾段的切入方式。"),
    "bold_label":    ("粗體標籤開頭", "中", False, "green",     "『**重點**：說明』是聊天機器人清單的樣式；改成一般句子或取消粗體。"),
    "bold_heavy":    ("粗體過多", "低", False, "green",     "整段幾乎都是粗體；只保留真正需要強調的詞。"),
    "heading_pair":  ("標題公式（A與B）", "低", False, "green",     "『挑戰與展望』之類的公式化標題，可改成更具體的標題。"),
    "heading_rule3": ("章節結構總是三個小節", "低", False, "green",     "多數大節都剛好三個小節（三段式結構）。依內容需要增減或合併小節，讓結構不那麼整齊。"),
    "restate":       ("重述／銜接句（換言之、這意味著…）", "低", False, "cyan",      "『換言之／這也是為什麼／更重要的是』常用來重複前一句。刪掉常不影響句意。"),
    "opener":        ("套式開頭（隨著…的發展／在當今…）", "低", False, "cyan",      "『隨著X的快速發展』是很常見的 AI 開場。直接從具體事實或問題開始。"),
    "triple_list":   ("三項並列密集", "低", False, "cyan",      "同一段多句都是『A、B與C』三項並列。保留真正三項的，其餘改成兩項或四項、或改寫句型。"),
    "generic":       ("內容空泛（缺少具體資訊）", "低", False, "green",     "整段幾乎沒有數字、專有名詞、出處或實例。網路上的經驗文章常建議補入具體內容，但本專案的小規模實驗中，少量補入對檢測器分數幾乎沒有影響（見 docs/experiments.md）；仍建議你補上自己查證過的資料，主要是為了內容品質，而非降低檢測分數。工具無法替你編造事實。"),
    "quote_mixed":   ("引號樣式混用", "低", False, "green",     "全文引號慣例不一致（「」與“”混用），請統一。"),
}

CONNECTORS = ("此外", "另外", "然而", "但是", "不過", "因此", "所以", "同時", "換言之", "換句話說", "具體而言", "進一步",
              "更重要的是", "綜上", "總之", "首先", "其次", "再者", "最後", "最終", "接著", "而且", "並且", "由此可見",
              "可見", "基於此", "在此基礎上", "與此同時", "整體而言", "整體來看")
BIGWORDS = ("賦能", "重塑", "深度融合", "深度整合", "全方位", "多維度", "助力", "打造", "創新驅動", "生態系統", "閉環", "抓手",
            "底層邏輯", "範式轉移", "顛覆性", "里程碑", "劃時代", "前所未有", "蓬勃發展", "方興未艾", "日新月異", "與日俱增",
            "不可或缺", "舉足輕重")
_BIG_RE = re.compile("|".join(re.escape(w) for w in BIGWORDS) + r"|扮演著?.{0,6}角色|具有重要(?:的)?(?:意義|價值|影響)|奠定了?.{0,4}基礎")
_NEG_RE = re.compile(r"不(?:僅|只(?!一[個項種位])|單|但)[^。！？\n]{1,30}?(?:而且|還|更|也|並且|同時)|並非[^。！？\n]{1,30}?而是|不是[^。！？\n]{1,30}?而是"
                     r"|\bnot (?:just|only|merely)\b[^.\n]{1,60}?\bbut\b", re.I)
_VAGUE_RE = re.compile(r"(?<!本)(?:相關|大量|許多|已有|現有|眾多|多項|多數)?(?<!本)(?:研究|文獻|實證|調查|學者|實踐)(?:均|皆|都|已)?(?:表明|顯示|指出|證明|發現|認為|表示|證實)")
_REF_ENTRY_RE = re.compile(r"[（(](?:19|20)\d{2}[a-z]?[）)]\s*[。.]")
_CITE_RE = re.compile(r"(?:19|20)\d{2}|\[\d+(?:[,，\-–]\d+)*\]")
_TRIAD_RE = re.compile(r"首先[^\n]{0,160}?其次[^\n]{0,160}?(?:最後|再者|再次|此外|另外|最終|第三)|第一[^\n]{0,140}?第二[^\n]{0,140}?第三")
_RESTATE_RE = re.compile(r"換言之|換句話說|也就是說|這意味著|這也說明|這也就是為什麼|這也是為什麼|更重要的是|不可否認|不難看出|由此可見")
_OPENER_RE = re.compile(r"(?:^|[。！？\n])\s*(?:隨著[^，。]{1,18}的(?:發展|推進|普及|加速|快速|興起|進步|成熟|提升|增加)|在當今[^，。]{0,10}|在這樣的背景下|在此背景下|在.{1,10}的浪潮下)")
_TRIPLE_RE = re.compile(r"[^，。；、]{1,12}、[^，。；、]{1,12}(?:與|及|和|以及)[^，。；、]{1,12}")
_TAIL_RE = re.compile(r"^(?:綜上所述|總之|總而言之|整體而言|整體來看|由此可見|簡言之|總的來說|簡單說|in summary|in conclusion|overall)", re.I)
_EG_RE = re.compile(r"[（(]如[^）)]+[）)]")
_NOM_RE = re.compile(r"(?:進行|予以|加以|開展|展開)(?:了|著)?[一-鿿]{1,4}")
_HEADPAIR_RE = re.compile(r"(?:挑戰|展望|機遇|機會|啟示|意義|影響|前景)[與及和]|[與及和](?:挑戰|展望|機遇|機會|啟示|前景)")
_SENT_SPLIT = re.compile(r"(?<=[。！？；!?;])|\n|(?<=[.])\s+(?=[A-Z])")
_SPECIFIC_RE = re.compile(r"\d|[A-Za-z]{2,}|《[^》]+》|第[一二三四五六七八九十百]+[條項款章節]|(?:大學|研究院|委員會|基金會|協會|醫院|公司|衛福部|衛生福利部|勞動部|教育部|行政院|國發會|統計處)")
_SIMPLE_RE = re.compile("[%s]" % SIMPLIFIED_CHARS)


@dataclass
class Para:
    idx: int
    text: str
    heading_path: str
    style: str
    is_heading: bool
    first_bold: bool
    bold_ratio: float
    element: Optional[object] = field(default=None, repr=False, compare=False)
    in_table: bool = False


@dataclass
class Flag:
    cat: str
    label: str
    conf: str
    para: int
    snippet: str
    key: str
    note: str
    fixable: bool
    span: Optional[tuple] = None   # 段落文字內的 (start, end)，供標註用
    heading: str = ""
    color: str = "yellow"


def _style_names(z: zipfile.ZipFile) -> Dict[str, str]:
    try:
        root = etree.fromstring(z.read("word/styles.xml"))
    except KeyError:
        return {}
    return {s.get(W + "styleId"): (s.find(W + "name").get(W + "val") if s.find(W + "name") is not None else "")
            for s in root.iter(W + "style")}


def _in_table(p) -> bool:
    return next(p.iterancestors(W + "tc"), None) is not None


def _mostly_citations(text: str, sents: List[str]) -> bool:
    """文獻清單的句長一致不代表文風：有多筆「作者（年）。」條目，或過半句子含年份／文獻編號。"""
    if len(_REF_ENTRY_RE.findall(text)) >= 2:
        return True
    return bool(sents) and sum(1 for s in sents if _CITE_RE.search(s)) * 2 >= len(sents)


def para_text(p) -> str:
    out = []
    for el in p.iter():
        if el.tag == W + "t" and el.text:
            out.append(el.text)
        elif el.tag == W + "tab":
            out.append("\t")
    return "".join(out)


def _level(style: str, p) -> Optional[int]:
    m = re.search(r"(?:heading|標題)\s*(\d)", style, re.I)
    if m:
        return int(m.group(1))
    ol = p.find("%spPr/%soutlineLvl" % (W, W))
    if ol is not None and ol.get(W + "val", "").isdigit():
        return int(ol.get(W + "val")) + 1
    return None


def reference_paragraph_set(body, names: Dict[str, str], skip_headings) -> set:
    """回傳位於「參考文獻」等章節內的 w:p 元素集合（遇到同級或更高級標題即結束）。"""
    skip, skipping, stack_lvl = set(), None, None
    for p in body.iter(W + "p"):
        sid = p.find("%spPr/%spStyle" % (W, W))
        style = names.get(sid.get(W + "val"), "") if sid is not None else ""
        text = para_text(p)
        lvl = _level(style, p)
        if lvl is not None and text.strip():
            if skipping is not None and lvl <= skipping:
                skipping = None
            if any(h in text for h in skip_headings):
                skipping = lvl
                continue
        if skipping is not None:
            skip.add(p)
    return skip


def extract_paragraphs(path: str, skip_headings=("參考文獻", "參考資料", "References", "Bibliography")) -> List[Para]:
    with zipfile.ZipFile(path) as z:
        names = _style_names(z)
        body = etree.fromstring(z.read("word/document.xml"))
    paras: List[Para] = []
    path_stack: List[tuple] = []
    skipping_level: Optional[int] = None
    n = 0
    for p in body.iter(W + "p"):
        sid = p.find("%spPr/%spStyle" % (W, W))
        style = names.get(sid.get(W + "val"), "") if sid is not None else ""
        text = para_text(p)
        low = style.lower()
        if low.startswith(("toc", "目錄", "table of")) or any((i.text or "").strip().startswith(("PAGEREF", "TOC")) for i in p.iter(W + "instrText")):
            continue
        if not text.strip():
            continue
        lvl = _level(style, p)
        is_h = lvl is not None
        if is_h:
            while path_stack and path_stack[-1][0] >= lvl:
                path_stack.pop()
            path_stack.append((lvl, text.strip()))
            if skipping_level is not None and lvl <= skipping_level:
                skipping_level = None
            if any(text.strip().startswith(h) or h in text for h in skip_headings):
                skipping_level = lvl
        if skipping_level is not None:
            continue
        bold_chars = tot = 0
        first_bold = False
        for i, r in enumerate(p.iter(W + "r")):
            t = "".join(x.text or "" for x in r.iter(W + "t"))
            if not t:
                continue
            b = r.find("%srPr/%sb" % (W, W))
            is_b = b is not None and b.get(W + "val", "1") not in ("0", "false")
            if tot == 0:
                first_bold = is_b
            tot += len(t)
            bold_chars += len(t) if is_b else 0
        n += 1
        paras.append(Para(n, text, " > ".join(h for _, h in path_stack), style, is_h,
                          first_bold, (bold_chars / tot) if tot else 0.0, p, _in_table(p)))
    return paras


def sentences(text: str) -> List[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text) if s and s.strip()]


def slen(s: str) -> int:
    return len(re.sub(r"\s", "", s))


def _cv(xs: List[int]) -> float:
    return (statistics.pstdev(xs) / statistics.mean(xs)) if xs and statistics.mean(xs) else 0.0


def _snip(text: str, a: int, b: int, ctx: int = 12) -> str:
    s = text[max(0, a - ctx):b + ctx].replace("\n", " ").replace("\t", " ")
    return ("…" if a - ctx > 0 else "") + s + ("…" if b + ctx < len(text) else "")


def _key(text: str, a: int, b: int) -> str:
    """Word 搜尋用字串：命中字詞前後各補一點，避開換行／tab，長度 ≤ 30。"""
    k = text[max(0, a - 3):b + 3]
    k = re.split(r"[\n\t]", k)[0]
    return k[:30]


def percentile(xs: List[float], q: float) -> float:
    xs = sorted(xs)
    if not xs:
        return 0.0
    return xs[min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))]


@dataclass
class Review:
    paras: List[Para]
    flags: List[Flag]
    metrics: Dict
    by_cat: Dict[str, int]
    hotspots: List[Dict]

    def to_dict(self) -> Dict:
        return {"metrics": self.metrics, "by_cat": self.by_cat, "hotspots": self.hotspots,
                "flags": [asdict(f) for f in self.flags]}


def analyze(path: str, skip_headings=("參考文獻", "參考資料", "References", "Bibliography")) -> Review:
    paras = extract_paragraphs(path, skip_headings)
    flags: List[Flag] = []

    def add(cat: str, p: Para, a: Optional[int], b: Optional[int], snippet: Optional[str] = None, key: Optional[str] = None, note: str = ""):
        label, conf, fix, color, hint = CATEGORIES[cat]
        text = p.text
        flags.append(Flag(
            cat=cat, label=label, conf=conf, para=p.idx,
            snippet=snippet if snippet is not None else (_snip(text, a, b) if a is not None else text[:40]),
            key=key if key is not None else (_key(text, a, b) if a is not None else text[:20].split("\t")[0]),
            note=(note + " " if note else "") + hint, fixable=fix,
            span=(a, b) if a is not None else None, heading=p.heading_path, color=color))

    doc_text = "\n".join(p.text for p in paras)
    trad = is_traditional(doc_text)
    body = [p for p in paras if not p.is_heading]

    # 引號慣例（文件層級）
    n_corner, n_curly = doc_text.count("「"), doc_text.count("“")
    minority = None
    if n_corner and n_curly:
        minority = "“" if n_curly < n_corner else "「"

    # 句長統計（先算，供相對門檻）
    sent_lens_all: List[int] = []
    para_cv: Dict[int, float] = {}
    de_rate: Dict[int, float] = {}
    for p in body:
        if p.in_table:      # 表格內是資料或文獻，不是文章的句子，也不納入全文分布
            continue
        ss = sentences(p.text)
        if _mostly_citations(p.text, ss):
            continue
        ls = [slen(s) for s in ss if slen(s) >= 2]
        sent_lens_all += ls
        if len(ls) >= 4:
            para_cv[p.idx] = _cv(ls)
        if len(p.text) >= 60:
            de_rate[p.idx] = p.text.count("的") * 100.0 / len(p.text)
    cv_cut = min(percentile(list(para_cv.values()), 0.20), 0.45) if para_cv else 0
    de_cut = max(percentile(list(de_rate.values()), 0.90), 5.0) if de_rate else 99

    spec: Dict[int, float] = {}
    for p in body:
        if len(p.text) >= 60:
            spec[p.idx] = len(_SPECIFIC_RE.findall(p.text)) * 100.0 / len(p.text)
    spec_cut = percentile(list(spec.values()), 0.25) if spec else 0
    generic_ids = {i for i, v in spec.items() if v <= min(spec_cut, 1.0)}
    # 若多數段落都空泛，逐段標示沒有區辨力 → 只回報文件層級指標，不逐段標
    generic_share = (sum(1 for v in spec.values() if v <= 1.0) / len(spec)) if spec else 0.0
    flag_generic = generic_share < 0.5

    prev_open: List[str] = []
    for p in paras:
        t = p.text
        if p.is_heading:
            if _HEADPAIR_RE.search(t):
                add("heading_pair", p, 0, len(t.rstrip()))
            continue
        for m in _ART_RE.finditer(t):
            add("artefact", p, m.start(), m.end())
        for m in _FILLER_RE.finditer(t):
            add("filler", p, m.start(), m.end())
        for m in _LEX_RE.finditer(t):
            add("lexicon", p, m.start(), m.end())
        for m in _BIG_RE.finditer(t):
            add("bigword", p, m.start(), m.end())
        for m in _DASH_CJK.finditer(t):
            core = m.start(1)
            if t[:core].strip() and t[m.end(1):].strip():   # 前後都有文字才算標點用法（單獨一個「—」是表格佔位符）
                add("dash", p, m.start(), m.end())
        if trad:
            for m in _REGION_RE.finditer(t):
                add("region", p, m.start(), m.end())
            sm = _SIMPLE_RE.search(t)
            if sm:
                add("simplified", p, sm.start(), sm.end(), note="共 %d 個簡體字。" % len(_SIMPLE_RE.findall(t)))
        for m in _NEG_RE.finditer(t):
            add("neg_parallel", p, m.start(), m.end())
        for m in _TRIAD_RE.finditer(t):
            add("triad", p, m.start(), min(m.end(), m.start() + 30))
        for m in _VAGUE_RE.finditer(t):
            a = max(t.rfind("。", 0, m.start()), t.rfind("！", 0, m.start()), t.rfind("？", 0, m.start())) + 1
            b_ = [i for i in (t.find("。", m.end()), t.find("！", m.end()), t.find("？", m.end())) if i != -1]
            b = (min(b_) + 1) if b_ else len(t)
            if not _CITE_RE.search(t[a:b]):
                add("vague_attr", p, m.start(), m.end())
        ss = [s for s in sentences(t) if slen(s) >= 2]
        if len(ss) >= 4:
            starts = sum(1 for s in ss if s.startswith(CONNECTORS))
            if starts >= 3 and starts / len(ss) >= 0.35:
                add("connector", p, None, None, note="%d／%d 句以連接詞開頭。" % (starts, len(ss)))
        if ss and _TAIL_RE.match(ss[-1]) and len(ss) >= 3:
            a = t.rfind(ss[-1])
            add("summary_tail", p, a, a + min(len(ss[-1]), 20))
        if len(_EG_RE.findall(t)) >= 2:
            m = _EG_RE.search(t)
            add("bracket_eg", p, m.start(), m.end(), note="本段 %d 處。" % len(_EG_RE.findall(t)))
        if p.idx in para_cv and para_cv[p.idx] <= cv_cut and para_cv[p.idx] < 0.45:
            ls = [slen(s) for s in ss]
            add("uniform_sent", p, None, None, note="句長變異係數 %.2f（本文第 20 百分位 %.2f），句長 %s。" % (
                para_cv[p.idx], cv_cut, "/".join(str(x) for x in ls[:8])))
        if p.idx in de_rate and de_rate[p.idx] >= de_cut:
            add("de_heavy", p, None, None, note="每 100 字 %.1f 個「的」。" % de_rate[p.idx])
        if len(_NOM_RE.findall(t)) >= 3:
            m = _NOM_RE.search(t)
            add("nominal", p, m.start(), m.end(), note="本段 %d 處。" % len(_NOM_RE.findall(t)))
        if flag_generic and p.idx in generic_ids:
            add("generic", p, None, None, note="每 100 字僅 %.1f 個數字／專名／出處（本文第 25 百分位 %.1f）。" % (spec[p.idx], spec_cut))
        for m in _RESTATE_RE.finditer(t):
            add("restate", p, m.start(), m.end())
        for m in _OPENER_RE.finditer(t):
            a0 = m.start() + (len(m.group(0)) - len(m.group(0).lstrip("。！？\n ")))
            add("opener", p, a0, m.end())
        trip = [m for m in _TRIPLE_RE.finditer(t)]
        if len(trip) >= 3:
            add("triple_list", p, trip[0].start(), trip[0].end(), note="本段 %d 處三項並列。" % len(trip))
        if p.first_bold and re.match(r"^.{1,20}?[：:]", t) and len(t) > 12:
            m = re.match(r"^.{1,20}?[：:]", t)
            add("bold_label", p, 0, m.end())
        if p.bold_ratio > 0.5 and len(t) > 40:
            add("bold_heavy", p, None, None, note="粗體約占 %d%%。" % int(p.bold_ratio * 100))
        if minority:
            i = t.find(minority)
            if i != -1:
                add("quote_mixed", p, i, i + 1)
        op = re.sub(r"^[\s\d.、（()）一二三四五六七八九十]+", "", t)[:3]
        prev_open.append(op)
        if len(prev_open) >= 3 and len(t) >= 60 and op and prev_open[-1] == prev_open[-2] == prev_open[-3]:
            add("para_open", p, 0, len(op), note="連續第 3 段以「%s」開頭。" % op)

    # ---- 章節結構：多數大節剛好三個小節（逐層檢查，標題層級不一定從 1 開始）----
    heads = [(p, _level(p.style, p.element)) for p in paras if p.is_heading]
    by_idx0 = {p.idx: p for p in paras}
    for L in sorted({lv for _, lv in heads if lv}):
        groups: List[tuple] = []
        for p, lv in heads:
            if lv == L:
                groups.append((p, []))
            elif lv == L + 1 and groups:
                groups[-1][1].append(p)
            elif lv and lv <= L and lv != L and groups:
                pass
        with_kids = [(p, k) for p, k in groups if k]
        if len(with_kids) >= 4:
            three = [p for p, k in with_kids if len(k) == 3]
            if len(three) / len(with_kids) >= 0.6:
                for hp in three:
                    add("heading_rule3", hp, 0, len(hp.text.rstrip()),
                        note="第 %d 級標題：%d／%d 個都剛好 3 個子標題。" % (L, len(three), len(with_kids)))

    # ---- 指標 ----
    chars = sum(len(p.text) for p in body) or 1
    paras_len = [len(p.text) for p in body if len(p.text) > 10]
    conn_total = sum(1 for p in body for s in sentences(p.text) if s.startswith(CONNECTORS))
    sent_total = len(sent_lens_all) or 1
    metrics = {
        "段落數(含標題)": len(paras), "內文段落數": len(body), "內文字數": chars,
        "句子數": len(sent_lens_all),
        "句長平均": round(statistics.mean(sent_lens_all), 1) if sent_lens_all else 0,
        "句長中位數": statistics.median(sent_lens_all) if sent_lens_all else 0,
        "句長標準差": round(statistics.pstdev(sent_lens_all), 1) if sent_lens_all else 0,
        "句長變異係數": round(_cv(sent_lens_all), 2),
        "句長落在15–30字的比例": round(sum(1 for x in sent_lens_all if 15 <= x <= 30) / sent_total, 2),
        "段落長度變異係數": round(_cv(paras_len), 2),
        "以連接詞開頭的句子比例": round(conn_total / sent_total, 2),
        "每百字「的」": round(doc_text.count("的") * 100.0 / max(1, len(doc_text)), 2),
        "每百字逗號／頓號": round((doc_text.count("，") + doc_text.count("、")) * 100.0 / max(1, len(doc_text)), 2),
        "每百字括號（半形／全形）": round(len(re.findall(r"[（(]", doc_text)) * 100.0 / max(1, len(doc_text)), 2),
        "破折號數": len(re.findall("——|—", doc_text)),
        "具體資訊稀少的段落比例": round(generic_share, 2),
        "文件判定為繁體": trad,
        "句長變異係數(逐段)第20百分位": round(cv_cut, 2),
    }

    # 避開文件原本就有的螢光筆顏色（否則使用者分不出哪些是工具標的）
    with zipfile.ZipFile(path) as _z:
        _raw = _z.read("word/document.xml").decode("utf-8", "ignore")
    used: Dict[str, int] = {}
    for c in re.findall(r'<w:highlight w:val="(\w+)"', _raw):
        used[c] = used.get(c, 0) + 1
    pool = [c for c in ("yellow", "cyan", "green", "magenta", "red", "blue", "darkYellow", "lightGray", "darkCyan")]
    remap: Dict[str, str] = {}
    taken = set(used)
    for cat_color in dict.fromkeys(v[3] for v in CATEGORIES.values()):
        if cat_color in used:
            free = [c for c in pool if c not in taken]
            remap[cat_color] = free[0] if free else cat_color
            taken.add(remap[cat_color])
        else:
            taken.add(cat_color)
    for f in flags:
        f.color = remap.get(f.color, f.color)
    metrics["原文件既有螢光筆顏色"] = ", ".join("%s×%d" % kv for kv in used.items()) or "無"

    by_cat: Dict[str, int] = {}
    for f in flags:
        by_cat[f.cat] = by_cat.get(f.cat, 0) + 1
    # 熱點段落
    score: Dict[int, Dict] = {}
    for f in flags:
        d = score.setdefault(f.para, {"para": f.para, "score": 0, "cats": set(), "heading": f.heading})
        d["score"] += CONF_WEIGHT[f.conf]
        d["cats"].add(f.label)
    by_idx = {p.idx: p for p in paras}
    hot = []
    for d in sorted(score.values(), key=lambda x: -x["score"])[:25]:
        p = by_idx[d["para"]]
        hot.append({"para": d["para"], "score": d["score"], "cats": sorted(d["cats"]), "heading": d["heading"],
                    "preview": p.text[:50].replace("\n", " "), "key": p.text[:20].split("\t")[0]})
    return Review(paras, flags, metrics, by_cat, hot)
