from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

from . import finalize, rebuild as rb, scan as sc, verify as vf


def _style_arg(a):
    if not a.style:
        return False
    if not a.style_rules:
        return True
    from .style import Ctx
    return Ctx(groups=[g.strip() for g in a.style_rules.split(",") if g.strip()])


def _review(path, html_path, json_path, ann_path, skip):
    from . import report, suspect, annotate
    rv = suspect.analyze(path, tuple(skip) if skip else ("參考文獻", "參考資料", "References", "Bibliography"))
    stem = os.path.splitext(path)[0]
    html_path = html_path or stem + "_review.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(report.build_html(rv, os.path.basename(path)))
    if json_path:
        with open(json_path, "w", encoding="utf-8") as f:
            f.write(report.build_json(rv))
    total = sum(rv.by_cat.values())
    manual = sum(n for c, n in rv.by_cat.items() if not suspect.CATEGORIES[c][2])
    print("== 可疑部位 ==  共 %d 處（需人工判斷 %d 處）" % (total, manual))
    for c in sorted(rv.by_cat, key=lambda c: (-suspect.CONF_WEIGHT[suspect.CATEGORIES[c][1]], -rv.by_cat[c])):
        lab, conf, fix, _, _ = suspect.CATEGORIES[c]
        print("  [%s] %-22s %4d 處  %s" % (conf, lab, rv.by_cat[c], "可自動修" if fix else "需人工"))
    print("報告：" + html_path)
    if ann_path:
        n = annotate.annotate(path, ann_path, rv)
        print("標註副本（%d 處標記，僅供檢閱、勿繳交）：%s" % (n, ann_path))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="wmr", description="Word 檔 AI 痕跡檢測與消除（自用）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="檢測痕跡")
    s.add_argument("file")
    s.add_argument("--json", action="store_true")
    s.add_argument("--style", action="store_true", help="一併檢測文字風格痕跡")
    rv = sub.add_parser("review", help="可疑部位報告（統計各類型、標位置，供人工修改）")
    rv.add_argument("file")
    rv.add_argument("--html", help="HTML 報告路徑（預設：與檔案同名 _review.html）")
    rv.add_argument("--json", help="另存 JSON")
    rv.add_argument("--annotate", help="輸出有色標註的副本 .docx（僅供檢閱，勿繳交）")
    rv.add_argument("--skip-heading", action="append", default=None, help="略過此標題下的章節（可重複；預設含 參考文獻）")
    ex = sub.add_parser("export", help="匯出有可疑訊號的段落給 AI／人工改寫（JSON）")
    ex.add_argument("file")
    ex.add_argument("-o", "--out", required=True)
    ex.add_argument("--min-conf", choices=["低", "中", "高"], default="低")
    ap_ = sub.add_parser("apply", help="把改寫結果（new_text）純本地寫回 docx")
    ap_.add_argument("file")
    ap_.add_argument("edits")
    ap_.add_argument("-o", "--out", required=True)
    ap_.add_argument("--force", action="store_true", help="含超連結／欄位等也強制替換（可能破壞該段結構）")
    c = sub.add_parser("clean", help="掃描→重建→(選用 Word 重存)→改屬性→複掃→保真比對")
    c.add_argument("file")
    c.add_argument("-o", "--out", required=True)
    c.add_argument("--word", action="store_true", help="以 Word for Mac 重存一次（選用；會讓 Word 改寫樣式，LibreOffice 渲染可能與原檔不同，請搭配 --render-check）")
    c.add_argument("--style", action="store_true", help="啟用文字風格層（規則式替換，選用）")
    c.add_argument("--render-check", action="store_true", help="用 LibreOffice 渲染原檔與輸出，逐頁比對畫面（最直接的格式保真證據）")
    c.add_argument("--style-rules", help="啟用的風格規則群組，逗號分隔（預設全開，另可加 quotes）。可用：artefact,filler,lexicon,region,markdown,emoji,dash,punct,quotes")
    c.add_argument("--review", action="store_true", help="清理後產出可疑部位報告與標註副本")
    c.add_argument("--keep-hidden", action="store_true", help="不刪除隱藏文字")
    c.add_argument("--page-check", action="store_true", help="用 Word 重存原檔與輸出檔，比對頁數／字數")
    a = ap.parse_args(argv)

    if a.cmd == "scan":
        f = sc.scan(a.file, style=a.style)
        print(json.dumps(sc.as_dicts(f), ensure_ascii=False, indent=2) if a.json else sc.summarize(f))
        return 1 if any(x.severity == "high" for x in f) else 0

    if a.cmd == "export":
        from . import edits
        n = edits.export_edits(a.file, a.out, min_conf=a.min_conf)
        print("已匯出 %d 個段落 → %s\n請在每項的 new_text 填入改寫（可交給 Claude 或自己改），再執行：python3 -m wmr apply %s %s -o 新檔.docx" % (n, a.out, a.file, a.out))
        return 0
    if a.cmd == "apply":
        from . import edits
        r = edits.apply_edits(a.file, a.edits, a.out, force=a.force)
        print("已套用 %d 段；未改動 %d 段；略過 %d 段 → %s" % (r["applied"], r["unchanged"], len(r["skipped"]), a.out))
        for i, why in r["skipped"]:
            print("  略過 id=%s：%s" % (i, why))
        return 0
    if a.cmd == "review":
        return _review(a.file, a.html, a.json, a.annotate, a.skip_heading)

    before = sc.scan(a.file, style=a.style)
    print("== 清理前 ==\n" + sc.summarize(before))
    tmp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out", "_work")
    os.makedirs(tmp, exist_ok=True)
    stage = os.path.join(tmp, "stage1.docx")
    rep = rb.rebuild(a.file, stage, drop_hidden=not a.keep_hidden, style=_style_arg(a))
    ctx = rep.get("style_ctx")
    if ctx is not None:
        print("\n== 風格層變更日誌 ==")
        if not ctx.log:
            print("（沒有需要替換的內容）")
        for g, items in ctx.log.items():
            ex = "；".join(ctx.examples(g))
            print("  %-9s %3d 處  例：%s" % (g, len(items), ex))
    print("\n已丟棄部件：", ", ".join(rep["dropped"]) or "(無)")
    cur = stage
    if a.word:
        from . import wordmac
        cur = os.path.join(tmp, "stage2.docx")
        wordmac.resave(stage, cur)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(cur, "rb") as fi, open(a.out, "wb") as fo:
        fo.write(fi.read())
    finalize.finalize_props(a.out)  # 純本地最後一步
    after = sc.scan(a.out, style=a.style)
    print("\n== 清理後 ==\n" + sc.summarize(after))
    diffs = vf.compare(a.file, a.out)
    print("\n== 保真比對 ==" + ("（已啟用風格層：文字／字數差異為預期，請只看表格、圖片、頁數）" if a.style else "") + "\n" + ("\n".join(diffs) if diffs else "段落/樣式/表格/圖片一致"))
    if a.page_check:
        from . import wordmac
        ref = os.path.join(tmp, "ref_orig.docx")
        wordmac.resave(a.file, ref)
        so, sn = wordmac.stats(ref), wordmac.stats(a.out)
        same = all(so.get(k) == sn.get(k) for k in ("Pages", "Words"))
        print("Word 頁數/字數：原檔 %s / 輸出 %s -> %s（字元數差異來自移除的隱形字元）" % (so, sn, "一致" if same else "不一致！"))
    if a.render_check:
        from .render import compare_render
        r = compare_render(a.file, a.out)
        ok = r["pages_a"] == r["pages_b"] and r["identical"] == r["pages_a"]
        print("渲染比對（LibreOffice）：頁數 %d → %d，畫面相同 %d/%d 頁 -> %s" % (
            r["pages_a"], r["pages_b"], r["identical"], r["pages_a"], "一致" if ok else "有差異！最差頁(頁,比例)：%s" % [(p, round(d, 3)) for p, d in r["worst"]]))
    print("\n輸出：" + a.out)
    if a.review:
        stem = os.path.splitext(a.out)[0]
        print()
        _review(a.out, stem + "_review.html", None, stem + "_標註.docx", None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
