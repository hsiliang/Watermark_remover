"""把 Markdown 轉成近似 Claude 產出的 docx（標題階層、**粗體標籤**、條列）。僅供測試用。"""
import re
import sys

from docx import Document


def convert(md_paths, out, author="Un-named"):
    d = Document()
    d.core_properties.author = author
    d.core_properties.last_modified_by = author
    for path in md_paths:
        for line in open(path, encoding="utf-8").read().splitlines():
            if not line.strip():
                continue
            m = re.match(r"^(#{1,3})\s+(.*)", line)
            if m:
                d.add_heading(m.group(2).strip(), len(m.group(1)))
                continue
            style = None
            if line.startswith("- "):
                style, line = "List Bullet", line[2:]
            p = d.add_paragraph(style=style)
            for i, seg in enumerate(re.split(r"\*\*(.+?)\*\*", line)):
                if seg:
                    p.add_run(seg).bold = bool(i % 2)
    d.save(out)


if __name__ == "__main__":
    convert(sys.argv[2:], sys.argv[1])
