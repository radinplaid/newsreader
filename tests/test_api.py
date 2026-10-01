"""API tests with an in-memory-ish temp DB and mocked refresh (no network)."""
import asyncio
import time

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

import api.app as app_module
from crawler.base import FetchContext
from newsreader.fetcher import EventBus, Fetcher, _error_text
from models.database import Database


@pytest_asyncio.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("NR_DB_PATH", str(tmp_path / "api.db"))
    from newsreader.config import settings
    settings.nr_db_path = str(tmp_path / "api.db")
    monkeypatch.setenv("NR_WEB_DIR", str(tmp_path / "noweb"))
    import importlib
    importlib.reload(app_module)

    # mock the per-source refresh so POST /api/refresh never hits the network
    async def fake_refresh_one(self, client_httpx, src, sem):
        await self.db.upsert_items(src["id"], [{
            "guid": "fake-1", "url": src["url"], "title": "Fake item",
            "summary": "s", "published_at": time.time(),
        }])
        await self.db.update_source(src["id"], name="Example Feed", last_status="ok",
                              last_error="", last_refreshed_at=time.time())
        return {"source_id": src["id"], "source": src["name"] or src["url"],
                "url": src["url"], "plugin": "mock", "inserted": 1, "updated": 0,
                "status": "ok", "error": "", "requests": 0, "duration": 0.0}

    monkeypatch.setattr(Fetcher, "_refresh_one", fake_refresh_one)

    with TestClient(app_module.app) as tc:
        yield tc


