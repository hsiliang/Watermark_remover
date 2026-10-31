"""以 Word for Mac（AppleScript）重存。非 AI，純本機。

此機器上 Word 的 `save as` 指令不可用（-1708），改用：複製檔案 → 在 Word 開啟
→ 標記為已修改 → `close saving yes`，由 Word 自行把整個封包重寫回該檔。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import zipfile

_SCRIPT = '''
tell application "Microsoft Word"
    open file name "{path}"
    delay 2
    if (count of documents) is 0 then error "Word 沒有開啟檔案"
    set saved of active document to false
    close active document saving yes
end tell
'''


def resave(src: str, dst: str) -> None:
    if os.path.abspath(src) != os.path.abspath(dst):
        shutil.copyfile(src, dst)
    r = subprocess.run(["osascript", "-e", _SCRIPT.format(path=os.path.abspath(dst))],
                       capture_output=True, text=True, timeout=90)
    if r.returncode != 0:
        raise RuntimeError("Word 自動化失敗：" + r.stderr.strip())


def stats(path: str) -> dict:
    """讀取 Word 寫入 app.xml 的 Pages/Words/Characters（需為 Word 重存過的檔案）。"""
    with zipfile.ZipFile(path) as z:
        xml = z.read("docProps/app.xml").decode("utf-8", "ignore")
    return {k: int(m.group(1)) for k in ("Pages", "Words", "Characters")
            if (m := re.search(r"<%s>(\d+)</%s>" % (k, k), xml))}
