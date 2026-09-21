"""Plugin framework primitives.

A plugin is a subclass of Plugin registered with crawler.register. Plugins
receive a FetchContext (httpx client, source row, merged config) and return
a FetchResult with parsed items.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

#: transport errors that are usually momentary and worth retrying
TRANSIENT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError,
                    httpx.ReadTimeout, httpx.RemoteProtocolError)
#: seconds between retry attempts (two retries per request)
RETRY_BACKOFF = (1.0, 2.5)


@dataclass
class ParsedItem:
    """One news item as produced by a plugin."""

    guid: str
    url: str = ""
    title: str = ""
    summary: str = ""
    content: str = ""
    author: str = ""
    published_at: float | None = None
    image_url: str = ""
    tags: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict:
        return {
            "guid": self.guid, "url": self.url, "title": self.title,
            "summary": self.summary, "content": self.content, "author": self.author,
            "published_at": self.published_at, "image_url": self.image_url,
            "tags": self.tags, "extra": self.extra,
        }


@dataclass
class FetchResult:
    items: list[ParsedItem] = field(default_factory=list)
    source_name: str | None = None
    #: plugin-provided config updates to persist on the source row
    config_updates: dict[str, Any] = field(default_factory=dict)


@dataclass
class FetchContext:
    """Everything a plugin needs to fetch one source."""

    source: dict                      # sources table row
    client: httpx.AsyncClient
    known_dates: dict[str, float | None]   # guid -> stored published_at
    config: dict                      # plugin defaults merged with source config
    logger: logging.Logger | logging.LoggerAdapter = field(default_factory=logging.getLogger)
    etag: str = ""                    # captured from responses, persisted by the fetcher
    last_modified: str = ""
    requests: int = 0

    async def get_text(self, url: str, conditional: bool = False,
                        headers: dict | None = None) -> str | None:
        """GET a URL and return its text, or None on HTTP 304.

        With conditional=True the stored ETag/Last-Modified of the source is
        sent so unchanged feeds cost nothing. Transient transport errors
        (connection resets, early server disconnects) are retried with a
        short backoff before surfacing as a source failure.
        """
        hdrs = dict(headers or {})
        if conditional:
            if self.source.get("etag"):
                hdrs.setdefault("If-None-Match", self.source["etag"])
            if self.source.get("last_modified"):
                hdrs.setdefault("If-Modified-Since", self.source["last_modified"])
        for attempt in range(len(RETRY_BACKOFF) + 1):
            try:
                self.requests += 1
                resp = await self.client.get(url, headers=hdrs)
                break
            except TRANSIENT_ERRORS as exc:
                if attempt >= len(RETRY_BACKOFF):
                    raise
                self.logger.warning("transient error fetching %s (%s), retrying",
                                    url, type(exc).__name__)
                await asyncio.sleep(RETRY_BACKOFF[attempt])
        if resp.status_code == 304:
            return None
        resp.raise_for_status()
        if resp.headers.get("ETag"):
            self.etag = resp.headers["ETag"]
        if resp.headers.get("Last-Modified"):
            self.last_modified = resp.headers["Last-Modified"]
        return resp.text


class FetchError(RuntimeError):
    pass


class Plugin:
    """Base class for source plugins. Subclass + @register to add one."""

    #: unique plugin name, stored on the source row
    name: str = "base"
    #: human readable label for the UI
    label: str = "Base"
    #: regexes matched against the source URL by can_handle
    patterns: tuple[str, ...] = ()
    #: higher priority wins when several plugins match a URL
    priority: int = 0

    def can_handle(self, url: str) -> bool:
        return any(re.search(p, url, re.I) for p in self.patterns)

    def default_config(self) -> dict:
        return {}

    async def fetch(self, ctx: FetchContext) -> FetchResult:
        raise NotImplementedError
