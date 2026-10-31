"""文字風格層（選用）。純規則／詞表，確定性，不使用 AI。

只做機械式替換，不改語意結構。詞表可自行擴充（LEXICON）。
限制：逐 run 處理，跨 run 斷開的詞組不會被替換。
"""
from __future__ import annotations

import re
from typing import List, Tuple

# 常見 AI 慣用語 → 較平實的說法（依長度由長到短套用）
LEXICON = {
    "值得注意的是": "要留意的是",
    "需要強調的是": "要說明的是",
    "綜上所述": "整體來看",
    "總而言之": "簡單說",
    "不僅如此": "而且",
    "與此同時": "同時",
    "至關重要": "很重要",
    "深入探討": "討論",
    "旨在": "目的是",
    "賦能": "支援",
    "此外": "另外",
    "delve into": "look into",
    "it is worth noting that": "note that",
    "in conclusion": "to sum up",
    "furthermore": "also",
    "moreover": "also",
    "leverage": "use",
    "pivotal": "key",
    "crucial": "important",
    "seamless": "smooth",
}
_LEX = sorted(LEXICON, key=len, reverse=True)
_LEX_LOWER = {k.lower(): v for k, v in LEXICON.items()}
_LEX_RE = re.compile("|".join(re.escape(k) for k in _LEX), re.I)

_EMOJI_RE = re.compile(
    "[ ]?[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\U0000FE0F\U0000200D]+"
)
_MD_BOLD = re.compile(r"(\*\*|__)(.+?)\1")
_MD_CODE = re.compile(r"`([^`]+)`")
_MD_HEAD = re.compile(r"^\s{0,3}#{1,6}\s+")
_CJK = r"[㐀-鿿　-〿＀-￯]"
_PUNCT = {",": "，", ":": "：", ";": "；", "?": "？", "!": "！", "(": "（", ")": "）"}
_DASH_CJK = re.compile(r"\s*(——|—|--)\s*")
_DASH_EN = re.compile(r"\s+[—–]\s+")


def _fix_punct(s: str) -> str:
    out = list(s)
    for i, ch in enumerate(s):
        if ch in _PUNCT:
            prev = s[i - 1] if i > 0 else ""
            nxt = s[i + 1] if i + 1 < len(s) else ""
            # 緊鄰 CJK 字元才轉全形；括號則視內側是否為 CJK
            near = re.match(_CJK, prev or " ") or re.match(_CJK, nxt or " ")
            if ch in "()" and not near:
                continue
            if near:
                out[i] = _PUNCT[ch]
    return "".join(out)


def restyle_text(s: str) -> str:
    s = _MD_HEAD.sub("", s)
    s = _MD_BOLD.sub(r"\2", s)
    s = _MD_CODE.sub(r"\1", s)
    s = _EMOJI_RE.sub("", s)
    if re.search(_CJK, s):
        s = _DASH_CJK.sub("，", s)
    else:
        s = _DASH_EN.sub(", ", s)
    s = _LEX_RE.sub(lambda m: _LEX_LOWER[m.group(0).lower()], s)
    if re.search(_CJK, s):
        s = _fix_punct(s)
    return s


def scan_text(s: str) -> List[Tuple[str, str]]:
    hits: List[Tuple[str, str]] = []
    for m in _LEX_RE.finditer(s):
        hits.append(("lexicon", m.group(0)))
    if _DASH_CJK.search(s) and ("——" in s or "—" in s or "--" in s):
        hits.append(("dash", "破折號"))
    if _MD_BOLD.search(s) or _MD_CODE.search(s) or _MD_HEAD.search(s):
        hits.append(("markdown", "Markdown 殘留"))
    if _EMOJI_RE.search(s):
        hits.append(("emoji", "表情符號"))
    return hits
