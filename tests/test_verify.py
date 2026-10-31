from docx import Document
from docx.shared import Pt
from wmr.verify import compare


def _mk(path, runs):
    d = Document()
    p = d.add_paragraph()
    for text, bold in runs:
        r = p.add_run(text)
        r.bold = bold
        r.font.size = Pt(12)
    d.save(path)


def test_run_split_is_not_a_difference(tmp_path):
    a, b = str(tmp_path / "a.docx"), str(tmp_path / "b.docx")
    _mk(a, [("中文 Word 測試", True)])
    _mk(b, [("中文", True), (" Word ", True), ("測試", True)])
    assert compare(a, b) == []


def test_real_format_change_is_detected(tmp_path):
    a, b = str(tmp_path / "a.docx"), str(tmp_path / "b.docx")
    _mk(a, [("中文測試", True)])
    _mk(b, [("中文", True), ("測試", False)])
    assert compare(a, b) != []
