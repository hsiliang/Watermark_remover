"""把 run_bench 的 JSON 整理成一張對照表。用法：.venv/bin/python bench/table.py results.json [results2.json ...]"""
import json
import sys

REFS = ("HC3人類", "HC3_ChatGPT", "維基(人類)")


def fmt(row, key, kind):
    v = row.get(key)
    if not v:
        return "-"
    if kind == "p":
        return "%.2f(%d%%)" % (v["mean"], round(100 * v.get("flag>0.5", 0)))
    return "%.2f" % v["median"]


def main(paths):
    merged = {}
    for p in paths:
        for det, row in json.load(open(p)).items():
            merged.setdefault(det, {}).update(row)
    files = []
    for row in merged.values():
        for k in row:
            if k not in REFS and not k.startswith("_") and k != "note" and k not in files:
                files.append(k)
    cols = list(merged)
    kinds = {d: ("p" if "ppl" not in d and "binoc" not in d else "m") for d in cols}
    print("格式：分類器＝平均P(AI)（>0.5 的片段比例）；ppl／binoculars＝中位數（越低越像機器）")
    print("%-34s" % "" + "".join("%-30s" % c[:28] for c in cols))
    print("%-34s" % "[AUC HC3]" + "".join("%-30s" % merged[c].get("_auc_hc3", "-") for c in cols))
    for r in REFS:
        print("%-34s" % ("[參考] " + r) + "".join("%-30s" % fmt(merged[c], r, kinds[c]) for c in cols))
    for f in files:
        print("%-34s" % f[:32] + "".join("%-30s" % fmt(merged[c], f, kinds[c]) for c in cols))


if __name__ == "__main__":
    main(sys.argv[1:])
