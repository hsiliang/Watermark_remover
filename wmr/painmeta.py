"""強制認捐額度（原疼痛指數）→ 文件統計屬性（建立時間前推分鐘數 a、編輯總時間 h、修訂版編號）。純函式，可注入亂數以便測試。"""
from __future__ import annotations

import math
import random

A_MAX_BASE = 525600      # 一年的分鐘數
A_MIN_BASE = 30
H_CAP = 35000            # h 的上限（只逼近、永不達到）
H_MIN_BASE = 30
TAU = 120000.0           # h 對 a 的飽和尺度（分鐘）


def pain_to_props(pain: float, rng: random.Random | None = None) -> dict:
    """pain: 0（不痛）~ 10（最痛）。回傳 {a, h, revision}（a、h 皆為整數分鐘）。

    a = amin * (amax/amin)^p      （對數內插：a 跨三個數量級，線性滑塊會幾乎全擠在「很久以前」）
    h = hmin + (H_CAP - hmin) * (1 - exp(-a/TAU))   （a 越大 h 越大，漸近但永遠 < H_CAP）
    h 再夾在 [hmin, a]：編輯時間不可能超過建立至今的時間。
    revision = ceil(h/100) + 1
    """
    r = rng or random
    p = min(max(float(pain), 0.0), 10.0) / 10.0
    amax = A_MAX_BASE * r.uniform(0.9, 1.1)
    amin = A_MIN_BASE * r.uniform(0.7, 1.5)
    hmin = H_MIN_BASE * r.uniform(0.9, 1.5)
    a = amin * (amax / amin) ** p
    h = hmin + (H_CAP - hmin) * (1.0 - math.exp(-a / TAU))
    h = min(h, a)                     # 編輯總時間 ≤ 建立至今
    h = max(1, min(int(round(h)), H_CAP - 1))
    return {"a": max(1, int(round(a))), "h": h, "revision": math.ceil(h / 100) + 1}
