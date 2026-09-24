"""Parallel source refresh orchestrator.

Runs one plugin per enabled source concurrently (bounded by a semaphore so
1000+ sources stay polite and stable), inserts new items, and records per
source refresh state.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict

import httpx

from config import settings

import crawler
from crawler.base import FetchContext, FetchError
from models.database import Database

log = logging.getLogger("newsreader.fetcher")

USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_ERROR_BACKOFF_BASE = 30 * 60.0     # seconds to rest after the first failure
_ERROR_BACKOFF_MAX = 24 * 3600.0    # cap for the exponential backoff


def _error_backoff_secs(fail_streak: int) -> float:
    """Seconds a source with fail_streak consecutive errors should rest:
    30 min, doubling per failure, capped at a day."""
    return min(_ERROR_BACKOFF_MAX, _ERROR_BACKOFF_BASE * (2 ** max(0, fail_streak - 1)))


def _in_error_backoff(src: dict, now: float) -> bool:
    """True while a repeatedly failing source should be left alone."""
    streak = int(src.get("error_streak") or 0)
    if streak < 1 or src.get("last_status") != "error":
        return False
    return now - (src.get("last_refreshed_at") or 0) < _error_backoff_secs(streak)


def _error_text(exc: BaseException) -> str:
    """Readable error line; walks the cause chain when str(exc) is empty.

    HTTP errors lead with the status code and a one-line response excerpt so
    the UI can surface them for debugging (collapsed by default).
    """
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        resp = exc.response
        try:
            url = resp.request.url
        except Exception:  # noqa: BLE001 - the request may already be gone
            url = "?"
        detail = f"HTTP {resp.status_code} {resp.reason_phrase} from {url}".rstrip()
        excerpt = " ".join((resp.text or "").split())[:300]
        if excerpt:
            detail += f" — response: {excerpt}"
        return detail[:800]
    detail = str(exc).strip()
    if not detail:
        seen, cur = {id(exc)}, exc.__cause__ or exc.__context__
        while cur is not None and id(cur) not in seen:
            seen.add(id(cur))
            if str(cur).strip():
                detail = f"{type(cur).__name__}: {str(cur).strip()}"
                break
            cur = cur.__cause__ or cur.__context__
    return f"{type(exc).__name__}: {detail or repr(exc)}"[:500]


class RefreshBusy(RuntimeError):
    pass


class EventBus:
    """Tiny in-process pub-sub; fans refresh events out to SSE subscribers.

    publish() never blocks or raises: a slow client just misses events and
    re-syncs from the snapshot sent when it (re)connects.
    """

    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=128)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, event: dict) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass


class Fetcher:
    """Owns refresh runs and their status."""

    def __init__(self, db: Database):
        self.db = db
        self._running = False
        self.events = EventBus()
        self.status: dict = {
            "running": False, "started_at": None, "finished_at": None,
            "total": 0, "done": 0, "skipped": 0, "inserted": 0, "updated": 0,
            "errors": 0, "current": "", "results": [],
        }
        self._task: asyncio.Task | None = None

    # -- status ---------------------------------------------------------
    def snapshot(self) -> dict:
        return dict(self.status)

    @property
    def running(self) -> bool:
        return self._running

    # -- entry points -----------------------------------------------------
    def start(self, source_ids: list[int] | None = None,
              force: bool = False) -> dict:
        if self._running:
            raise RefreshBusy("a refresh is already running")
        self._task = asyncio.create_task(self._run(source_ids, force))
        return self.snapshot()

    async def run_blocking(self, source_ids: list[int] | None = None,
                           force: bool = False) -> dict:
        if self._running:
            raise RefreshBusy("a refresh is already running")
        return await self._run(source_ids, force)

    # -- implementation ----------------------------------------------------
    async def _run(self, source_ids: list[int] | None = None,
                   force: bool = False) -> dict:
        self._running = True
        self.status.update({
            "running": True, "started_at": time.time(), "finished_at": None,
            "total": 0, "done": 0, "skipped": 0, "inserted": 0, "updated": 0,
            "errors": 0, "current": "", "results": [],
        })
        try:
            if source_ids:
                sources = [s for s in [await self.db.get_source(i) for i in source_ids] if s]
            else:
                sources = [s for s in await self.db.list_sources() if s["enabled"]]
            min_minutes = settings.nr_min_refresh_minutes
            if not force:
                now = time.time()
                cutoff = now - min_minutes * 60 if min_minutes > 0 else None
                pending, skipped = [], 0
                for s in sources:
                    if cutoff is not None and (s.get("last_refreshed_at") or 0) > cutoff:
                        skipped += 1
                    elif _in_error_backoff(s, now):
                        skipped += 1
                    else:
                        pending.append(s)
                if skipped:
                    self.status["skipped"] = skipped
                    log.info("skipping %d source(s): refreshed recently or resting "
                             "after repeated errors", skipped)
                sources = pending
            self.status["total"] = len(sources)
            self.events.publish({"type": "snapshot", **self.snapshot()})
            concurrency = max(1, settings.nr_max_concurrency)
            timeout = httpx.Timeout(settings.nr_request_timeout)
            sem = asyncio.Semaphore(concurrency)
            async with httpx.AsyncClient(
                timeout=timeout, follow_redirects=True,
                headers={"User-Agent": USER_AGENT, "Accept-Language": "en",
                         # avoid httpx/brotli decoding bugs; gzip is fine for our use
                         "Accept-Encoding": "gzip, deflate",
                         "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/rss+xml;q=0.8,*/*;q=0.7"},
            ) as client:
                tasks = [asyncio.create_task(self._refresh_one(client, src, sem))
                         for src in sources]
                for chunk_done in asyncio.as_completed(tasks):
                    result = await chunk_done
                    self.status["done"] += 1
                    self.status["inserted"] += result["inserted"]
                    self.status["updated"] += result["updated"]
                    self.status["errors"] += 1 if result["status"] == "error" else 0
                    self.status["current"] = result["source"]
                    self.status["results"].append(result)
                    self.events.publish({
                        "type": "source", "result": result,
                        **{k: self.status[k] for k in
                           ("total", "done", "inserted", "errors")},
                    })
            return self.snapshot()
        finally:
            self._running = False
            self.status["running"] = False
            self.status["finished_at"] = time.time()
            self.events.publish({"type": "done", "status": self.snapshot()})

    async def _refresh_one(self, client: httpx.AsyncClient, src: dict,
                           sem: asyncio.Semaphore) -> dict:
        name = src["name"] or src["url"]
        result = {
            "source_id": src["id"], "source": name, "url": src["url"],
            "plugin": src["plugin"] or "", "inserted": 0, "updated": 0,
            "inserted_ids": [],
            "status": "ok", "error": "", "requests": 0, "duration": 0.0,
        }
        async with sem:
            started = time.time()
            try:
                plugin = None
                if src["plugin"]:
                    plugin = crawler.plugin_by_name(src["plugin"])
                if plugin is None:
                    plugin = crawler.find_plugin(src["url"])
                    if plugin is None:
                        raise FetchError(f"no plugin can handle {src['url']}")
                if plugin.name == "web":
                    # self-heal sources stored before a dedicated plugin
                    # matched their URL: prefer the dedicated match (the
                    # row's plugin field is updated after the refresh)
                    better = crawler.find_plugin(src["url"])
                    if better is not None and better.name != plugin.name:
                        plugin = better
                result["plugin"] = plugin.name
                config = {**plugin.default_config(), **(src.get("config") or {})}
                known = await self.db.known_dates(src["id"])
                ctx = FetchContext(
                    source=src, client=client, known_dates=known, config=config,
                    logger=logging.LoggerAdapter(log, {"source": name}),
                )
                fetched = await plugin.fetch(ctx)
                rows = [item.to_row() for item in fetched.items]
                inserted, updated, new_ids = await self.db.upsert_items(src["id"], rows)
                result["inserted"], result["updated"] = inserted, updated
                # capped list of fresh row ids: lets the web client surface
                # "N new" and prepend them without reloading the whole list
                result["inserted_ids"] = new_ids[:500]
                if fetched.source_name and not (src.get("name") or ""):
                    result["source_name"] = fetched.source_name
                    await self.db.update_source(src["id"], name=fetched.source_name)
                if fetched.config_updates:
                    merged = {**(src.get("config") or {}), **fetched.config_updates}
                    await self.db.update_source(src["id"], config=merged)
                fields = {
                    "last_refreshed_at": time.time(), "last_status": "ok", "last_error": "",
                    "plugin": plugin.name, "error_streak": 0,
                }
                if ctx.etag:
                    fields["etag"] = ctx.etag
                if ctx.last_modified:
                    fields["last_modified"] = ctx.last_modified
                await self.db.update_source(src["id"], **fields)
                result["requests"] = ctx.requests
            except Exception as exc:  # Catch all to isolate failures
                result["status"] = "error"
                result["error"] = _error_text(exc)
                log.exception("refresh failed for %s", src["url"])
                await self.db.update_source(
                    src["id"], last_refreshed_at=time.time(),
                    last_status="error", last_error=result["error"],
                    error_streak=int(src.get("error_streak") or 0) + 1,
                )
            result["duration"] = round(time.time() - started, 2)
        return result
