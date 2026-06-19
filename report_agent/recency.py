"""記事の鮮度判定。

SearXNG は publishedDate をほぼ返さないため、タイトル/URL から日付を抽出して
鮮度を判定する。本文中の日付はイベント日や発売日など publish 日でないことが多く
ノイズになるため、URL とタイトルのみを使う(抜粋は使わない)。
"""

from __future__ import annotations

import re
from datetime import date, timedelta

# time_range → 許容日数(slack 込み。day でも昨日を拾えるよう少し広め)
_WINDOW_DAYS = {"day": 2, "week": 8, "month": 32, "year": 370}

_RE_JP = re.compile(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_RE_SLASH = re.compile(r"(20\d{2})[/.\-](\d{1,2})[/.\-](\d{1,2})")
_RE_URL = re.compile(r"/(20\d{2})[/_\-](\d{1,2})[/_\-](\d{1,2})(?:[/_\-]|\b)")


def _mk(y: str, m: str, d: str) -> date | None:
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def _from_text(text: str) -> date | None:
    if not text:
        return None
    m = _RE_JP.search(text) or _RE_SLASH.search(text)
    return _mk(*m.groups()) if m else None


def _from_url(url: str) -> date | None:
    if not url:
        return None
    m = _RE_URL.search(url)
    return _mk(*m.groups()) if m else None


_RE_ISO = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")


def parse_iso(s: str) -> date | None:
    """ISO 風文字列(例 2026-06-18T09:00:00+09:00)から日付を取り出す。"""
    if not s:
        return None
    m = _RE_ISO.search(s)
    return _mk(*m.groups()) if m else None


def extract_date(title: str = "", url: str = "", meta: str = "") -> date | None:
    """meta(記事の published_time 等) > URL > タイトル の順で日付を取り出す。"""
    return parse_iso(meta) or _from_url(url) or _from_text(title)


def window_days(time_range: str | None) -> int:
    """time_range 文字列を許容日数に。未設定/不明は 0(=鮮度判定しない)。"""
    return _WINDOW_DAYS.get((time_range or "").strip(), 0)


def classify(d: date | None, days: int, today: date | None = None) -> str:
    """'fresh'(窓内) / 'old'(窓より古い) / 'unknown'(日付不明 or 未来日)。

    未来日(publish 日でない可能性が高い発売日/イベント日)は unknown 扱いにして
    誤って fresh と判定しない。
    """
    if d is None or days <= 0:
        return "unknown"
    today = today or date.today()
    delta = (today - d).days
    if delta < -3:          # 3日より先の未来 → publish 日ではない
        return "unknown"
    if delta <= days:
        return "fresh"
    return "old"
