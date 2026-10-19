"""Refresh-time redirect resolution: syndicated URLs that redirect to one page."""
import time

import httpx
import pytest
import pytest_asyncio

from models.database import Database
from newsreader.fetcher import resolve_redirects

LONG = "https://www.nytimes.com/2026/10/16/business/us-budget-deficit-nears-2-trillion.html"
SHORT = "https://www.nytimes.com/2026/10/16/business/us-budget-deficit.html"


def _transport(redirects: dict[str, str], fail: set[str] = frozenset()):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url in fail:
            raise httpx.ConnectError("blocked", request=request)
        if url in redirects:
            return httpx.Response(301, headers={"location": redirects[url]}, request=request)
        return httpx.Response(200, text="<html>ok</html>", request=request)
    return httpx.MockTransport(handler)


def _client(redirects, fail=frozenset()):
    return httpx.AsyncClient(transport=_transport(redirects, fail), follow_redirects=True)


def _row(guid, url):
    return {"guid": guid, "url": url, "title": "US budget deficit", "summary": "",
            "content": "", "author": "", "published_at": time.time(), "image_url": "",
            "tags": [], "extra": {}}


@pytest.mark.asyncio
async def test_resolve_rewrites_redirecting_urls_to_final():
    rows = [_row(LONG, LONG), _row(SHORT, SHORT)]
    async with _client({LONG: SHORT}) as client:
        await resolve_redirects(client, rows, known={})
    assert rows[0]["url"] == SHORT
    assert rows[1]["url"] == SHORT
    # guid is the feed identity and must not change
    assert rows[0]["guid"] == LONG


@pytest.mark.asyncio
async def test_resolve_skips_known_items_and_empty_urls():
    rows = [_row("stored", LONG), _row("new-empty", ""), _row("new", LONG)]
    async with _client({LONG: SHORT}) as client:
        await resolve_redirects(client, rows, known={"stored": 1.0})
    assert rows[0]["url"] == LONG          # already stored: no lookup
    assert rows[1]["url"] == ""            # nothing to resolve
    assert rows[2]["url"] == SHORT


@pytest.mark.asyncio
async def test_resolve_keeps_original_on_failure():
    rows = [_row("a", LONG)]
    async with _client({}, fail={LONG}) as client:
        await resolve_redirects(client, rows, known={})
    assert rows[0]["url"] == LONG


@pytest.mark.asyncio
async def test_resolve_respects_per_refresh_limit(monkeypatch):
    import newsreader.fetcher as fetcher
    monkeypatch.setattr(fetcher, "_RESOLVE_LIMIT", 2)
    urls = [f"https://example.com/{i}" for i in range(5)]
    rows = [_row(u, u) for u in urls]
    async with _client({urls[0]: "https://example.com/final"}) as client:
        await resolve_redirects(client, rows, known={})
    assert rows[0]["url"] == "https://example.com/final"
    assert [r["url"] for r in rows[2:]] == urls[2:]  # beyond the cap: untouched


@pytest_asyncio.fixture()
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.init()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_redirected_syndicated_urls_store_once(db):
    sid = await db.create_source("https://rss.example.com/home.xml", name="NYT")
    rows = [_row(LONG, LONG), _row(SHORT, SHORT)]
    async with _client({LONG: SHORT}) as client:
        known = await db.known_dates(sid)
        await resolve_redirects(client, rows, known=known)
    inserted, _, _ = await db.upsert_items(sid, rows)
    assert inserted == 1
