"""把 docx 統計資料（core.xml）的建立／修改時間，同步到檔案系統的建立／修改時間（Word「內容 → 通用」分頁看到的那組）。

macOS：先把 mtime 設成 created（系統會連 birthtime 一起拉早），再把 mtime 設成 modified（birthtime 不會被拉回）。
Windows：用 SetFileTime 直接設建立／存取／修改時間。
Linux：沒有可設定的建立時間，只能設修改時間。
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import zipfile

from lxml import etree

from .common import NS_DCTERMS


def read_times(path: str):
    """回傳 (created, modified)，皆為 UTC-aware datetime；缺少或格式錯誤則報錯。"""
    with zipfile.ZipFile(path) as z:
        try:
            root = etree.fromstring(z.read("docProps/core.xml"))
        except KeyError:
            raise ValueError("檔案沒有 docProps/core.xml（不是 Word 產物？）")
    out = []
    for tag in ("created", "modified"):
        e = root.find("{%s}%s" % (NS_DCTERMS, tag))
        if e is None or not (e.text or "").strip():
            raise ValueError("統計資料缺少 %s 時間" % tag)
        t = e.text.strip().replace("Z", "+00:00")
        try:
            d = _dt.datetime.fromisoformat(t)
        except ValueError:
            raise ValueError("無法解析 %s 時間：%s" % (tag, e.text))
        out.append(d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc))
    return out[0], out[1]


def _set_windows(path, created, modified):
    import ctypes
    from ctypes import wintypes

    def ft(d):
        v = int((d.timestamp() + 11644473600) * 10_000_000)
        return wintypes.FILETIME(v & 0xFFFFFFFF, v >> 32)

    k = ctypes.windll.kernel32
    k.CreateFileW.restype = wintypes.HANDLE
    h = k.CreateFileW(path, 0x100, 0, None, 3, 0x80, None)  # FILE_WRITE_ATTRIBUTES, OPEN_EXISTING
    if h in (None, wintypes.HANDLE(-1).value):
        raise OSError("無法開啟檔案設定時間")
    try:
        c, m = ft(created), ft(modified)
        if not k.SetFileTime(h, ctypes.byref(c), ctypes.byref(m), ctypes.byref(m)):
            raise OSError("SetFileTime 失敗")
    finally:
        k.CloseHandle(h)


def sync_file_times(path: str, created=None, modified=None) -> dict:
    """依 docx 統計資料設定檔案時間；回傳實際套用的說明。"""
    if created is None or modified is None:
        c, m = read_times(path)
        created, modified = created or c, modified or m
    if modified < created:
        modified = created
    if sys.platform == "win32":
        _set_windows(path, created, modified)
        note = "已設定建立／修改時間"
    elif sys.platform == "darwin":
        os.utime(path, (created.timestamp(), created.timestamp()))
        os.utime(path, (modified.timestamp(), modified.timestamp()))
        note = "已設定建立／修改時間"
    else:
        os.utime(path, (modified.timestamp(), modified.timestamp()))
        note = "此系統沒有可設定的建立時間，只設定了修改時間"
    return {"created": created, "modified": modified, "note": note}
