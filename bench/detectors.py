"""獨立的本地 AI 文本檢測器，用來量測 wmr 的清理成效（紅隊迴圈的「裁判」）。

與 wmr 本身刻意分離：
- 不用 wmr 的規則，避免「自己出題自己改考卷」。
- 都是公開方法／公開模型：RoBERTa 分類器（HC3 等）、GPT2 困惑度＋GLTR、Binoculars（Qwen 雙模型）。
- 這些檢測器各有偏誤（HC3 是 GPT-3.5 時代的問答短文），分數只能做「同一份文章清理前後」的相對比較，
  以及和已知人類／AI 文本對照，不可當作任何商業系統（知網等）的預測。
"""
from __future__ import annotations

import math
import re
from typing import Dict, List

import torch

DEVICE = "mps" if torch.backends.mps.is_available() else "cpu"


def split_chunks(text: str, target: int = 260) -> List[str]:
    """依句界切成約 target 字的片段（網站類檢測常以段／數百字為單位）。"""
    sents = [s for s in re.split(r"(?<=[。！？；\n])", text) if s.strip()]
    chunks, cur = [], ""
    for s in sents:
        if len(cur) + len(s) > target and cur:
            chunks.append(cur.strip())
            cur = ""
        cur += s
    if cur.strip():
        chunks.append(cur.strip())
    return [c for c in chunks if len(c) >= 40]


class Roberta:
    def __init__(self, model_id: str, max_len: int = 512):
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        self.name = model_id.split("/")[-1]
        self.tok = AutoTokenizer.from_pretrained(model_id)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_id).to(DEVICE).eval()
        self.max_len = max_len
        self.ai_index = 1   # 之後以 calibrate_direction 依 AUC 決定

    @torch.no_grad()
    def probs(self, texts: List[str], bs: int = 16) -> List[List[float]]:
        out = []
        for i in range(0, len(texts), bs):
            enc = self.tok(texts[i:i + bs], truncation=True, max_length=self.max_len, padding=True, return_tensors="pt").to(DEVICE)
            out += torch.softmax(self.model(**enc).logits, -1).cpu().tolist()
        return out

    def score(self, texts: List[str]) -> List[float]:
        return [p[self.ai_index] for p in self.probs(texts)]

    def calibrate_direction(self, human: List[str], ai: List[str]) -> float:
        """用已知樣本決定哪個標籤代表 AI（不信任模型卡），回傳 AUC。"""
        ph, pa = self.probs(human), self.probs(ai)
        best = (0.0, 1)
        for idx in range(len(ph[0])):
            a = auc([p[idx] for p in ph], [p[idx] for p in pa])
            if a > best[0]:
                best = (a, idx)
        self.ai_index = best[1]
        return best[0]


class GPT2PPL:
    """困惑度 + GLTR（top-10 命中比例）。分數越『可預測』越像 AI。"""

    def __init__(self, model_id: str = "uer/gpt2-chinese-cluecorpussmall"):
        from transformers import AutoModelForCausalLM, BertTokenizerFast
        self.name = "gpt2zh-ppl"
        self.tok = BertTokenizerFast.from_pretrained(model_id)
        self.model = AutoModelForCausalLM.from_pretrained(model_id).to(DEVICE).eval()

    @torch.no_grad()
    def metrics(self, text: str) -> Dict[str, float]:
        ids = self.tok(text, return_tensors="pt", truncation=True, max_length=510).input_ids.to(DEVICE)
        if ids.shape[1] < 8:
            return {}
        logits = self.model(ids).logits[0, :-1]
        tgt = ids[0, 1:]
        logp = torch.log_softmax(logits, -1)
        tok_lp = logp.gather(1, tgt[:, None])[:, 0]
        rank = (logp > tok_lp[:, None]).sum(1)
        return {"ppl": math.exp(-tok_lp.mean().item()), "top10": (rank < 10).float().mean().item(),
                "lp_std": tok_lp.std().item()}

    def score(self, texts: List[str]) -> List[float]:
        ms = [self.metrics(t) for t in texts]
        return [m["ppl"] if m else float("nan") for m in ms]


class Binoculars:
    """Binoculars（Hans et al. 2024）：log-PPL(observer) / cross-PPL(observer, performer)。越低越像機器。
    原論文用 Falcon-7B；這裡用 Qwen2.5 base + instruct（中文較合適、可在本機跑）。門檻不可沿用論文，需用已知樣本重新校準。"""

    def __init__(self, observer="Qwen/Qwen2.5-0.5B", performer="Qwen/Qwen2.5-0.5B-Instruct"):
        from transformers import AutoModelForCausalLM, AutoTokenizer
        self.name = "binoculars-" + observer.split("/")[-1]
        self.tok = AutoTokenizer.from_pretrained(observer)
        self.obs = AutoModelForCausalLM.from_pretrained(observer, torch_dtype=torch.float32).to(DEVICE).eval()
        self.perf = AutoModelForCausalLM.from_pretrained(performer, torch_dtype=torch.float32).to(DEVICE).eval()

    @torch.no_grad()
    def one(self, text: str):
        ids = self.tok(text, return_tensors="pt", truncation=True, max_length=512).input_ids.to(DEVICE)
        if ids.shape[1] < 16:
            return None
        lo, lp = self.obs(ids).logits[0, :-1].float(), self.perf(ids).logits[0, :-1].float()
        tgt = ids[0, 1:]
        ppl = -torch.log_softmax(lo, -1).gather(1, tgt[:, None]).mean()
        xppl = -(torch.softmax(lp, -1) * torch.log_softmax(lo, -1)).sum(-1).mean()
        return (ppl / xppl).item()

    def score(self, texts: List[str]) -> List[float]:
        return [s if s is not None else float("nan") for s in (self.one(t) for t in texts)]


def auc(neg: List[float], pos: List[float]) -> float:
    """pos 分數高於 neg 的機率（Mann-Whitney）。"""
    neg = [x for x in neg if x == x]
    pos = [x for x in pos if x == x]
    if not neg or not pos:
        return float("nan")
    gt = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return gt / (len(pos) * len(neg))
