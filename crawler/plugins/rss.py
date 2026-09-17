"""RSS / Atom feeds."""
from __future__ import annotations

import asyncio
import calendar
from dataclasses import dataclass

import feedparser

from crawler import register
from crawler.base import Plugin, FetchContext, FetchResult, ParsedItem
from crawler.textutil import absolutize, clean, first_image_src, strip_tags


@dataclass
class _Entry:
    pass


def _epoch(struct_time) -> float | None:
    try:
        return calendar.timegm(struct_time)
    except (TypeError, ValueError):
        return None


def _entry_image(entry) -> str:
    for key in ("media_content", "media_thumbnail"):
        media = entry.get(key) or []
        if media and media[0].get("url"):
            return media[0]["url"]
    for enc in entry.get("enclosures", []) or []:
        if str(enc.get("type", "")).startswith("image/") and enc.get("href"):
            return enc["href"]
    html = entry.get("summary") or entry.get("description") or ""
    if "<img" in html:
        from crawler.textutil import soup_from
        soup = soup_from(html)
        tag = soup.find("img")
        if tag and first_image_src(tag):
            return absolutize(entry.get("link") or "", first_image_src(tag))
    return ""


def _parse_feed(text: str) -> tuple[list[ParsedItem], str | None]:
    parsed = feedparser.parse(text)
    items: list[ParsedItem] = []
    for entry in parsed.entries:
        guid = clean(entry.get("id") or entry.get("link"))
        if not guid:
            continue
        content = ""
        if entry.get("content"):
            content = strip_tags(entry["content"][0].get("value", ""))
        if not content:
            content = strip_tags(entry.get("description") or "")
        summary = strip_tags(entry.get("summary") or entry.get("description") or "")
        if not summary and content:
            summary = content[:300]
        published = None
        for key in ("published_parsed", "updated_parsed", "created_parsed"):
            if entry.get(key):
                published = _epoch(entry[key])
                if published:
                    break
        tags = [clean(t.get("term") or "") for t in entry.get("tags", []) or []]
        items.append(ParsedItem(
            guid=guid,
            url=entry.get("link") or "",
            title=clean(entry.get("title") or guid),
            summary=summary,
            content=content,
            author=clean(entry.get("author") or ""),
            published_at=published,
            image_url=_entry_image(entry),
            tags=[t for t in tags if t and len(t) < 40][:5],
        ))
    feed_title = clean(parsed.feed.get("title") or "") or None
    return items, feed_title


@register
class RSSPlugin(Plugin):
    name = "rss"
    label = "RSS / Atom feed"
    patterns = (
        r"\.(xml|rss|atom)([?#]|$)",
        r"/(feed|rss|atom)/?([?#]|$)",
        r"/feeds?/",
        r"feedburner\.com/",
    )
    priority = 30

    def default_config(self) -> dict:
        return {"max_items": 200}

    async def fetch(self, ctx: FetchContext) -> FetchResult:
        text = await ctx.get_text(ctx.source["url"], conditional=True)
        if text is None:
            return FetchResult(items=[])
        items, feed_title = await asyncio.to_thread(_parse_feed, text)
        if not items and not feed_title:
            from crawler.base import FetchError
            raise FetchError("no entries found and feed has no title (not a valid feed?)")
        limit = int(ctx.config.get("max_items", 200))
        return FetchResult(items=items[:limit], source_name=feed_title)
