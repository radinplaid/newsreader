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
#: how many videos are extracted at the same time
_DATE_CONCURRENCY = 6


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


def _config_updates(skip: dict, raw_skip: dict, cursor: int, raw_cursor,
                    size: int) -> dict | None:
    """The source config changes worth persisting, or None if there are none."""
    updates: dict = {}
    if skip != raw_skip:
        updates["date_fetch_skip"] = skip
    if size and cursor != raw_cursor:
        updates["date_fetch_cursor"] = cursor
    return updates or None


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
            updates = await self._fetch_dates(ctx, items)
            if updates:
                config_updates.update(updates)
        return FetchResult(items=items, source_name=source_name,
                           config_updates=config_updates)

    async def _fetch_dates(self, ctx: FetchContext, items: list[ParsedItem]) -> dict | None:
        """Fetch upload dates/descriptions for new and still-undated videos.

        Returns the config updates to persist on the source row: the videos
        that failed with a sticky error (not retried until their entry
        expires) and the cursor that says where in the backlog to pick up
        next time.

        The cursor is what keeps the backlog moving. A run gives up after a
        few errors (YouTube is bot checking), and without a cursor every run
        started at the same videos again - the ones behind them never got a
        date at all. Rotating the backlog means each video gets its turn
        across refreshes, even while YouTube is refusing to answer.
        """
        limit = int(ctx.config.get("date_fetch_limit", 100))
        max_errors = max(1, int(ctx.config.get("date_fetch_max_errors", 5)))
        now = time.time()
        raw_skip = ctx.config.get("date_fetch_skip") or {}
        skip = {g: ts for g, ts in raw_skip.items()
                if isinstance(ts, (int, float)) and ts > now - _STICKY_SKIP_SECS}
        raw_cursor = ctx.config.get("date_fetch_cursor")
        cursor = int(raw_cursor) if isinstance(raw_cursor, (int, float)) else 0

        # new videos first: there are only a handful of them per refresh and
        # they are the ones the reader is waiting for
        fresh = [it for it in items
                 if it.guid not in ctx.known_dates and it.guid not in skip]
        # then the backlog: videos that are stored but still have no date
        backlog = [it for it in items
                   if it.guid in ctx.known_dates and ctx.known_dates[it.guid] is None
                   and it.published_at is None and it.guid not in skip]
        # rotate the backlog, so a run that bails out early still moves on
        size = len(backlog)
        if size:
            cursor %= size
            backlog = backlog[cursor:] + backlog[:cursor]

        # the backlog always keeps at least half of the budget: it is what
        # the rotation is about, and there are only a few new videos anyway
        need_fresh = fresh[:max(1, limit // 2)]
        need_backlog = backlog[:max(0, limit - len(need_fresh))]
        need = need_fresh + need_backlog
        if not need:
            return _config_updates(skip, raw_skip, cursor, raw_cursor, size)

        logger = ctx.logger
        errors = 0

        async def one(item: ParsedItem) -> None:
            nonlocal errors
            try:
                info = await asyncio.to_thread(_extract, item.url, _FULL_OPTS)
            except Exception as exc:  # noqa: BLE001 - date fetch is best effort
                errors += 1
                logger.debug("yt date fetch failed for %s: %s", item.url, exc)
                if _sticky_failure(exc):
                    skip[item.guid] = time.time()
                return
            item.published_at = _upload_epoch(info) or item.published_at
            if info.get("description"):
                item.summary = info["description"][:500]
                item.content = info["description"][:4000]
            if info.get("uploader"):
                item.author = info["uploader"]
            if info.get("thumbnail"):
                item.image_url = info["thumbnail"]

        # in batches, so giving up after too many errors is decided between
        # batches instead of in the middle of one
        for start in range(0, len(need), _DATE_CONCURRENCY):
            if errors >= max_errors:
                logger.warning(
                    "yt date fetch giving up after %d errors (bot check or "
                    "rate limit?); the rest of the backlog waits for a later "
                    "refresh", errors)
                break
            batch = need[start:start + _DATE_CONCURRENCY]
            await asyncio.gather(*(one(it) for it in batch), return_exceptions=True)

        if len(skip) > _SKIP_MAP_MAX:
            keep = sorted(skip, key=skip.get, reverse=True)[:_SKIP_MAP_MAX]
            skip = {g: skip[g] for g in keep}
        # the cursor moves past the videos that were taken into the budget,
        # so a run that gave up early still makes progress on the backlog
        next_cursor = (cursor + len(need_backlog)) % size if size else cursor
        return _config_updates(skip, raw_skip, next_cursor, raw_cursor, size)
