"""Text/date helpers shared by plugins."""
from __future__ import annotations

import calendar
import re
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_MONTH_RE = r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
DATE_TEXT_PATTERNS = [
    # Sep 10, 2026 / September 10, 2026
    re.compile(rf"\b{_MONTH_RE}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", re.I),
    # 10 Sep 2026 / 10th September 2026
    re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MONTH_RE},?\s+(\d{{4}})\b", re.I),
    # 2026-09-10
    re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"),
    # Submitted 13 September, 2026
    re.compile(rf"\bSubmitted\s+(\d{{1,2}})\s+{_MONTH_RE},?\s+(\d{{4}})\b", re.I),
    # September 2026 (card dates often omit the day)
    re.compile(rf"\b{_MONTH_RE}\.?\s+(\d{{4}})\b", re.I),
]


def clean(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def absolutize(base_url: str, href: str | None) -> str:
    return urljoin(base_url, (href or "").strip())


def host_of(url: str) -> str:
    return (urlparse(url).netloc or "").lower().removeprefix("www.")


def soup_from(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def text_of(element) -> str:
    if element is None:
        return ""
    return clean(element.get_text(" ", strip=True))


def first_image_src(element, base_url: str = "") -> str:
    """Best-effort image URL from an <img> (srcset aware).

    When base_url is given the result is absolutized, so root-relative
    srcs (e.g. "/_astro/cover.webp") resolve against the source site."""
    if element is None:
        return ""
    img = element if element.name == "img" else element.find("img")
    if img is None:
        return ""
    val = ""
    for attr in ("src", "data-src", "data-lazy-src"):
        val = img.get(attr)
        if val:
            break
    if not val:
        srcset = img.get("srcset") or img.get("data-srcset") or ""
        if srcset:
            val = srcset.split(",")[0].strip().split(" ")[0]
    val = (val or "").strip()
    if not val:
        return ""
    return absolutize(base_url, val) if base_url else val


def _mktime(y: int, m: int, d: int) -> float | None:
    try:
        return calendar.timegm((int(y), int(m), int(d), 12, 0, 0, 0, 0, 0))
    except (ValueError, OverflowError):
        return None


def parse_date(text: str | None) -> float | None:
    """Parse common date formats to a UTC epoch (noon to dodge TZ edges)."""
    if not text:
        return None
    text = clean(text)
    # ISO 8601 / RFC 3339 (with Z or offsets)
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?"
                 r"(Z|[+-]\d{2}:?\d{2})?)?", text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if m.group(4):
            hh, mm, ss = int(m.group(4)), int(m.group(5) or 0), int(m.group(6) or 0)
        else:
            hh, mm, ss = 12, 0, 0  # date-only: noon dodges TZ edges
        try:
            if m.group(7) in (None, "Z"):
                return calendar.timegm((y, mo, d, hh, mm, ss, 0, 0, 0))
            sign = 1 if m.group(7)[0] == "+" else -1
            digits = m.group(7)[1:].replace(":", "")
            offset = sign * (int(digits[:2]) * 3600 + int(digits[2:4]) * 60)
            return calendar.timegm((y, mo, d, hh, mm, ss, 0, 0, 0)) - offset
        except (ValueError, OverflowError):
            return None
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
                "%d %b %Y %H:%M:%S %z", "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt.hour == 0 and dt.minute == 0 and "%H" not in fmt:
                dt = dt.replace(hour=12)  # date-only formats: noon
            return dt.timestamp()
        except ValueError:
            continue
    # month-name fallbacks embedded in longer text
    m = re.search(rf"\b{_MONTH_RE}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b", text, re.I)
    if m:
        ts = _mktime(m.group(3), MONTHS[m.group(1)[:3].lower()], m.group(2))
        if ts:
            return ts
    m = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MONTH_RE},?\s+(\d{{4}})\b", text, re.I)
    if m:
        ts = _mktime(m.group(3), MONTHS[m.group(2)[:3].lower()], m.group(1))
        if ts:
            return ts
    return None


def find_date_in_text(text: str | None) -> float | None:
    if not text:
        return None
    for pattern in DATE_TEXT_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        groups = m.groups()
        try:
            if pattern.pattern.startswith(r"\b(\d{4})-"):  # ISO y-m-d
                return _mktime(groups[0], groups[1], groups[2])
            if len(groups) == 2:  # "Month YYYY" → first of the month
                return _mktime(groups[1], MONTHS[groups[0][:3].lower()], 1)
            if groups[0].isdigit():  # day-first or "Submitted D Month, Y"
                day = groups[0]
                month = groups[1]
                year = groups[2]
            else:  # month-first
                month = groups[0]
                day = groups[1]
                year = groups[2]
            return _mktime(year, MONTHS[str(month)[:3].lower()], day)
        except (KeyError, ValueError, IndexError):
            continue
    return None


def strip_tags(html: str, limit: int = 0) -> str:
    text = clean(BeautifulSoup(html or "", "lxml").get_text(" ", strip=True))
    return text[:limit] if limit else text


def timeago(epoch: float | None) -> str:
    """Human 'x minutes ago' style string."""
    if not epoch:
        return ""
    delta = max(0, time.time() - epoch)
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    if delta < 2592000:
        return f"{int(delta // 86400)}d ago"
    if delta < 31536000:
        return f"{max(1, int(delta // 2592000))}mo ago"
    return f"{int(delta // 31536000)}y ago"
