"""YouTube sources: playlists, channels, user pages and single videos.

Uses yt-dlp for robust extraction (it tracks YouTube's changing internal
API). The playlist listing is a cheap flat extraction; for videos that are
new to the database a full per-video extraction fetches the upload date
and description (bounded and parallel). Anonymous clients hit bot checks
and members-only videos: those extractions fail fast, sticky failures are
remembered between refreshes, and a run gives up early once errors pile
up so one blocked refresh stays cheap.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

from crawler import register
from crawler.base import Plugin, FetchContext, FetchResult, ParsedItem

_FLAT_OPTS = {
    "quiet": True, "no_warnings": True, "skip_download": True,
    "extract_flat": "in_playlist", "socket_timeout": 30, "retries": 2,
}
_FULL_OPTS = {
    "quiet": True, "no_warnings": True, "skip_download": True,
    "socket_timeout": 15, "retries": 0, "extractor_retries": 0,
}

_STICKY_ERROR_MARKERS = (
    "sign in to confirm", "not a bot", "join this channel",
    "channel's members", "members-only", "members only",
    "video unavailable", "private video", "age-restricted",
)
_STICKY_SKIP_SECS = 3 * 86400
_SKIP_MAP_MAX = 1000


def _extract(url: str, opts: dict) -> dict:
    import yt_dlp
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.sanitize_info(ydl.extract_info(url, download=False))


def _sticky_failure(exc: BaseException) -> bool:
    """True when retrying this video later cannot help (bot check,
    members-only, removed video) and it should be skipped for a while."""
    msg = str(exc).lower()
    return any(m in msg for m in _STICKY_ERROR_MARKERS)


def _upload_epoch(info: dict) -> float | None:
    ts = info.get("timestamp")
    if ts:
        return float(ts)
    upload_date = info.get("upload_date")
    if upload_date:
        try:
            dt = datetime.strptime(upload_date, "%Y%m%d")
            return dt.replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            return None
    return None


@register
class YouTubePlugin(Plugin):
    name = "youtube"
    label = "YouTube playlist / channel / video"
    patterns = (
        r"https?://(www\.|m\.)?youtube\.com/(playlist\?|watch\?|channel/|c/|@|user/)",
        r"https?://(www\.)?youtu\.be/",
    )
    priority = 25

    def default_config(self) -> dict:
        return {"max_items": 300, "fetch_video_dates": True, "date_fetch_limit": 100,
                "date_fetch_max_errors": 5}

    async def fetch(self, ctx: FetchContext) -> FetchResult:
        url = ctx.source["url"]
        limit = int(ctx.config.get("max_items", 300))
        opts = {**_FLAT_OPTS, "playlistend": limit}
        info = await asyncio.to_thread(_extract, url, opts)
        source_name = info.get("title") or None
        items: list[ParsedItem] = []
        entries = info.get("entries") or []
        if info.get("_type") == "playlist" or entries:
            for entry in entries:
                vid = entry.get("id")
                if not vid:
                    continue
                watch_url = entry.get("url") or f"https://www.youtube.com/watch?v={vid}"
                if "youtu.be/" in watch_url or "/watch" not in watch_url:
                    watch_url = f"https://www.youtube.com/watch?v={vid}"
                thumb = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
                items.append(ParsedItem(
                    guid=str(vid), url=watch_url,
                    title=entry.get("title") or vid,
                    summary=(entry.get("description") or "")[:500],
                    author=entry.get("uploader") or entry.get("channel") or "",
                    image_url=thumb,
                    published_at=_upload_epoch(entry) if entry.get("timestamp") else None,
                ))
        else:
            vid = info.get("id")
            if vid:
                items.append(ParsedItem(
                    guid=str(vid), url=f"https://www.youtube.com/watch?v={vid}",
                    title=info.get("title") or vid,
                    summary=(info.get("description") or "")[:500],
                    author=info.get("uploader") or "",
                    image_url=info.get("thumbnail") or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                    published_at=_upload_epoch(info),
                ))

        items = items[:limit]

        config_updates = {}
        if ctx.config.get("fetch_video_dates", True):
            skip = await self._fetch_dates(ctx, items)
            if skip is not None:
                config_updates["date_fetch_skip"] = skip
        return FetchResult(items=items, source_name=source_name,
                           config_updates=config_updates)

    async def _fetch_dates(self, ctx: FetchContext, items: list[ParsedItem]) -> dict | None:
        """Fetch upload dates/descriptions for new and still-undated videos.

        Returns the updated date_fetch_skip map (guid -> failed-at time) when
        it changed, so the fetcher can persist it on the source row: videos
        that failed with a sticky error are not retried until their entry
        expires, and once date_fetch_max_errors extractions fail in one run
        the rest are skipped (YouTube is bot checking or rate limiting, so
        hammering on cannot succeed).
        """
        limit = int(ctx.config.get("date_fetch_limit", 100))
        max_errors = max(1, int(ctx.config.get("date_fetch_max_errors", 5)))
        now = time.time()
        raw_skip = ctx.config.get("date_fetch_skip") or {}
        skip = {g: ts for g, ts in raw_skip.items()
                if isinstance(ts, (int, float)) and ts > now - _STICKY_SKIP_SECS}
        # new videos first, then known-but-still-undated ones (backfill);
        # videos the DB already dates are never re-extracted, so the
        # per-refresh budget keeps reaching the undated tail
        need = [it for it in items
                if (it.guid not in ctx.known_dates
                    or (ctx.known_dates[it.guid] is None and it.published_at is None))
                and it.guid not in skip][:limit]
        if not need:
            return skip if skip != raw_skip else None
        sem = asyncio.Semaphore(6)
        logger = ctx.logger
        errors = 0
        bail = False

        async def one(item: ParsedItem) -> None:
            nonlocal errors, bail
            async with sem:
                if bail:
                    return
                try:
                    info = await asyncio.to_thread(_extract, item.url, _FULL_OPTS)
                except Exception as exc:  # noqa: BLE001 - date fetch is best effort
                    errors += 1
                    logger.debug("yt date fetch failed for %s: %s", item.url, exc)
                    if _sticky_failure(exc):
                        skip[item.guid] = time.time()
                    if not bail and errors >= max_errors:
                        bail = True
                        logger.warning(
                            "yt date fetch giving up after %d errors (bot check or "
                            "rate limit?); remaining videos wait for a later refresh",
                            errors)
                    return
                item.published_at = _upload_epoch(info) or item.published_at
                if info.get("description"):
                    item.summary = info["description"][:500]
                    item.content = info["description"][:4000]
                if info.get("uploader"):
                    item.author = info["uploader"]
                if info.get("thumbnail"):
                    item.image_url = info["thumbnail"]

        await asyncio.gather(*(one(it) for it in need), return_exceptions=True)
        if len(skip) > _SKIP_MAP_MAX:
            keep = sorted(skip, key=skip.get, reverse=True)[:_SKIP_MAP_MAX]
            skip = {g: skip[g] for g in keep}
        return skip if skip != raw_skip else None
