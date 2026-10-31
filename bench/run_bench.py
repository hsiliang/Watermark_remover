"""用獨立檢測器量測文章。用法：
  .venv/bin/python bench/run_bench.py --ref <含 hc3_*.jsonl 與 wiki/*.txt 的資料夾> 文章1.docx 文章2.txt ...
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from detectors import Binoculars, GPT2PPL, Roberta, auc, split_chunks  # noqa: E402

ROBERTAS = ["Hello-SimpleAI/chatgpt-detector-roberta-chinese", "mokawa3018/cmj-chinese-aigc-text-detector",
            "yuchuantian/AIGC_detector_zhv3"]


def to_trad(s):
    from opencc import OpenCC
    return OpenCC("s2twp").convert(s)


def load_hc3(ref, n=120, seed=7):
    rnd = random.Random(seed)
    hum, ai = [], []
    for f in glob.glob(os.path.join(ref, "hc3_*.jsonl")):
        for line in open(f, encoding="utf-8"):
            d = json.loads(line)
            for h in d.get("human_answers", []):
                if h and 120 <= len(h) <= 600:
                    hum.append(h)
            for a in d.get("chatgpt_answers", []):
                if a and 120 <= len(a) <= 600:
                    ai.append(a)
    rnd.shuffle(hum), rnd.shuffle(ai)
    return [to_trad(x) for x in hum[:n]], [to_trad(x) for x in ai[:n]]


def load_wiki(ref):
    chunks = []
    for f in sorted(glob.glob(os.path.join(ref, "wiki", "*.txt"))):
        t = open(f, encoding="utf-8").read()
        t = "\n".join(l for l in t.splitlines() if not l.startswith("=") and len(l) > 30)
        chunks += split_chunks(t)
    return chunks


def read_doc(path):
    if path.endswith(".docx"):
        from wmr.suspect import extract_paragraphs
        ps = [p.text for p in extract_paragraphs(path) if not p.is_heading]
        return "\n".join(ps)
    return open(path, encoding="utf-8").read()


def summarize(xs):
    xs = [x for x in xs if x == x]
    return {"median": round(st.median(xs), 3), "mean": round(st.mean(xs), 3), "n": len(xs)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True)
    ap.add_argument("--out")
    ap.add_argument("--dump", help="輸出逐片段分數 JSON（供 flag_corr.py 分析）")
    ap.add_argument("--no-binoculars", action="store_true")
    ap.add_argument("files", nargs="+")
    a = ap.parse_args()

    hc3_h, hc3_a = load_hc3(a.ref)
    wiki = load_wiki(a.ref)
    docs = {os.path.basename(f): split_chunks(read_doc(f)) for f in a.files}
    print("參考集：HC3 人類 %d / ChatGPT %d；維基百科片段 %d；文章片段 %s" % (
        len(hc3_h), len(hc3_a), len(wiki), {k: len(v) for k, v in docs.items()}), flush=True)

    results = {}
    dump = {}
    dets = []
    for mid in ROBERTAS:
        try:
            d = Roberta(mid)
            ac = d.calibrate_direction(hc3_h[:60], hc3_a[:60])
            dets.append((d, "p", "AUC(HC3,繁)=%.2f AI標籤=%d" % (ac, d.ai_index)))
        except Exception as e:
            print("載入失敗", mid, repr(e)[:150], flush=True)
    try:
        g = GPT2PPL()
        dets.append((g, "ppl", ""))
    except Exception as e:
        print("載入失敗 gpt2", repr(e)[:150], flush=True)
    if not a.no_binoculars:
        try:
            dets.append((Binoculars(), "bino", ""))
        except Exception as e:
            print("載入失敗 binoculars", repr(e)[:150], flush=True)

    for d, kind, note in dets:
        row = {"note": note}
        row["HC3人類"] = summarize(d.score(hc3_h))
        row["HC3_ChatGPT"] = summarize(d.score(hc3_a))
        row["維基(人類)"] = summarize(d.score(wiki))
        row["_auc_hc3"] = round(auc(d.score(hc3_h), d.score(hc3_a)), 3)
        for name, ch in docs.items():
            sc = d.score(ch)
            dump.setdefault(d.name, {})[name] = [{"text": c, "score": (None if x != x else x)} for c, x in zip(ch, sc)]
            row[name] = summarize(sc)
            if kind == "p":
                row[name]["flag>0.5"] = round(sum(s > 0.5 for s in sc if s == s) / max(1, len([s for s in sc if s == s])), 2)
        results[d.name] = row
        print("\n== %s  %s" % (d.name, note), flush=True)
        for k, v in row.items():
            if k not in ("note",):
                print("  %-28s %s" % (k, v), flush=True)
    if a.out:
        json.dump(results, open(a.out, "w"), ensure_ascii=False, indent=1)
    if a.dump:
        json.dump(dump, open(a.dump, "w"), ensure_ascii=False)


if __name__ == "__main__":
    main()
