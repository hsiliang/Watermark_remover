"""成對比較：edits.json 中每個段落「改寫前 text」vs「改寫後 new_text」的檢測器分數差。
依旗標類別彙整平均降幅。用法：.venv/bin/python bench/para_delta.py edits.json [--out r.json]
（分類器：分數降低＝更像人；ppl／binoculars：分數升高＝更像人。一律換算成「往人類方向的移動量」，正值＝改善。）
"""
import json
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from detectors import Binoculars, GPT2PPL, Roberta  # noqa: E402

ROBERTAS = ["Hello-SimpleAI/chatgpt-detector-roberta-chinese", "mokawa3018/cmj-chinese-aigc-text-detector",
            "yuchuantian/AIGC_detector_zhv3"]


def main(path, out=None):
    edits = [e for e in json.load(open(path, encoding="utf-8"))["edits"] if e.get("new_text") and e["new_text"] != e["text"]]
    olds, news = [e["text"] for e in edits], [e["new_text"] for e in edits]
    print("成對段落：%d" % len(edits), flush=True)
    dets = []
    for mid in ROBERTAS:
        d = Roberta(mid)
        d.ai_index = 1   # 三者在 HC3 校準時皆為 1（見 run_bench 輸出）
        dets.append((d, +1))
    dets += [(GPT2PPL(), -1), (Binoculars(), -1)]
    res = {}
    for d, sign in dets:
        so, sn = d.score(olds), d.score(news)
        # 往人類方向的改善量：分類器 = 舊-新；ppl/binoculars = 新-舊
        imp = [(a - b) if sign > 0 else (b - a) for a, b in zip(so, sn)]
        res[d.name] = {"improve": imp, "old": so, "new": sn}
        ok = [x for x in imp if x == x]
        print("\n== %s：平均改善 %+.3f，改善的段落 %d/%d；舊均值 %.3f → 新均值 %.3f" % (
            d.name, st.mean(ok), sum(x > 0 for x in ok), len(ok),
            st.mean(x for x in so if x == x), st.mean(x for x in sn if x == x)), flush=True)
        cats = {}
        for e, x in zip(edits, imp):
            if x != x:
                continue
            for c in {f["類別"] for f in e["flags"]}:
                cats.setdefault(c, []).append(x)
        for c, xs in sorted(cats.items(), key=lambda kv: -st.mean(kv[1])):
            if len(xs) >= 3:
                print("   %-30s n=%-2d 平均改善 %+.3f" % (c[:14], len(xs), st.mean(xs)), flush=True)
    if out:
        json.dump(res, open(out, "w"), ensure_ascii=False)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[3] if len(sys.argv) > 3 and sys.argv[2] == "--out" else None)
