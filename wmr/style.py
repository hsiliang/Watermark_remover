"""文字風格層（選用）。純規則／詞表，確定性，不使用 AI。

只做「機械式、語意安全」的替換；無法確定、需要改寫的部分交給 suspect.py 統計並標位置，由人工處理。
詞組替換在「段落層級」進行（可跨 run），字元層級轉換在 run 內進行。
規則群組（可個別關閉）：artefact, filler, lexicon, region, markdown, emoji, dash, punct, quotes
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Callable, Dict, List, Optional, Tuple

from lxml import etree

from .common import NS_W

W = "{%s}" % NS_W
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

ALL_GROUPS = ("artefact", "filler", "lexicon", "region", "markdown", "emoji", "dash", "punct")
OPTIONAL_GROUPS = ("quotes",)  # 預設關閉：引號慣例依文件而定

SIMPLIFIED_CHARS = "们这为与对发国学会时个来说过还没经现长门问间开关东车书两无电气实点体动机业产种应样进变"
TRADITIONAL_CHARS = "們這為與對發國學會時個來說過還沒經現長門問間開關東車書兩無電氣實點體動機業產種應樣進變"

# ---- 規則表 ------------------------------------------------------------
# 1) ChatGPT/搜尋工具殘留標記
ARTEFACTS = [
    r"【[^】]{0,80}†[^】]{0,80}】",
    r"\[?contentReference\[oaicite:\d+\]\{index=\d+\}\]?",
    r"\boaicite:\d+\b",
    r"\bturn\d+(?:search|view|news|image)\d+\b",
    r"[?&]utm_source=(?:chatgpt\.com|openai|claude)[^\s)）]*",
]

# 2) 純套話：整個刪除（含後面的逗號）
FILLERS = [
    "值得注意的是", "值得一提的是", "值得關注的是", "需要指出的是", "需要強調的是", "需要注意的是",
    "不難發現", "顯而易見", "毋庸置疑", "不言而喻", "眾所周知",
]

# 3) 慣用語替換（多個選項時依出現順序輪替，避免新的單調）
LEXICON: Dict[str, List[str]] = {
    "此外": ["另外", "同時", "再者"],
    "與此同時": ["同時"],
    "不僅如此": ["而且"],
    "綜上所述": ["整體而言", "整體來看"],
    "總而言之": ["簡單說"],
    "總的來說": ["整體而言"],
    "至關重要": ["非常重要", "十分重要"],
    "深入探討": ["探討"],
    "旨在": ["目的在於"],
    "賦能": ["協助"],
    "助力": ["協助"],
    "全方位": ["全面"],
    "多維度": ["多面向"],
    "日益": ["逐漸"],
    "delve into": ["look into"],
    "delves into": ["looks into"],
    "it is worth noting that": ["note that"],
    "in conclusion": ["to sum up"],
    "furthermore": ["also"],
    "moreover": ["also"],
    "pivotal": ["key"],
    "crucial": ["important"],
    "seamless": ["smooth"],
    "tapestry": ["mix"],
    "underscores": ["shows"],
    "showcasing": ["showing"],
}

# 4) 大陸用語 → 台灣用語（僅在文件以繁體為主時套用；排除有歧義的詞）
REGION = {
    "信息": "資訊", "軟件": "軟體", "硬件": "硬體", "網絡": "網路", "默認": "預設", "用戶": "使用者",
    "視頻": "影片", "服務器": "伺服器", "數據庫": "資料庫", "屏幕": "螢幕", "鼠標": "滑鼠",
    "文檔": "文件", "打印": "列印", "登錄": "登入", "激活": "啟用", "調用": "呼叫", "接口": "介面",
}

_EMOJI_RE = re.compile("[ ]?[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\U0000FE0F\U0000200D]+")
_MD_BOLD = re.compile(r"(\*\*|__)(.+?)\1")
_MD_CODE = re.compile(r"`([^`]+)`")
_MD_HEAD = re.compile(r"^\s{0,3}#{1,6}\s+")
_CJK = r"[㐀-鿿　-〿＀-￯]"
_PUNCT = {",": "，", ":": "：", ";": "；", "?": "？", "!": "！"}   # 括號不轉：成對轉換無法跨 run 保證
_IDEO = r"[\u3400-\u9fff]"
_DASH_CJK = re.compile(r"\s*(——|—|--)\s*")
_DASH_EN = re.compile(r"\s+[—–]\s+")
_FILLER_RE = re.compile("(?:%s)[，,、]?" % "|".join(re.escape(x) for x in sorted(FILLERS, key=len, reverse=True)))
_LEX_RE = re.compile("|".join(re.escape(k) for k in sorted(LEXICON, key=len, reverse=True)), re.I)
_LEX_LOWER = {k.lower(): v for k, v in LEXICON.items()}
_REGION_RE = re.compile("|".join(re.escape(k) for k in sorted(REGION, key=len, reverse=True)))
_ART_RE = re.compile("|".join("(?:%s)" % a for a in ARTEFACTS))


class Ctx:
    """一次清理的共用狀態：啟用的群組、輪替計數、是否繁體為主。"""

    def __init__(self, groups=None, traditional: bool = True):
        self.groups = set(groups) if groups is not None else set(ALL_GROUPS)
        self.traditional = traditional
        self.counts: Dict[str, int] = defaultdict(int)
        self.skip: set = set()   # 不處理的 w:p（例如參考文獻章節）
        self.log: Dict[str, List[Tuple[str, str]]] = defaultdict(list)   # 規則群組 → [(改前, 改後)]

    def record(self, group: str, before: str, after: str) -> None:
        if before != after:
            self.log[group].append((before, after))

    def examples(self, group: str, n: int = 3) -> List[str]:
        """只顯示差異處（含前後文），避免整段長字串看不出改了什麼。"""
        import difflib
        out = []
        for b, a in self.log.get(group, [])[:n]:
            sm = difflib.SequenceMatcher(None, b, a)
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag != "equal":
                    out.append("「%s」→「%s」" % (b[max(0, i1 - 5):i2 + 5], a[max(0, j1 - 5):j2 + 5] if a[j1:j2] or True else ""))
                    break
        return out

    def summary(self) -> Dict[str, int]:
        return {g: len(v) for g, v in self.log.items()}

    def on(self, g: str) -> bool:
        return g in self.groups

    def pick(self, key: str, options: List[str]) -> str:
        i = self.counts[key]
        self.counts[key] += 1
        return options[i % len(options)]


def is_traditional(text: str) -> bool:
    s = sum(text.count(c) for c in SIMPLIFIED_CHARS)
    t = sum(text.count(c) for c in TRADITIONAL_CHARS)
    return t >= s


def _fix_punct(s: str) -> str:
    out = list(s)
    for i, ch in enumerate(s):
        if ch in _PUNCT:
            prev = s[i - 1] if i > 0 else ""
            nxt = s[i + 1] if i + 1 < len(s) else ""
            near = re.match(_IDEO, prev or " ") or re.match(_IDEO, nxt or " ")   # 只看漢字，不看全形標點
            if near:
                out[i] = _PUNCT[ch]
    return "".join(out)


def _phrase_rules(ctx: Ctx) -> List[Tuple[re.Pattern, Callable[[re.Match], str]]]:
    rules: List[Tuple[re.Pattern, Callable[[re.Match], str]]] = []
    def logged(group, fn):
        def inner(m):
            out = fn(m)
            ctx.record(group, m.group(0), out)
            return out
        return inner
    if ctx.on("artefact"):
        rules.append((_ART_RE, logged("artefact", lambda m: "")))
    if ctx.on("filler"):
        rules.append((_FILLER_RE, logged("filler", lambda m: "")))
    if ctx.on("lexicon"):
        rules.append((_LEX_RE, logged("lexicon", lambda m: ctx.pick("lex:" + m.group(0).lower(), _LEX_LOWER[m.group(0).lower()]))))
    if ctx.on("region") and ctx.traditional:
        rules.append((_REGION_RE, logged("region", lambda m: REGION[m.group(0)])))
    return rules


def _char_level(s: str, ctx: Ctx, has_cjk: Optional[bool] = None) -> str:
    """has_cjk：段落層級的中文語境（run 可能只含破折號或英文，不能只看自己）。"""
    if has_cjk is None:
        has_cjk = re.search(_CJK, s) is not None
    if ctx.on("markdown"):
        n = _MD_HEAD.sub("", s)
        n = _MD_BOLD.sub(r"\2", n)
        n = _MD_CODE.sub(r"\1", n)
        ctx.record("markdown", s, n)
        s = n
    if ctx.on("emoji"):
        n = _EMOJI_RE.sub("", s)
        ctx.record("emoji", s, n)
        s = n
    if ctx.on("dash"):
        n = _DASH_CJK.sub("，", s) if has_cjk else _DASH_EN.sub(", ", s)
        ctx.record("dash", s, n)
        s = n
    if ctx.on("quotes") and has_cjk:
        n = s.replace("\u201c", "「").replace("\u201d", "」")
        ctx.record("quotes", s, n)
        s = n
    if ctx.on("punct") and has_cjk:
        n = _fix_punct(s)
        ctx.record("punct", s, n)
        s = n
    return s


# ---- 字串 API（測試與預覽用）------------------------------------------------
def restyle_text(s: str, ctx: Optional[Ctx] = None) -> str:
    ctx = ctx or Ctx(traditional=is_traditional(s))
    for rx, fn in _phrase_rules(ctx):
        s = rx.sub(fn, s)
    return _char_level(s, ctx)


# ---- 段落 API（跨 run）----------------------------------------------------
def _text_nodes(p: etree._Element) -> List[etree._Element]:
    return [t for t in p.iter(W + "t")]


def _set_text(t: etree._Element, text: str) -> None:
    t.text = text
    if text != text.strip() or "  " in text:
        t.set(XML_SPACE, "preserve")


def _apply_rule(p: etree._Element, rx: re.Pattern, fn: Callable[[re.Match], str]) -> None:
    nodes = _text_nodes(p)
    if not nodes:
        return
    texts = [t.text or "" for t in nodes]
    full = "".join(texts)
    matches = list(rx.finditer(full))
    if not matches:
        return
    starts, pos = [], 0
    for x in texts:
        starts.append(pos)
        pos += len(x)
    repls = [fn(m) for m in matches]  # 正向計算（輪替順序）
    for m, rep in reversed(list(zip(matches, repls))):
        s, e = m.start(), m.end()
        first = True
        for i, t in enumerate(nodes):
            ns, ne = starts[i], starts[i] + len(texts[i])
            if ne <= s or ns >= e:
                continue
            a, b = max(s, ns) - ns, min(e, ne) - ns
            cur = texts[i]
            texts[i] = cur[:a] + (rep if first else "") + cur[b:]
            first = False
    for t, x in zip(nodes, texts):
        _set_text(t, x)


def restyle_paragraph(p: etree._Element, ctx: Ctx) -> None:
    if p in ctx.skip:
        return
    for rx, fn in _phrase_rules(ctx):
        _apply_rule(p, rx, fn)
    para_cjk = re.search(_CJK, "".join(t.text or "" for t in _text_nodes(p))) is not None
    for t in _text_nodes(p):
        if t.text:
            new = _char_level(t.text, ctx, para_cjk)
            if new != t.text:
                _set_text(t, new)


# ---- 簡易偵測（供 scan --style 使用）-----------------------------------------
def scan_text(s: str) -> List[Tuple[str, str]]:
    hits: List[Tuple[str, str]] = []
    for m in _LEX_RE.finditer(s):
        hits.append(("lexicon", m.group(0)))
    for m in _FILLER_RE.finditer(s):
        hits.append(("filler", m.group(0)))
    if _ART_RE.search(s):
        hits.append(("artefact", "殘留標記"))
    if _REGION_RE.search(s) and is_traditional(s):
        hits.append(("region", "大陸用語"))
    if re.search(_CJK, s) and _DASH_CJK.search(s) and ("——" in s or "—" in s or "--" in s):
        hits.append(("dash", "破折號"))
    if _MD_BOLD.search(s) or _MD_CODE.search(s) or _MD_HEAD.search(s):
        hits.append(("markdown", "Markdown 殘留"))
    if _EMOJI_RE.search(s):
        hits.append(("emoji", "表情符號"))
    return hits
