"""以 Word for Mac（AppleScript）重存／匯出 PDF。非 AI，純本機。"""
from __future__ import annotations

import os
import subprocess

_SAVE = '''
tell application "Microsoft Word"
    set d to open file name "{src}"
    delay 1
    save as d file name "{dst}" file format format document
    close d saving no
end tell
'''
_PDF = '''
tell application "Microsoft Word"
    set d to open file name "{src}"
    delay 1
    save as d file name "{dst}" file format format PDF
    close d saving no
end tell
'''


def _run(script: str) -> None:
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=75)
    if r.returncode != 0:
        raise RuntimeError("Word 自動化失敗：" + r.stderr.strip())


def resave(src: str, dst: str) -> None:
    _run(_SAVE.format(src=os.path.abspath(src), dst=os.path.abspath(dst)))


def to_pdf(src: str, dst: str) -> None:
    _run(_PDF.format(src=os.path.abspath(src), dst=os.path.abspath(dst)))
