"""API tests with an in-memory-ish temp DB and mocked refresh (no network)."""
import asyncio
import time

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

import api.app as app_module
from crawler.base import FetchContext
from fetcher import EventBus, Fetcher, _error_text
from models.database import Database


@pytest_asyncio.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("NR_DB_PATH", str(tmp_path / "api.db"))
    from config import settings
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
    from config import settings
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
        assert (await db.get_source(sid))["plugin"] == "rss"
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
    finally:
        await db.close()
