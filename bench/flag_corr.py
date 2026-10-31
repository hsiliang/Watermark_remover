"""我的可疑旗標 vs 獨立檢測器分數。
用法：.venv/bin/python bench/flag_corr.py dump.json 名稱=檔案.docx [名稱=檔案.docx ...]
對每個類別：有該旗標的片段平均分數 vs 沒有的片段（分類器：越高越像 AI；ppl／binoculars：越低越像機器，故取負號統一方向）。
樣本小、檢測器本身有偏誤——只能當線索，不是證明。
"""
import json
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wmr.suspect import CATEGORIES, analyze  # noqa: E402


def main(dump_path, pairs):
    dump = json.load(open(dump_path))
    rows = {}   # det -> list of (flags_by_cat, total, score)
    for pair in pairs:
        name, path = pair.split("=", 1)
        rv = analyze(path)
        body = [p for p in rv.paras if not p.is_heading]
        text, spans, pos = "", {}, 0
        for p in body:
            spans[p.idx] = (pos, pos + len(p.text))
            text += p.text + "\n"
            pos += len(p.text) + 1
        for det, docs in dump.items():
            if name not in docs:
                continue
            cur = 0
            for ch in docs[name]:
                i = text.find(ch["text"][:30], cur)
                if i < 0 or ch["score"] is None:
                    continue
                j = i + len(ch["text"])
                cur = i
                cats = {}
                for f in rv.flags:
                    if f.para in spans and spans[f.para][0] < j and spans[f.para][1] > i:
                        cats[f.cat] = cats.get(f.cat, 0) + 1
                sc = ch["score"]
                if "ppl" in det or "binoc" in det:
                    sc = -sc
                rows.setdefault(det, []).append((cats, sum(cats.values()), sc))
    for det, data in rows.items():
        print("\n== %s  (片段數 %d)" % (det, len(data)))
        allc = sorted({c for d in data for c in d[0]})
        out = []
        for c in allc:
            w = [s for cats, _, s in data if c in cats]
            wo = [s for cats, _, s in data if c not in cats]
            if len(w) >= 3 and len(wo) >= 3:
                out.append((st.mean(w) - st.mean(wo), c, len(w), st.mean(w), st.mean(wo)))
        for lift, c, n, a, b in sorted(out, reverse=True):
            print("  %-26s n=%-3d  有旗標 %.3f  無旗標 %.3f  差 %+.3f" % (CATEGORIES[c][0][:12], n, a, b, lift))
        # 總旗標數與分數的相關（Spearman 的簡化：秩相關）
        xs = [d[1] for d in data]; ys = [d[2] for d in data]
        def rank(v):
            o = sorted(range(len(v)), key=lambda k: v[k]); r = [0] * len(v)
            for k, i in enumerate(o): r[i] = k
            return r
        rx, ry = rank(xs), rank(ys)
        n = len(xs); mx, my = st.mean(rx), st.mean(ry)
        num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
        den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
        print("  總旗標數 vs 分數 秩相關 ρ = %.2f" % (num / den if den else 0))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
