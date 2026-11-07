import random

from wmr.painmeta import pain_to_props


def test_bounds_and_monotonic():
    for seed in range(200):
        rows = [pain_to_props(p, random.Random(seed)) for p in range(11)]
        assert rows[0]["a"] <= 30 * 1.5 + 1 and rows[0]["a"] >= 30 * 0.7 - 1
        assert 525600 * 0.9 - 1 <= rows[10]["a"] <= 525600 * 1.1 + 1
        for x, y in zip(rows, rows[1:]):
            assert x["a"] <= y["a"] and x["h"] <= y["h"] and x["revision"] <= y["revision"]
        for r in rows:
            assert 1 <= r["h"] <= r["a"] and r["h"] < 35000
            assert r["revision"] == -(-r["h"] // 100) + 1
        assert rows[10]["h"] > 34000


def test_finalize_writes_props(dirty_docx, tmp_path):
    import datetime as dt
    import zipfile
    from wmr import finalize, rebuild

    out = str(tmp_path / "o.docx")
    rebuild.rebuild(str(dirty_docx), out)
    finalize.finalize_props(out, pain=10)
    z = zipfile.ZipFile(out)
    core, app = z.read("docProps/core.xml").decode(), z.read("docProps/app.xml").decode()
    import re
    h = int(re.search(r"<TotalTime>(\d+)</TotalTime>", app).group(1))
    rev = int(re.search(r"<cp:revision>(\d+)</cp:revision>", core).group(1))
    cr = dt.datetime.fromisoformat(re.search(r"created[^>]*>([^<]+)Z<", core).group(1))
    mo = dt.datetime.fromisoformat(re.search(r"modified[^>]*>([^<]+)Z<", core).group(1))
    assert h > 34000 and rev == -(-h // 100) + 1 and (mo - cr).days > 300
