"""The backfill chain, end to end.

A stored item that has no date and no body has to be finished off even
when it is no longer on the listing. The pieces are: the database hands
the plugin its backlog, the plugin fetches the article page, and the
result travels back through the upsert so the stored row is updated in
place rather than duplicated.
"""
import httpx
import pytest

from crawler.base import ENRICH_ATTEMPTS_KEY, FetchContext
from crawler.plugins.web import WebPlugin
from models.database import Database

LISTING = """
<html><body>
<div class="list"><a href="/blog/new-post"><h3>New Post</h3><p>Fresh text.</p>
<p>Aug 2, 2026</p></a></div>
</body></html>"""

ARTICLE = """
<html><head>
<meta property="og:title" content="Old Post">
<meta property="article:published_time" content="2026-08-01T10:00:00Z">
<meta name="author" content="Jane Doe">
</head><body><article>
<h1>Old Post</h1>
<p>The first paragraph of the article, long enough to count as the body.</p>
<p>The second paragraph, also long enough to count as the body of it.</p>
</article></body></html>"""


@pytest.mark.asyncio
async def test_a_stored_item_is_finished_off_in_place(tmp_path):
    db = Database(str(tmp_path / "backfill.db"))
    await db.init()
    try:
        sid = await db.create_source("https://ex.com/blog", "Blog", "web")
        # a stored item from an earlier refresh: no date, no body
        inserted, _, ids = await db.upsert_items(sid, [{
            "guid": "https://ex.com/blog/old-post",
            "url": "https://ex.com/blog/old-post",
            "title": "Old Post", "summary": "", "content": "",
            "published_at": None, "image_url": "", "tags": [], "extra": {},
        }])
        assert inserted == 1
        item_id = ids[0]

        def handler(request):
            if request.url.path == "/blog":
                return httpx.Response(200, text=LISTING)
            return httpx.Response(200, text=ARTICLE)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        known = await db.known_dates(sid)
        backlog = await db.backfill_items(sid)
        assert [b["guid"] for b in backlog] == ["https://ex.com/blog/old-post"]
        ctx = FetchContext(
            source={"id": sid, "url": "https://ex.com/blog", "name": "Blog"},
            client=client, known_dates=known,
            config={"fetch_content": True, "content_fetch_limit": 4},
            backlog=backlog,
        )
        result = await WebPlugin().fetch(ctx)
        await client.aclose()

        # the stored row is updated, not duplicated
        inserted, updated, inserted_ids = await db.upsert_items(
            sid, [item.to_row() for item in result.items])
        assert inserted == 1        # only the new listing item
        assert inserted_ids != [item_id]
        assert updated >= 1         # the old item was finished off

        row = await db.get_item(item_id)
        assert row["published_at"] is not None
        assert "first paragraph" in row["content"]
        assert row["title"] == "Old Post"       # nothing was lost
        assert row["author"] == "Jane Doe"

        # and it is no longer unfinished, so it leaves the backlog
        assert await db.backfill_items(sid) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_finished_item_forgets_its_failed_attempts(tmp_path):
    """An item that failed twice and then succeeded must not keep the failure
    count in the database: stored, it would leave one retry before the item
    is given up on."""
    db = Database(str(tmp_path / "attempts.db"))
    await db.init()
    try:
        sid = await db.create_source("https://ex.com/blog", "Blog", "web")
        _, _, ids = await db.upsert_items(sid, [{
            "guid": "https://ex.com/blog/old-post",
            "url": "https://ex.com/blog/old-post",
            "title": "Old Post", "summary": "", "content": "",
            "published_at": None, "image_url": "", "tags": [],
            "extra": {ENRICH_ATTEMPTS_KEY: 2},
        }])

        def handler(request):
            if request.url.path == "/blog":
                return httpx.Response(200, text=LISTING)
            return httpx.Response(200, text=ARTICLE)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        backlog = await db.backfill_items(sid)
        assert [b["guid"] for b in backlog] == ["https://ex.com/blog/old-post"]
        ctx = FetchContext(
            source={"id": sid, "url": "https://ex.com/blog", "name": "Blog"},
            client=client, known_dates=await db.known_dates(sid),
            config={"fetch_content": True, "content_fetch_limit": 4},
            backlog=backlog,
        )
        result = await WebPlugin().fetch(ctx)
        await client.aclose()
        await db.upsert_items(sid, [item.to_row() for item in result.items])

        row = await db.get_item(ids[0])
        assert row["published_at"] is not None
        assert ENRICH_ATTEMPTS_KEY not in row["extra"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_backlog_only_holds_unfinished_items(tmp_path):
    db = Database(str(tmp_path / "backfill.db"))
    await db.init()
    try:
        sid = await db.create_source("https://ex.com/blog", "Blog", "web")
        now = 1758400000.0
        await db.upsert_items(sid, [
            {"guid": "done", "url": "https://ex.com/1", "title": "Done",
             "summary": "s", "content": "a body", "published_at": now},
            {"guid": "undated", "url": "https://ex.com/2", "title": "Undated",
             "summary": "s", "content": "a body", "published_at": None},
            {"guid": "bodyless", "url": "https://ex.com/3", "title": "Bodyless",
             "summary": "s", "content": "", "published_at": now},
        ])
        backlog = await db.backfill_items(sid)
        assert {b["guid"] for b in backlog} == {"undated", "bodyless"}
        # a dismissed item is not worth fetching again
        items, _ = await db.list_items(source_id=sid)
        await db.set_dismissed(items[0]["id"], True)
        backlog = await db.backfill_items(sid)
        assert items[0]["guid"] not in {b["guid"] for b in backlog}
    finally:
        await db.close()