@pytest.mark.asyncio
async def test_health_and_categories(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True
    r = client.post("/api/categories", json={"name": "Research"})
    assert r.status_code == 200 and r.json()["name"] == "Research"
    cid = r.json()["id"]
    r = client.patch(f"/api/categories/{cid}", json={"name": "Research2"})
    assert r.status_code == 200
    cats = client.get("/api/categories").json()["categories"]
    assert cats[0]["name"] == "Research2"
    assert client.delete(f"/api/categories/{cid}").json()["ok"] is True


@pytest.mark.asyncio
async def test_source_lifecycle(client):
    cat = client.post("/api/categories", json={"name": "News"}).json()
    r = client.post("/api/sources", json={
        "url": "https://example.org/feed.xml", "category_id": cat["id"]})
    assert r.status_code == 200
    src = r.json()
    assert src["plugin"] == "rss"
    assert src["category_name"] == "News"
    sid = src["id"]
    # duplicate URL: update in place
    r2 = client.post("/api/sources", json={"url": "https://example.org/feed.xml",
                                           "name": "Example"})
    assert r2.json()["id"] == sid
    # bad url
    assert client.post("/api/sources", json={"url": "ftp://nope"}).status_code == 422
    assert client.patch(f"/api/sources/{sid}",
                        json={"enabled": False}).json()["enabled"] is False
    assert client.delete(f"/api/sources/{sid}").json()["ok"] is True
    assert client.delete(f"/api/sources/{sid}").status_code == 404


@pytest.mark.asyncio
async def test_items_search_and_tags(client):
    cat = client.post("/api/categories", json={"name": "C"}).json()
    src = client.post("/api/sources", json={
        "url": "https://example.org/feed.xml", "category_id": cat["id"]}).json()
    db: Database = client.app.state.db
    await db.upsert_items(src["id"], [
        {"guid": "g1", "url": "https://example.org/1", "title": "Quantum leaps",
         "summary": "physics things", "published_at": time.time()},
        {"guid": "g2", "url": "https://example.org/2", "title": "Pasta recipes",
         "summary": "cooking", "published_at": time.time() - 100, "tags": ["food"]},
    ])
    items = client.get("/api/items", params={"limit": 10}).json()
    assert items["total"] == 2
    assert [i["title"] for i in items["items"]] == ["Quantum leaps", "Pasta recipes"]
    hits = client.get("/api/items", params={"q": "quantum"}).json()
    assert hits["total"] == 1 and hits["items"][0]["title"] == "Quantum leaps"
    ranked = client.get("/api/items", params={"q": "pasta", "sort": "rank"}).json()
    assert ranked["total"] == 1
    by_tag = client.get("/api/items", params={"tag": "food"}).json()
    assert by_tag["total"] == 1
    by_cat = client.get("/api/items", params={"category_id": cat["id"]}).json()
    assert by_cat["total"] == 2

    item_id = items["items"][1]["id"]
    tags = client.post(f"/api/items/{item_id}/tags",
                       json={"tags": ["yum"]}).json()["tags"]
    assert "yum" in tags and "food" in tags
    tags = client.put(f"/api/items/{item_id}/tags",
                      json={"tags": ["only-one"]}).json()["tags"]
    assert tags == ["only-one"]
    detail = client.get(f"/api/items/{item_id}").json()
    assert detail["tags"] == ["only-one"] and detail["content"] == ""
    assert client.delete(f"/api/items/{item_id}/tags/only-one").json()["tags"] == []
    assert client.get("/api/tags").json()["tags"][0]["name"] == "food"


@pytest.mark.asyncio
async def test_refresh_flow(client):
    src = client.post("/api/sources", json={"url": "https://example.org/feed.xml"}).json()
    r = client.post("/api/refresh", json={"source_id": src["id"]})
    assert r.status_code == 200 and r.json()["ok"] is True
    # wait briefly for the background task
    for _ in range(50):
        status = client.get("/api/refresh/status").json()
        if not status["running"] and status["total"]:
            break
        time.sleep(0.05)
    assert status["done"] == 1 and status["inserted"] == 1
    items = client.get("/api/items").json()
    assert items["total"] == 1
    assert items["items"][0]["source_name"] == "Example Feed"


def wait_refresh_done(client):
    status = {}
    for _ in range(50):
        status = client.get("/api/refresh/status").json()
        if not status["running"] and status["finished_at"]:
            break
        time.sleep(0.05)
    return status


@pytest.mark.asyncio
async def test_refresh_min_interval_skip_and_force(client, monkeypatch):
    monkeypatch.setenv("NR_MIN_REFRESH_MINUTES", "15")
    from newsreader.config import settings
    settings.nr_min_refresh_minutes = 15
    src = client.post("/api/sources", json={"url": "https://example.org/feed.xml"}).json()

    # never refreshed yet: a plain refresh-all runs
    client.post("/api/refresh", json={})
    status = wait_refresh_done(client)
    assert status["total"] == 1 and status["done"] == 1 and status["skipped"] == 0

    # refreshed seconds ago: refresh-all skips it
    client.post("/api/refresh", json={})
    status = wait_refresh_done(client)
    assert status["total"] == 0 and status["skipped"] == 1

    # explicit single-source refresh also skips unless forced
    client.post("/api/refresh", json={"source_id": src["id"]})
    status = wait_refresh_done(client)
    assert status["total"] == 0 and status["skipped"] == 1

    client.post("/api/refresh", json={"source_id": src["id"], "force": True})
    status = wait_refresh_done(client)
    assert status["total"] == 1 and status["done"] == 1 and status["skipped"] == 0


@pytest.mark.asyncio
async def test_refresh_error_backoff_skip_and_force(client, monkeypatch):
    from newsreader.config import settings
    monkeypatch.setattr(settings, "nr_min_refresh_minutes", 0.0)
    src = client.post("/api/sources", json={"url": "https://example.org/feed.xml"}).json()
    db: Database = client.app.state.db

    # failed twice 20 min ago: the backoff is 60 min, so the source rests
    await db.update_source(src["id"], last_status="error", last_error="x",
                           error_streak=2, last_refreshed_at=time.time() - 20 * 60)
    client.post("/api/refresh", json={})
    status = wait_refresh_done(client)
    assert status["total"] == 0 and status["skipped"] == 1

    # explicit single-source refresh rests too unless forced
    client.post("/api/refresh", json={"source_id": src["id"]})
    status = wait_refresh_done(client)
    assert status["total"] == 0 and status["skipped"] == 1

    # force bypasses the backoff
    client.post("/api/refresh", json={"source_id": src["id"], "force": True})
    status = wait_refresh_done(client)
    assert status["total"] == 1 and status["done"] == 1 and status["skipped"] == 0

    # one failure 2 h ago: the 30 min backoff has expired, the source runs
    await db.update_source(src["id"], last_status="error", last_error="x",
                           error_streak=1, last_refreshed_at=time.time() - 2 * 3600)
    client.post("/api/refresh", json={})
    status = wait_refresh_done(client)
    assert status["total"] == 1 and status["done"] == 1 and status["skipped"] == 0


def test_error_backoff_grows_and_caps():
    from newsreader.fetcher import _error_backoff_secs, _in_error_backoff
    assert _error_backoff_secs(1) == 30 * 60
    assert _error_backoff_secs(2) == 60 * 60
    assert _error_backoff_secs(3) == 120 * 60
    assert _error_backoff_secs(20) == 24 * 3600
    now = 1_700_000_000.0
    resting = {"last_status": "error", "error_streak": 1}
    assert _in_error_backoff({**resting, "last_refreshed_at": now - 60}, now)
    assert not _in_error_backoff({**resting, "last_refreshed_at": now - 3600}, now)
    # healthy or never-failed sources are never held back
    assert not _in_error_backoff({"last_status": "ok", "error_streak": 0,
                                  "last_refreshed_at": now - 60}, now)
    assert not _in_error_backoff({"last_status": "error", "error_streak": 0,
                                  "last_refreshed_at": now - 60}, now)


@pytest.mark.asyncio
async def test_star_and_dismiss_endpoints(client):
    src = client.post("/api/sources", json={"url": "https://example.org/feed.xml"}).json()
    db: Database = client.app.state.db
    await db.upsert_items(src["id"], [
        {"guid": "g1", "url": "https://example.org/1", "title": "One",
         "published_at": time.time()},
        {"guid": "g2", "url": "https://example.org/2", "title": "Two",
         "published_at": time.time() - 50},
    ])
    ids = [i["id"] for i in client.get("/api/items").json()["items"]]

    assert client.post(f"/api/items/{ids[0]}/star").json()["starred"] is True
    assert client.get("/api/items", params={"starred": "true"}).json()["total"] == 1
    assert client.get("/api/items").json()["items"][0]["starred"] is True
    assert client.delete(f"/api/items/{ids[0]}/star").json()["starred"] is False
    assert client.get("/api/items", params={"starred": "true"}).json()["total"] == 0

    assert client.post(f"/api/items/{ids[1]}/dismiss").json()["dismissed"] is True
    assert client.get("/api/items").json()["total"] == 1
    assert client.get("/api/health").json()["counts"]["dismissed"] == 1
    assert client.post("/api/items/424242/star").status_code == 404
    assert client.post("/api/items/424242/dismiss").status_code == 404


@pytest.mark.asyncio
async def test_source_edit_url_reresolves_plugin(client):
    src = client.post("/api/sources", json={"url": "https://example.org/feed.xml"}).json()
    assert src["plugin"] == "rss"
    # point it at a youtube playlist: plugin re-detected, conditional state reset
    r = client.patch(f"/api/sources/{src['id']}", json={
        "url": "https://www.youtube.com/playlist?list=PLabc123", "name": "Renamed"})
    assert r.status_code == 200
    body = r.json()
    assert body["plugin"] == "youtube"
    assert body["name"] == "Renamed"
    assert body["etag"] == "" and body["last_modified"] == ""
    # invalid URL is rejected
    assert client.patch(f"/api/sources/{src['id']}",
                        json={"url": "https://ftp://bad"}).status_code == 422


@pytest.mark.asyncio
async def test_hide_source(client):
    src = client.post("/api/sources", json={"url": "https://example.org/feed.xml"}).json()
    db: Database = client.app.state.db
    await db.upsert_items(src["id"], [
        {"guid": "g1", "url": "https://example.org/1", "title": "One",
         "published_at": time.time()},
        {"guid": "g2", "url": "https://example.org/2", "title": "Two",
         "published_at": time.time() - 50},
    ])
    assert client.get("/api/items").json()["total"] == 2
    r = client.patch(f"/api/sources/{src['id']}", json={"hidden": True})
    assert r.status_code == 200 and r.json()["hidden"] is True
    assert client.get("/api/items").json()["total"] == 0
    # explicit source view still shows the hidden source's items
    assert client.get("/api/items",
                      params={"source_id": src["id"]}).json()["total"] == 2
    r = client.patch(f"/api/sources/{src['id']}", json={"hidden": False})
    assert r.json()["hidden"] is False
    assert client.get("/api/items").json()["total"] == 2


@pytest.mark.asyncio
async def test_export_import_sources(client):
    cat = client.post("/api/categories", json={"name": "News"}).json()
    client.post("/api/sources", json={
        "url": "https://example.org/feed.xml", "name": "Example Feed",
        "category_id": cat["id"]})
    client.post("/api/sources", json={
        "url": "https://www.youtube.com/playlist?list=PLabc123", "name": "Videos"})

    r = client.get("/api/sources/export")
    assert r.status_code == 200
    doc = r.json()
    assert doc["format"] == "newsreader-sources"
    assert len(doc["sources"]) == 2
    feed = next(s for s in doc["sources"] if s["url"] == "https://example.org/feed.xml")
    assert feed["name"] == "Example Feed"
    assert feed["category"] == "News"
    assert feed["plugin"] == "rss"

    # wipe and re-import into an empty-ish db (same URLs are updated in place)
    for s in client.get("/api/sources").json()["sources"]:
        client.delete(f"/api/sources/{s['id']}")
    for c in client.get("/api/categories").json()["categories"]:
        client.delete(f"/api/categories/{c['id']}")

    r = client.post("/api/sources/import", json={"sources": doc["sources"]})
    assert r.status_code == 200
    res = r.json()
    assert res["ok"] is True and res["created"] == 2 and res["skipped"] == 0

    sources = client.get("/api/sources").json()["sources"]
    assert len(sources) == 2
    feed2 = next(s for s in sources if s["url"] == "https://example.org/feed.xml")
    assert feed2["name"] == "Example Feed" and feed2["plugin"] == "rss"
    assert feed2["category_name"] == "News"

    # importing the same document again updates instead of duplicating
    r = client.post("/api/sources/import", json={"sources": doc["sources"]})
    assert r.json()["updated"] == 2 and r.json()["created"] == 0
    assert len(client.get("/api/sources").json()["sources"]) == 2

    # entries no plugin can handle are skipped
    r = client.post("/api/sources/import", json={
        "sources": [{"url": "not-a-url"}]})
    assert r.json()["skipped"] == 1


@pytest.mark.asyncio
async def test_export_import_sources_opml(client):
    cat = client.post("/api/categories", json={"name": "News"}).json()
    client.post("/api/sources", json={
        "url": "https://example.org/feed.xml", "name": "Example Feed",
        "category_id": cat["id"], "config": {"max_items": 5}})
    client.post("/api/sources", json={
        "url": "https://www.youtube.com/playlist?list=PLabc123", "name": "Videos"})

    r = client.get("/api/sources/export.opml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    assert "newsreader-sources.opml" in r.headers.get("content-disposition", "")
    xml = r.text
    assert xml.startswith("<?xml")
    assert "<opml" in xml and 'version="2.0"' in xml
    assert 'xmlUrl="https://example.org/feed.xml"' in xml
    assert 'text="News"' in xml

    # wipe and re-import into an empty-ish db
    for s in client.get("/api/sources").json()["sources"]:
        client.delete(f"/api/sources/{s['id']}")
    for c in client.get("/api/categories").json()["categories"]:
        client.delete(f"/api/categories/{c['id']}")

    r = client.post("/api/sources/import.opml", content=xml)
    assert r.status_code == 200
    res = r.json()
    assert res["ok"] is True and res["created"] == 2 and res["updated"] == 0
    assert res["skipped"] == 0

    sources = client.get("/api/sources").json()["sources"]
    assert len(sources) == 2
    feed = next(s for s in sources if s["url"] == "https://example.org/feed.xml")
    assert feed["name"] == "Example Feed" and feed["plugin"] == "rss"
    assert feed["category_name"] == "News"
    assert feed["config"] == {"max_items": 5}
    videos = next(s for s in sources if "youtube.com" in s["url"])
    assert videos["plugin"] == "youtube" and videos["category_name"] is None

    # importing the same document again updates instead of duplicating
    r = client.post("/api/sources/import.opml", content=xml)
    assert r.json()["updated"] == 2 and r.json()["created"] == 0
    assert len(client.get("/api/sources").json()["sources"]) == 2

    # an explicit plugin attribute wins over URL-based detection
    r = client.post("/api/sources/import.opml", content=(
        '<opml version="2.0"><body>'
        '<outline text="Heist" xmlUrl="https://example.org/other.xml" plugin="web"/>'
        '</body></opml>'))
    assert r.json()["created"] == 1
    heist = next(s for s in client.get("/api/sources").json()["sources"]
                 if s["url"] == "https://example.org/other.xml")
    assert heist["plugin"] == "web"

    # malformed XML is rejected
    r = client.post("/api/sources/import.opml", content=b"this is not xml")
    assert r.status_code == 422

    # URLs nothing can be done with are skipped
    r = client.post("/api/sources/import.opml", content=(
        '<opml version="2.0"><body>'
        '<outline text="Bad" xmlUrl="not-a-url"/>'
        '</body></opml>'))
    assert r.json()["skipped"] == 1


@pytest.mark.asyncio
async def test_fetcher_run_blocking_and_busy(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "f.db"))
    await db.init()
    sid = await db.create_source("https://example.org/feed.xml", "Ex", "rss")

    async def fake_refresh_one(self, client_httpx, src, sem):
        return {"source_id": src["id"], "source": src["name"], "url": src["url"],
                "plugin": "mock", "inserted": 3, "updated": 0, "status": "ok",
                "error": "", "requests": 1, "duration": 0.1}

    monkeypatch.setattr(Fetcher, "_refresh_one", fake_refresh_one)
    fetcher = Fetcher(db)
    import asyncio
    try:
        status = await fetcher.run_blocking()
        assert status["inserted"] == 3 and status["errors"] == 0
        assert status["results"][0]["source_id"] == sid
    finally:
        await db.close()


def test_event_bus_fanout_and_backpressure():
    import asyncio

    async def main():
        bus = EventBus()
        q1, q2, tiny = bus.subscribe(), bus.subscribe(), bus.subscribe()
        bus.publish({"type": "source", "n": 1})
        first = [await asyncio.wait_for(q.get(), 1) for q in (q1, q2)]
        assert first == [{"type": "source", "n": 1}] * 2
        # a full subscriber never breaks publishing for the others
        for i in range(200):
            bus.publish({"type": "source", "n": i})
        assert q1.qsize() == q1.maxsize and tiny.qsize() == tiny.maxsize
        # unsubscribed queues stop receiving
        bus.unsubscribe(q1)
        while not q1.empty():
            await q1.get()
        bus.publish({"type": "done"})
        assert q1.empty()
        bus.unsubscribe(q2)
        bus.unsubscribe(tiny)

    asyncio.run(main())


def test_error_text_walks_cause_chain():
    root = OSError(104, "Connection reset by peer")
    exc = httpx.ConnectError("")
    exc.__cause__ = root
    text = _error_text(exc)
    assert text.startswith("ConnectError:") and "Connection reset by peer" in text
    # plain exceptions keep their own message
    assert _error_text(ValueError("boom")) == "ValueError: boom"
    # nothing anywhere in the chain: still non-empty
    assert _error_text(httpx.ConnectError("")).startswith("ConnectError:")


def test_error_text_includes_http_status_and_response():
    request = httpx.Request("GET", "https://example.org/feed.xml")
    response = httpx.Response(404, request=request,
                              text="<!doctype html>\n  Page Not Found")
    exc = httpx.HTTPStatusError("Client error '404 Not Found'",
                                request=request, response=response)
    text = _error_text(exc)
    assert text.startswith("HTTP 404")
    assert "https://example.org/feed.xml" in text
    assert "response: <!doctype html> Page Not Found" in text
    # no response body: still a useful, single line
    request2 = httpx.Request("GET", "https://example.org/feed.xml")
    response2 = httpx.Response(500, request=request2, text="")
    text2 = _error_text(httpx.HTTPStatusError("x", request=request2, response=response2))
    assert text2 == "HTTP 500 Internal Server Error from https://example.org/feed.xml"


@pytest.mark.asyncio
async def test_refresh_upgrades_misrouted_web_plugin(tmp_path, monkeypatch):
    """Sources stored as the generic web plugin (before a dedicated plugin
    matched their URL) are switched to the dedicated plugin on refresh."""
    from crawler.base import FetchResult, ParsedItem
    from crawler.plugins.rss import RSSPlugin

    db = Database(str(tmp_path / "heal.db"))
    await db.init()
    try:
        sid = await db.create_source("https://www.cbc.ca/webfeed/rss/rss-topstories",
                                     "CBC", "web")
        assert (await db.get_source(sid))["plugin"] == "web"
        await db.update_source(sid, last_status="error", error_streak=3)

        async def fake_fetch(self, ctx):
            return FetchResult(items=[ParsedItem(guid="g1", url="https://x/1",
                                                 title="Top story")],
                               source_name="CBC News")

        monkeypatch.setattr(RSSPlugin, "fetch", fake_fetch)
        fetcher = Fetcher(db)
        src = await db.get_source(sid)
        async with httpx.AsyncClient() as hc:
            result = await fetcher._refresh_one(hc, src, asyncio.Semaphore(1))
        assert result["status"] == "ok"
        assert result["plugin"] == "rss"
        assert result["inserted"] == 1
        row = await db.get_source(sid)
        assert row["plugin"] == "rss"
        assert row["error_streak"] == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_refresh_failure_records_http_error(tmp_path, monkeypatch):
    db = Database(str(tmp_path / "fail.db"))
    await db.init()
    try:
        sid = await db.create_source("https://example.org/feed.xml", "Ex", "rss")
        src = await db.get_source(sid)

        async def boom(self, url, conditional=False, headers=None):
            request = httpx.Request("GET", url)
            response = httpx.Response(500, request=request, text="<html>boom</html>")
            raise httpx.HTTPStatusError("server error", request=request, response=response)

        monkeypatch.setattr(FetchContext, "get_text", boom)
        fetcher = Fetcher(db)
        async with httpx.AsyncClient() as hc:
            result = await fetcher._refresh_one(hc, src, asyncio.Semaphore(1))
        assert result["status"] == "error"
        assert "HTTP 500" in result["error"]
        assert "response: <html>boom</html>" in result["error"]
        row = await db.get_source(sid)
        assert row["last_status"] == "error"
        assert "HTTP 500" in row["last_error"]
        assert row["error_streak"] == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_read_endpoints_unread_filter_and_bulk(client):
    src = client.post("/api/sources", json={"url": "https://example.org/feed.xml"}).json()
    db: Database = client.app.state.db
    await db.upsert_items(src["id"], [
        {"guid": "g1", "url": "https://example.org/1", "title": "One",
         "published_at": time.time()},
        {"guid": "g2", "url": "https://example.org/2", "title": "Two",
         "published_at": time.time() - 50},
    ])
    items = client.get("/api/items").json()["items"]
    assert all(i["read_at"] is None for i in items)
    assert client.get("/api/items", params={"unread": "true"}).json()["total"] == 2
    assert client.get("/api/health").json()["counts"]["unread_items"] == 2

    iid = items[0]["id"]
    assert client.post(f"/api/items/{iid}/read").json()["read"] is True
    got = client.get(f"/api/items/{iid}").json()
    assert got["read_at"] is not None
    assert client.get("/api/items", params={"unread": "true"}).json()["total"] == 1
    assert client.delete(f"/api/items/{iid}/read").json()["read"] is False
    assert client.get("/api/items", params={"unread": "true"}).json()["total"] == 2
    assert client.post("/api/items/424242/read").status_code == 404
    assert client.delete("/api/items/424242/read").status_code == 404

    r = client.post("/api/items/read-all", json={"source_id": src["id"]})
    assert r.json()["updated"] == 2
    assert client.get("/api/items", params={"unread": "true"}).json()["total"] == 0
    # bulk unread over explicit ids
    r = client.post("/api/items/read-all",
                    json={"ids": [items[1]["id"]], "read": False})
    assert r.json()["updated"] == 1
    assert client.get("/api/items", params={"unread": "true"}).json()["total"] == 1
    # empty ids list touches nothing
    r = client.post("/api/items/read-all", json={"ids": [], "read": False})
    assert r.json()["updated"] == 0


@pytest.mark.asyncio
async def test_undismiss_endpoint(client):
    src = client.post("/api/sources", json={"url": "https://example.org/feed.xml"}).json()
    db: Database = client.app.state.db
    await db.upsert_items(src["id"], [
        {"guid": "g1", "url": "https://example.org/1", "title": "One",
         "published_at": time.time()},
    ])
    iid = client.get("/api/items").json()["items"][0]["id"]
    assert client.post(f"/api/items/{iid}/dismiss").json()["dismissed"] is True
    assert client.get("/api/items").json()["total"] == 0
    assert client.get("/api/health").json()["counts"]["dismissed"] == 1
    # undo: the item is back in lists, search and counts
    assert client.delete(f"/api/items/{iid}/dismiss").json()["dismissed"] is False
    assert client.get("/api/items").json()["total"] == 1
    assert client.get("/api/health").json()["counts"]["dismissed"] == 0
    assert client.delete("/api/items/424242/dismiss").status_code == 404


@pytest.mark.asyncio
async def test_items_since_until_and_ids_params(client):
    src = client.post("/api/sources", json={"url": "https://example.org/feed.xml"}).json()
    db: Database = client.app.state.db
    now = 1_700_000_000  # fixed epoch (2023-11-14 UTC): no midnight flakiness
    await db.upsert_items(src["id"], [
        {"guid": "old", "url": "u1", "title": "Old", "published_at": now - 3000},
        {"guid": "fresh", "url": "u2", "title": "Fresh", "published_at": now - 10},
    ])
    got = client.get("/api/items", params={"since": now - 300}).json()
    assert [i["guid"] for i in got["items"]] == ["fresh"]
    got = client.get("/api/items", params={"until": now - 100}).json()
    assert [i["guid"] for i in got["items"]] == ["old"]
    ids = ",".join(str(i["id"]) for i in client.get("/api/items").json()["items"])
    got = client.get("/api/items", params={"ids": ids, "since": now - 300}).json()
    assert [i["guid"] for i in got["items"]] == ["fresh"]
    # ISO dates work too; a date-only until includes that whole day
    assert client.get("/api/items", params={"since": "2023-11-14"}).json()["total"] == 2
    assert client.get("/api/items", params={"until": "2023-11-14"}).json()["total"] == 2
    assert client.get("/api/items", params={"since": "2023-11-15"}).json()["total"] == 0
    # garbage is rejected, not silently ignored
    assert client.get("/api/items", params={"since": "not-a-date"}).status_code == 422
    assert client.get("/api/items", params={"ids": "a,b"}).status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("plugin", ["rss", "youtube", "reddit", "arxiv", "web"])
@pytest.mark.parametrize("format", ["json", "opml"])
async def test_source_transfer_fresh_and_update(client, plugin, format):
    from newsreader.opml import export_opml

    row = {"url": "https://example.org/feed.xml?a=1&b=2", "name": "",
           "plugin": plugin, "category": 'Research & "News"',
           "enabled": False, "hidden": True,
           "config": {"nested": {"list": [False, None, '<&"', 3]}}}
    def transfer():
        if format == "json":
            return client.post("/api/sources/import", json={"sources": [row]})
        return client.post("/api/sources/import.opml", content=export_opml(
            [{**row, "category_name": row["category"]}]).encode())

    assert transfer().json() == {"ok": True, "created": 1, "updated": 0, "skipped": 0}
    src = client.get("/api/sources").json()["sources"][0]
    sid = src["id"]
    for key in ("url", "name", "plugin", "enabled", "hidden", "config"):
        assert src[key] == row[key]
    assert src["category_name"] == row["category"]
    db = client.app.state.db
    await db.upsert_items(sid, [{"guid": "retained", "url": "https://example.org/1",
                                 "title": "Retained", "published_at": time.time()}])
    items, _ = await db.list_items(source_id=sid)
    iid = items[0]["id"]
    await db.set_starred(iid, True)
    await db.update_source(sid, plugin="web" if plugin != "web" else "rss",
                           name="Changed", enabled=True, hidden=False, config={"old": 1})
    assert transfer().json() == {"ok": True, "created": 0, "updated": 1, "skipped": 0}
    restored = client.get(f"/api/sources/{sid}").json()
    for key in ("name", "plugin", "enabled", "hidden", "config"):
        assert restored[key] == row[key]
    assert restored["id"] == sid
    assert (await db.get_source(sid))["item_count"] == 1
    item = await db.get_item(iid)
    assert item["guid"] == "retained" and item["starred"] is True
    exported = client.get("/api/sources/export").json()["sources"][0]
    assert exported == row
    row.update(category=None, config={})
    transfer()
    restored = client.get(f"/api/sources/{sid}").json()
    assert restored["category_id"] is None and restored["config"] == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("format", ["json", "opml"])
async def test_import_omissions_duplicates_and_unknown_plugins(client, format):
    src = client.post("/api/sources", json={"url": "https://example.org/feed.xml",
        "name": "Original", "category_name": "Keep", "config": {"keep": True}}).json()
    sid = src["id"]
    await client.app.state.db.update_source(sid, enabled=False, hidden=True, plugin="web")
    if format == "json":
        r = client.post("/api/sources/import", json={"sources": [
            {"url": " example.org/feed.xml "},
            {"url": src["url"], "name": "First"},
            {"url": src["url"], "name": "Last"},
            {"url": src["url"], "plugin": "unknown", "category": "Never"},
            {"url": "https://new.example/", "plugin": "unknown", "category": "Never"},
            {"url": "ftp://invalid", "category": "Never"}]})
    else:
        r = client.post("/api/sources/import.opml", content='''<opml><body>
          <outline url=" example.org/feed.xml " config="not-json"/>
          <outline url="https://example.org/feed.xml" text="First" config="[1]"/>
          <outline url="https://example.org/feed.xml" text="Last"/>
          <outline url="https://example.org/feed.xml" plugin="unknown" category="Never"/>
          <outline url="https://new.example/" plugin="unknown" category="Never"/>
          <outline url="ftp://invalid" category="Never"/>
        </body></opml>''')
    assert r.json() == {"ok": True, "created": 0, "updated": 3, "skipped": 3}
    restored = client.get(f"/api/sources/{sid}").json()
    assert restored["name"] == "Last"
    for key in ("category_id", "config"):
        assert restored[key] == src[key]
    assert restored["enabled"] is False and restored["hidden"] is True
    assert restored["plugin"] == "web"
    assert [c["name"] for c in client.get("/api/categories").json()["categories"]] == ["Keep"]


@pytest.mark.asyncio
async def test_import_new_duplicates_and_explicit_false(client):
    r = client.post("/api/sources/import", json={"sources": [
        {"url": "new.example/feed.xml", "plugin": "web", "enabled": False,
         "hidden": True, "config": {"old": 1}},
        {"url": "https://new.example/feed.xml", "hidden": False, "config": {}}]})
    assert r.json() == {"ok": True, "created": 1, "updated": 1, "skipped": 0}
    src = client.get("/api/sources").json()["sources"][0]
    assert src["plugin"] == "web" and src["enabled"] is False
    assert src["hidden"] is False and src["config"] == {}


@pytest.mark.asyncio
async def test_foreign_opml_omitted_name_and_legacy_folder(client):
    src = client.post("/api/sources", json={"url": "https://a.example/feed.xml",
        "name": "Keep", "category_name": "Keep"}).json()
    r = client.post("/api/sources/import.opml", content='''<opml><body>
      <outline url="https://a.example/feed.xml"/>
      <outline url="https://b.example/feed.xml"/>
    </body></opml>''')
    assert r.json() == {"ok": True, "created": 1, "updated": 1, "skipped": 0}
    restored = client.get(f'/api/sources/{src["id"]}').json()
    assert restored["name"] == "Keep" and restored["category_name"] == "Keep"
    new = next(s for s in client.get("/api/sources").json()["sources"]
               if s["url"] == "https://b.example/feed.xml")
    assert new["name"] == new["url"] and new["category_id"] is None
    assert new["plugin"] == "rss" and new["enabled"] is True and new["hidden"] is False
    client.post("/api/sources/import.opml", content='''<opml><body>
      <outline text="Legacy"><outline url="https://a.example/feed.xml"/></outline>
    </body></opml>''')
    assert client.get(f'/api/sources/{src["id"]}').json()["category_name"] == "Legacy"


@pytest.mark.asyncio
async def test_import_empty_and_malformed_documents(client):
    for content in (b"", b"not xml", b"<other><body/></other>", b"<opml/>",
                    b'<opml><body><outline url="https://a.example/"/></body>'):
        assert client.post("/api/sources/import.opml", content=content).status_code == 422
    for data in ({}, {"sources": [{}]}, {"sources": "bad"}):
        assert client.post("/api/sources/import", json=data).status_code == 422
    assert client.post("/api/sources/import", content="{").status_code == 422
    for path, kwargs in (("/api/sources/import", {"json": {"sources": []}}),
                         ("/api/sources/import.opml", {"content": "<opml><body/></opml>"})):
        assert client.post(path, **kwargs).json() == {
            "ok": True, "created": 0, "updated": 0, "skipped": 0}
    assert client.get("/api/sources").json()["sources"] == []


@pytest.mark.asyncio
async def test_opml_skipped_duplicate_does_not_suppress_new_name(client):
    r = client.post("/api/sources/import.opml", content='''<opml><body>
      <outline url="https://example.org/feed.xml" plugin="unknown"/>
      <outline url="https://example.org/feed.xml"/>
    </body></opml>''')
    assert r.json() == {"ok": True, "created": 1, "updated": 0, "skipped": 1}
    source = client.get("/api/sources").json()["sources"][0]
    assert source["name"] == source["url"]


def test_opml_unsupported_encoding_is_validation_error(client):
    r = client.post("/api/sources/import.opml", content=(
        b'<?xml version="1.0" encoding="NOT-A-CODEC"?><opml><body/></opml>'))
    assert r.status_code == 422
