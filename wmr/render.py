"""用 LibreOffice 把兩份 docx 渲染成 PDF，逐頁比對畫面（相同渲染器 → 相對比較有效）。"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from typing import Dict

SOFFICE = shutil.which("soffice") or "/Applications/LibreOffice.app/Contents/MacOS/soffice"


def _pdf(docx: str, outdir: str) -> str:
    os.makedirs(outdir, exist_ok=True)
    r = subprocess.run([SOFFICE, "--headless", "--convert-to", "pdf", "--outdir", outdir, docx],
                       capture_output=True, text=True, timeout=300)
    pdf = os.path.join(outdir, os.path.splitext(os.path.basename(docx))[0] + ".pdf")
    if not os.path.exists(pdf):
        raise RuntimeError("LibreOffice 轉 PDF 失敗：" + (r.stderr or r.stdout).strip())
    return pdf


def _pages(pdf: str, zoom: float = 0.5):
    import fitz
    from PIL import Image
    out = []
    for pg in fitz.open(pdf):
        pix = pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom), colorspace=fitz.csGRAY)
        out.append(Image.frombytes("L", (pix.width, pix.height), pix.samples))
    return out


def compare_render(a: str, b: str, threshold: float = 0.001) -> Dict:
    """回傳 {pages_a, pages_b, identical, worst:[(頁, 差異比例)...]}。差異比例＝灰階差>32 的像素占比。"""
    from PIL import ImageChops
    tmp = tempfile.mkdtemp(prefix="wmr_render_")
    # 兩個檔案可能同名，分開目錄渲染
    pa, pb = _pages(_pdf(a, os.path.join(tmp, "a"))), _pages(_pdf(b, os.path.join(tmp, "b")))
    diffs = []
    for i, (x, y) in enumerate(zip(pa, pb), 1):
        if x.size != y.size:
            diffs.append((i, 1.0))
            continue
        h = ImageChops.difference(x, y).histogram()
        diffs.append((i, sum(h[32:]) / float(x.size[0] * x.size[1])))
    shutil.rmtree(tmp, ignore_errors=True)
    return {
        "pages_a": len(pa), "pages_b": len(pb),
        "identical": sum(1 for _, d in diffs if d < threshold),
        "worst": sorted(diffs, key=lambda x: -x[1])[:5],
    }
