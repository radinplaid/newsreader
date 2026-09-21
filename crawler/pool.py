"""Process pool for CPU-bound parsing.

BeautifulSoup/lxml parsing holds the GIL in long C chunks, so even
asyncio.to_thread stalls the event loop for hundreds of milliseconds on
large pages. Parse work therefore runs in real worker processes (spawn
context, so forking hazards don't apply), while pure-Python parsing and
network-bound extraction stay on worker threads.

run_parse() degrades gracefully: when the pool is disabled (0 workers),
cannot start, or broke (a worker died), the call falls back to a thread
so a refresh still completes.
"""
from __future__ import annotations

import asyncio
import logging
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from typing import Any, Callable, TypeVar

from config import settings

log = logging.getLogger("newsreader.parsepool")

T = TypeVar("T")

_executor: ProcessPoolExecutor | None = None
_pool_broken = False


def _get_executor() -> ProcessPoolExecutor | None:
    """Lazy singleton; returns None when the pool is disabled/unavailable."""
    global _executor
    if _executor is None:
        workers = int(getattr(settings, "nr_parse_workers", 0) or 0)
        if workers <= 0:
            return None
        try:
            _executor = ProcessPoolExecutor(
                max_workers=workers,
                mp_context=multiprocessing.get_context("spawn"),
            )
        except Exception as exc:  # noqa: BLE001 - restricted sandboxes etc.
            log.warning("parse process pool unavailable (%s); using threads", exc)
            return None
    return _executor


async def run_parse(fn: Callable[..., T], /, *args: Any) -> T:
    """Run fn(*args) off the event loop: in the parse process pool when
    available, otherwise in a worker thread. Positional args only (they
    and the return value must be picklable); use functools.partial for
    keyword arguments."""
    global _pool_broken
    ex = _get_executor()
    if ex is None:
        return await asyncio.to_thread(fn, *args)
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(ex, fn, *args)
    except BrokenProcessPool:
        _executor = None  # recreate a fresh pool on the next call
        if not _pool_broken:
            _pool_broken = True
            log.warning("a parse worker died; falling back to threads")
        return await asyncio.to_thread(fn, *args)


def shutdown_parse_pool() -> None:
    """Stop worker processes (queued jobs cancelled, running ones finish)."""
    global _executor, _pool_broken
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None
    _pool_broken = False
