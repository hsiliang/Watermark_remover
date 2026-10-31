from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

from . import finalize, rebuild as rb, scan as sc, verify as vf


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="wmr", description="Word 檔 AI 痕跡檢測與消除（自用）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan", help="檢測痕跡")
    s.add_argument("file")
    s.add_argument("--json", action="store_true")
    s.add_argument("--style", action="store_true", help="一併檢測文字風格痕跡")
    c = sub.add_parser("clean", help="掃描→重建→(選用 Word 重存)→改屬性→複掃→保真比對")
    c.add_argument("file")
    c.add_argument("-o", "--out", required=True)
    c.add_argument("--word", action="store_true", help="以 Word for Mac 重存一次（選用；會讓 Word 改寫樣式，LibreOffice 渲染可能與原檔不同，請搭配 --render-check）")
    c.add_argument("--style", action="store_true", help="啟用文字風格層（規則式替換，選用）")
    c.add_argument("--render-check", action="store_true", help="用 LibreOffice 渲染原檔與輸出，逐頁比對畫面（最直接的格式保真證據）")
    c.add_argument("--keep-hidden", action="store_true", help="不刪除隱藏文字")
    c.add_argument("--page-check", action="store_true", help="用 Word 重存原檔與輸出檔，比對頁數／字數")
    a = ap.parse_args(argv)

    if a.cmd == "scan":
        f = sc.scan(a.file, style=a.style)
        print(json.dumps(sc.as_dicts(f), ensure_ascii=False, indent=2) if a.json else sc.summarize(f))
        return 1 if any(x.severity == "high" for x in f) else 0

    before = sc.scan(a.file, style=a.style)
    print("== 清理前 ==\n" + sc.summarize(before))
    tmp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out", "_work")
    os.makedirs(tmp, exist_ok=True)
    stage = os.path.join(tmp, "stage1.docx")
    rep = rb.rebuild(a.file, stage, drop_hidden=not a.keep_hidden, style=a.style)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
