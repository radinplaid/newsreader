"""Unit tests for the SQLite data layer and FTS search."""
import sqlite3
import time

import pytest
import pytest_asyncio

from crawler.base import MAX_ENRICH_ATTEMPTS
from models.database import _SCHEMA, SCHEMA_VERSION, Database, fts_query, normalize_url


@pytest_asyncio.fixture()
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.init()
    yield database
    await database.close()
    


def _item(guid, title="A title", **kw):
    base = {
        "guid": guid, "url": f"https://example.com/{guid}", "title": title,
        "summary": kw.pop("summary", "A short summary"),
        "content": kw.pop("content", ""), "author": kw.pop("author", ""),
        "published_at": kw.pop("published_at", time.time()),
        "image_url": kw.pop("image_url", ""), "tags": kw.pop("tags", []),
        "extra": kw.pop("extra", {}),
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_category_crud(db):
    cat = await db.create_category("  AI Research ")
    assert cat["name"] == "AI Research"
    again = await db.create_category("ai research")  # unique, case-insensitive-enough
    assert again["id"] == cat["id"]
    assert await db.update_category(cat["id"], "Research") is True
    assert (await db.list_categories())[0]["name"] == "Research"
    assert await db.delete_category(cat["id"]) is True


@pytest.mark.asyncio
async def test_category_delete_keeps_sources(db):
    cat = await db.create_category("C1")
    sid = await db.create_source("https://example.com/rss", "Ex", "rss", cat["id"])
    await db.delete_category(cat["id"])
    assert (await db.get_source(sid))["category_id"] is None


@pytest.mark.asyncio
async def test_source_crud_and_config(db):
    sid = await db.create_source("https://example.com/rss", "Ex", "rss", None, {"a": 1})
    src = await db.get_source(sid)
    assert src["config"] == {"a": 1}
    assert src["enabled"] is True
    await db.update_source(sid, name="Renamed", enabled=False, config={"b": 2})
    src = await db.get_source(sid)
    assert src["name"] == "Renamed" and src["enabled"] is False and src["config"] == {"b": 2}
    assert src["category_name"] is None
    assert await db.delete_source(sid) is True
    assert await db.get_source(sid) is None


@pytest.mark.asyncio
async def test_upsert_counts_and_merge(db):
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    now = time.time()
    items = [_item("g1", title="First", published_at=now - 10, tags=["tag1"]),
             _item("g2", title="Second", published_at=now - 20)]
    assert (await db.upsert_items(sid, items))[:2] == (2, 0)
    # same guids again: updated, not duplicated
    assert (await db.upsert_items(sid, items))[:2] == (0, 2)
    # longer content should win on update
    items2 = [_item("g1", title="First", content="longer content than before")]
    await db.upsert_items(sid, items2)
    lst, total = await db.list_items(source_id=sid)
    assert total == 2
    # list responses stay slim: full content only comes from the detail query,
    # but extra (score & friends) rides along for the list cards
    assert all("content" not in i for i in lst)
    assert all(i["extra"] == {} for i in lst)
    g1 = await db.get_item(next(i["id"] for i in lst if i["guid"] == "g1"))
    assert g1["content"] == "longer content than before"
    assert g1["published_at"] == pytest.approx(now - 10)
    assert sorted(g1["tags"]) == ["tag1"]


@pytest.mark.asyncio
async def test_upsert_skips_empty_and_duplicate_guids(db):
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    items = [_item("g1"), _item("g1"), _item(""), _item("  ")]
    assert (await db.upsert_items(sid, items))[:2] == (1, 0)


@pytest.mark.asyncio
async def test_upsert_skips_new_entry_with_known_url(db):
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    await db.upsert_items(sid, [_item("g1", url="https://example.com/story")])
    # same URL under a new guid is the same entry: not added again
    ins, upd, ids = await db.upsert_items(sid, [_item("g2", url="https://example.com/story")])
    assert (ins, upd, ids) == (0, 0, [])
    lst, total = await db.list_items(source_id=sid)
    assert total == 1 and lst[0]["guid"] == "g1"
    # a known (source, guid) with that URL still updates in place
    ins, upd, ids = await db.upsert_items(
        sid, [_item("g1", url="https://example.com/story", title="Retitled")])
    assert (ins, upd, ids) == (0, 1, [])
    lst, _ = await db.list_items(source_id=sid)
    assert lst[0]["title"] == "Retitled"


@pytest.mark.asyncio
async def test_upsert_dedupes_url_across_sources(db):
    s1 = await db.create_source("https://example.com/a", "A", "rss")
    s2 = await db.create_source("https://example.com/b", "B", "rss")
    await db.upsert_items(s1, [_item("a1", url="https://example.com/story")])
    ins, upd, ids = await db.upsert_items(s2, [_item("b1", url="https://example.com/story")])
    assert (ins, upd, ids) == (0, 0, [])
    _, total = await db.list_items()
    assert total == 1


@pytest.mark.asyncio
async def test_upsert_dedupes_url_within_batch(db):
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    items = [_item("g1", url="https://example.com/same"),
             _item("g2", url="https://example.com/same"),
             _item("g3", url="https://example.com/other"),
             _item("g4", url=""),
             _item("g5", url="")]
    assert (await db.upsert_items(sid, items))[:2] == (4, 0)
    lst, total = await db.list_items(source_id=sid)
    assert {i["guid"] for i in lst} == {"g1", "g3", "g4", "g5"}


@pytest.mark.asyncio
async def test_upsert_update_reserves_url_against_new_guid(db):
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    await db.upsert_items(sid, [_item("g1", url="https://example.com/story")])
    items = [_item("g2", url="https://example.com/story"),
             _item("g1", url="https://example.com/story", title="Updated")]
    assert (await db.upsert_items(sid, items))[:2] == (0, 1)
    _, total = await db.list_items(source_id=sid)
    assert total == 1


def test_normalize_url():
    assert normalize_url("https://Ex.com/a/?utm_source=rss&utm_medium=feed") == \
        "https://ex.com/a"
    assert normalize_url("https://ex.com/a?fbclid=xyz&id=7#frag") == \
        "https://ex.com/a?id=7"
    assert normalize_url("https://ex.com/watch?v=abc&t=10s") == \
        "https://ex.com/watch?v=abc&t=10s"
    assert normalize_url("https://ex.com/") == "https://ex.com"
    assert normalize_url("u1") == "u1"
    assert normalize_url("  ") == "" and normalize_url(None) == ""


@pytest.mark.asyncio
async def test_upsert_dedupes_tracking_param_variants(db):
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    await db.upsert_items(sid, [_item("g1", url="https://example.com/story?utm_source=rss")])
    ins, upd, ids = await db.upsert_items(
        sid, [_item("g2", url="https://example.com/story/?utm_source=mail&fbclid=abc")])
    assert (ins, upd, ids) == (0, 0, [])
    lst, total = await db.list_items(source_id=sid)
    assert total == 1
    assert lst[0]["url"] == "https://example.com/story"


@pytest.mark.asyncio
async def test_migrate_normalizes_stored_urls(tmp_path):
    path = str(tmp_path / "mig.db")
    db = Database(path)
    await db.init()
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    # pretend the row was written by an older version: that database has not
    # run the one-time cleanup yet (user_version still 0)
    async with db.write() as conn:
        await conn.execute("PRAGMA user_version = 0")
        await conn.execute(
            "INSERT INTO items (source_id, guid, url, fetched_at) VALUES (?, ?, ?, ?)",
            (sid, "g1", "https://Example.com/a/?utm_campaign=x", time.time()))
    await db.close()
    db2 = Database(path)
    await db2.init()
    try:
        lst, _ = await db2.list_items(source_id=sid)
        assert lst[0]["url"] == "https://example.com/a"
    finally:
        await db2.close()


@pytest.mark.asyncio
async def test_migrate_cleanup_runs_only_once(tmp_path):
    """The O(rows) cleanup is version-gated: it must not run on every boot."""
    path = str(tmp_path / "mig.db")
    db = Database(path)
    await db.init()
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    async with db.write() as conn:
        await conn.execute("PRAGMA user_version = 0")
        await conn.execute(
            "INSERT INTO items (source_id, guid, url, fetched_at) VALUES (?, ?, ?, ?)",
            (sid, "g1", "https://Example.com/a/?utm_campaign=x", time.time()))
    await db.close()

    # first boot after the upgrade: the cleanup runs
    db2 = Database(path)
    await db2.init()
    async with db2.write() as conn:
        async with conn.execute("PRAGMA user_version") as cur:
            version = (await cur.fetchone())[0]
        assert version == SCHEMA_VERSION
    await db2.close()

    # a later boot must leave the stored rows alone
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE items SET url='https://example.com/mangled/?utm_x=1' WHERE guid='g1'")
        conn.commit()
    db3 = Database(path)
    await db3.init()
    try:
        lst, _ = await db3.list_items(source_id=sid)
        assert lst[0]["url"] == "https://example.com/mangled/?utm_x=1"
    finally:
        await db3.close()


@pytest.mark.asyncio
async def test_source_error_streak(db):
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    assert (await db.get_source(sid))["error_streak"] == 0
    await db.update_source(sid, last_status="error", error_streak=2)
    src = await db.get_source(sid)
    assert src["error_streak"] == 2 and src["last_status"] == "error"
    await db.update_source(sid, last_status="ok", error_streak=0)
    assert (await db.get_source(sid))["error_streak"] == 0


@pytest.mark.asyncio
async def test_item_tags_auto_apply_on_upsert(db):
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    await db.upsert_items(sid, [_item("g1", tags=["ml", "  safety "])])
    tags = {t["name"]: t["item_count"] for t in await db.list_tags()}
    assert tags["ml"] == 1 and tags["safety"] == 1


@pytest.mark.asyncio
async def test_delete_source_cascades_items(db):
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    await db.upsert_items(sid, [_item("g1", tags=["t1"]), _item("g2", tags=["t1"])])
    _, total = await db.list_items()
    assert total == 2
    await db.delete_source(sid)
    assert (await db.counts())["items"] == 0
    # item_tags rows cascade too
    assert (await db.list_tags())[0]["item_count"] == 0


async def _raw_pub_first_seen(db, sid, guid):
    conn = await db.get_conn()
    async with conn.execute(
        "SELECT published_at, first_seen_at FROM items WHERE source_id=? AND guid=?",
        (sid, guid)) as cur:
        row = await cur.fetchone()
    return row["published_at"], row["first_seen_at"]


@pytest.mark.asyncio
async def test_undated_item_fallback_and_late_date(db):
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    # new item without any date: API returns the added date as published_at
    await db.upsert_items(sid, [_item("g1", title="no date yet", published_at=None)])
    pub, first_seen = await _raw_pub_first_seen(db, sid, "g1")
    assert pub is None and first_seen is not None
    lst, _ = await db.list_items(source_id=sid)
    assert lst[0]["published_at"] == pytest.approx(first_seen)
    # refreshing without a date must not bump or fake a date
    await db.upsert_items(sid, [_item("g1", title="no date yet", published_at=None)])
    pub, first_seen2 = await _raw_pub_first_seen(db, sid, "g1")
    assert pub is None and first_seen2 == first_seen
    # a date found later replaces the fallback in the API view
    t = time.time() - 500
    await db.upsert_items(sid, [_item("g1", title="dated now", published_at=t)])
    lst, _ = await db.list_items(source_id=sid)
    assert lst[0]["published_at"] == pytest.approx(t)


@pytest.mark.asyncio
async def test_future_date_rejected(db):
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    # a feed with a broken CMS timezone can publish dates hours in the future;
    # those must be stored as missing so the first-seen fallback applies
    future = time.time() + 7200
    await db.upsert_items(sid, [_item("g1", title="future dated", published_at=future)])
    pub, first_seen = await _raw_pub_first_seen(db, sid, "g1")
    assert pub is None and first_seen is not None
    lst, _ = await db.list_items(source_id=sid)
    assert lst[0]["published_at"] == pytest.approx(first_seen)
    # refreshing with the same bogus date must not re-plant it
    await db.upsert_items(sid, [_item("g1", title="future dated", published_at=future)])
    pub, _ = await _raw_pub_first_seen(db, sid, "g1")
    assert pub is None
    # small clock skew within the tolerance is kept
    skew = time.time() + 60
    await db.upsert_items(sid, [_item("g2", title="skew", published_at=skew)])
    pub2, _ = await _raw_pub_first_seen(db, sid, "g2")
    assert pub2 == pytest.approx(skew)


@pytest.mark.asyncio
async def test_migrate_heals_stored_future_dates(db):
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    past = time.time() - 100
    future = time.time() + 7200
    await db.upsert_items(sid, [_item("g1", published_at=past), _item("g2")])
    # plant a future date behind the back of upsert_items (pre-fix row), then
    # re-run the migration on a database that has not been cleaned up yet;
    # it is idempotent and must heal the bad row only
    with sqlite3.connect(db.path) as conn:
        conn.execute("PRAGMA user_version = 0")
        conn.execute("UPDATE items SET published_at=? WHERE guid='g2'", (future,))
        Database._heal(conn, 0)
        row = conn.execute(
            "SELECT published_at FROM items WHERE guid='g2'").fetchone()
        assert row[0] is None
        row = conn.execute(
            "SELECT published_at FROM items WHERE guid='g1'").fetchone()
        assert row[0] == pytest.approx(past)


@pytest.mark.asyncio
async def test_fts_search(db):
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    await db.upsert_items(sid, [
        _item("g1", title="Machine translation breakthrough",
              summary="Neural MT advances", published_at=time.time() - 100),
        _item("g2", title="AI safety evaluations",
              summary="Evaluating dangerous capabilities", published_at=time.time()),
        _item("g3", title="Translating poetry", summary="verse by verse",
              published_at=time.time() - 50),
    ])
    lst, total = await db.list_items(q="translation")
    assert total == 2
    assert [i["guid"] for i in lst] == ["g3", "g1"]  # newest first
    # prefix matching
    _, total = await db.list_items(q="translat")
    assert total == 2
    # multi-token AND
    _, total = await db.list_items(q="machine translation breakthrough")
    assert total == 1
    # phrase
    _, total = await db.list_items(q='"dangerous capabilities"')
    assert total == 1
    # no match
    _, total = await db.list_items(q="zzzqqq")
    assert total == 0


@pytest.mark.asyncio
async def test_list_filters_and_sort(db):
    cat = await db.create_category("Research")
    s1 = await db.create_source("https://a.example/rss", "A", "rss", cat["id"])
    s2 = await db.create_source("https://b.example/rss", "B", "rss")
    t = time.time()
    await db.upsert_items(s1, [_item("a1", title="one", published_at=t - 30, tags=["x"]),
                         _item("a2", title="two", published_at=t - 10)])
    await db.upsert_items(s2, [_item("b1", title="three", published_at=t - 20),
                         _item("b2", title="four", published_at=None)])  # no date
    lst, total = await db.list_items()
    assert total == 4
    # dated items newest-first; the undated item trails by its added date
    assert [i["title"] for i in lst] == ["two", "three", "one", "four"]
    assert lst[0]["published_at"] == pytest.approx(t - 10)
    assert lst[3]["published_at"] == pytest.approx(t, abs=5)  # first-seen fallback
    lst, total = await db.list_items(sort="old")
    # dated items oldest-first; the undated item still trails
    assert [i["title"] for i in lst] == ["one", "three", "two", "four"]
    assert lst[0]["published_at"] == pytest.approx(t - 30)
    _, total = await db.list_items(category_id=cat["id"])
    assert total == 2
    _, total = await db.list_items(source_id=s2)
    assert total == 2
    _, total = await db.list_items(tag="x")
    assert total == 1 and lst is not None
    # combined search + filter
    lst, total = await db.list_items(q="one", category_id=cat["id"])
    assert total == 1 and lst[0]["title"] == "one"


@pytest.mark.asyncio
async def test_pagination(db):
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    await db.upsert_items(sid, [_item(f"g{i:03d}", title=f"t{i}", published_at=time.time() - i)
                          for i in range(10)])
    page1, total = await db.list_items(limit=4, offset=0)
    page2, _ = await db.list_items(limit=4, offset=4)
    assert total == 10 and len(page1) == 4 and len(page2) == 4
    assert page1[0]["guid"] != page2[0]["guid"]


@pytest.mark.asyncio
async def test_tag_management(db):
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    await db.upsert_items(sid, [_item("g1")])
    item_id = (await db.list_items())[0][0]["id"]
    assert await db.set_item_tags(item_id, ["a", "b"]) == ["a", "b"]
    assert await db.add_item_tags(item_id, ["b", "c"]) == ["a", "b", "c"]
    assert await db.remove_item_tag(item_id, "B") == ["a", "c"]  # case-insensitive


@pytest.mark.asyncio
async def test_star_and_dismiss(db):
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    await db.upsert_items(sid, [_item("g1", tags=["t1"]), _item("g2"), _item("g3")])
    by_guid = {i["guid"]: i["id"] for i in (await db.list_items())[0]}
    g1, g2, g3 = by_guid["g1"], by_guid["g2"], by_guid["g3"]

    assert await db.set_starred(g1, True) is True
    assert (await db.get_item(g1))["starred"] == 1
    lst, total = await db.list_items(starred=True)
    assert total == 1 and lst[0]["id"] == g1
    assert await db.set_starred(g1, False) is True
    assert (await db.list_items(starred=True))[1] == 0

    await db.set_dismissed(g2, True)
    lst, total = await db.list_items()
    assert total == 2 and all(i["id"] != g2 for i in lst)
    # dismissed items are hidden everywhere: source count, tag count, stats
    assert (await db.get_source(sid))["item_count"] == 2
    assert {t["name"]: t["item_count"] for t in await db.list_tags()}["t1"] == 1
    counts = await db.counts()
    assert counts["items"] == 2 and counts["dismissed"] == 1
    # dismissed+starred items still never show up
    await db.set_starred(g2, True)
    assert (await db.list_items(starred=True))[1] == 0
    # dismiss is reversible
    await db.set_dismissed(g2, False)
    assert (await db.list_items())[1] == 3


@pytest.mark.asyncio
async def test_star_filter_combines_with_search(db):
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    await db.upsert_items(sid, [
        _item("g1", title="translation paper"), _item("g2", title="translation redux"),
        _item("g3", title="unrelated"),
    ])
    by_guid = {i["guid"]: i["id"] for i in (await db.list_items())[0]}
    await db.set_starred(by_guid["g2"], True)
    lst, total = await db.list_items(q="translation", starred=True)
    assert total == 1 and lst[0]["guid"] == "g2"


@pytest.mark.asyncio
async def test_fts_query_builder():
    assert fts_query("  hello world ") == '"hello"* AND "world"*'
    assert fts_query('"exact phrase"') == '"exact phrase"*'
    assert fts_query('!@#$%') is None
    assert fts_query("") is None


@pytest.mark.asyncio
async def test_hidden_sources(db):
    cat = await db.create_category("News")
    s1 = await db.create_source("https://a.example/rss", "A", "rss", cat["id"])
    s2 = await db.create_source("https://b.example/rss", "B", "rss", cat["id"])
    await db.upsert_items(s1, [_item("a1", title="one"), _item("a2", title="two")])
    await db.upsert_items(s2, [_item("b1", title="three", tags=["x"])])
    assert (await db.get_source(s1))["hidden"] is False
    assert (await db.list_items())[1] == 3

    await db.update_source(s1, hidden=True)
    assert (await db.get_source(s1))["hidden"] is True
    # hidden feed-wide...
    lst, total = await db.list_items()
    assert total == 1 and lst[0]["guid"] == "b1"
    # ...but still listed when the source is selected explicitly
    assert (await db.list_items(source_id=s1))[1] == 2
    # a hidden source keeps its own item count
    assert (await db.get_source(s1))["item_count"] == 2
    # category and tag counts only count visible items
    assert {c["name"]: c["item_count"] for c in await db.list_categories()}["News"] == 1
    assert {t["name"]: t["item_count"] for t in await db.list_tags()}["x"] == 1
    # starred items from hidden sources stay out of the starred view
    g1 = next(i for i in (await db.list_items(source_id=s1))[0] if i["guid"] == "a1")
    await db.set_starred(g1["id"], True)
    assert (await db.list_items(starred=True))[1] == 0
    assert (await db.counts())["starred_items"] == 0

    await db.update_source(s1, hidden=False)
    assert (await db.list_items())[1] == 3
    assert (await db.list_items(starred=True))[1] == 1
    assert (await db.counts())["starred_items"] == 1


@pytest.mark.asyncio
async def test_upsert_returns_inserted_ids(db):
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    ins, upd, ids = await db.upsert_items(sid, [_item("g1"), _item("g2")])
    assert (ins, upd) == (2, 0) and len(ids) == 2
    # re-upsert: no new ids
    ins, upd, ids2 = await db.upsert_items(sid, [_item("g1"), _item("g2")])
    assert (ins, upd, ids2) == (0, 2, [])
    # a third guid reports only its own fresh id
    ins, upd, ids3 = await db.upsert_items(sid, [_item("g1"), _item("g3")])
    assert (ins, upd) == (1, 1) and len(ids3) == 1
    assert ids3[0] not in ids


@pytest.mark.asyncio
async def test_read_state_and_unread_filter(db):
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    await db.upsert_items(sid, [_item("g1", published_at=time.time() - 10),
                                _item("g2", published_at=time.time() - 20)])
    # fresh items arrive unread
    _, total = await db.list_items(unread=True)
    assert total == 2
    assert (await db.counts())["unread_items"] == 2
    g1 = (await db.list_items())[0][0]  # newest first: g1

    assert g1["read_at"] is None
    assert await db.set_item_read(g1["id"], True) is True
    got = await db.get_item(g1["id"])
    assert got["read_at"] is not None
    lst, total = await db.list_items(unread=True)
    assert total == 1 and lst[0]["guid"] == "g2"
    assert (await db.counts())["unread_items"] == 1

    # and back to unread
    assert await db.set_item_read(g1["id"], False) is True
    assert (await db.get_item(g1["id"]))["read_at"] is None
    assert (await db.list_items(unread=True))[1] == 2
    assert await db.set_item_read(424242, True) is False


@pytest.mark.asyncio
async def test_mark_items_read_bulk(db):
    cat = await db.create_category("News")
    s1 = await db.create_source("https://a.example/rss", "A", "rss", cat["id"])
    s2 = await db.create_source("https://b.example/rss", "B", "rss", cat["id"])
    await db.upsert_items(s1, [_item("a1", tags=["x"]), _item("a2", tags=["y"])])
    await db.upsert_items(s2, [_item("b1", tags=["x"])])

    assert await db.mark_items_read(source_id=s1) == 2
    assert (await db.list_items(unread=True))[1] == 1          # b1 left
    assert (await db.get_source(s1))["unread_count"] == 0
    assert (await db.get_source(s2))["unread_count"] == 1

    assert await db.mark_items_read(tag="x") == 2              # a1 (already read) + b1
    assert (await db.list_items(unread=True))[1] == 0
    assert await db.mark_items_read(read=False, category_id=cat["id"]) == 3
    assert (await db.list_items(unread=True))[1] == 3

    a2 = next(i for i in (await db.list_items(source_id=s1))[0] if i["guid"] == "a2")
    assert await db.mark_items_read(ids=[a2["id"]]) == 1
    assert (await db.list_items(unread=True))[1] == 2
    assert (await db.counts())["unread_items"] == 2


@pytest.mark.asyncio
async def test_unread_counts_per_source_and_category(db):
    cat = await db.create_category("News")
    s1 = await db.create_source("https://a.example/rss", "A", "rss", cat["id"])
    await db.upsert_items(s1, [_item("a1", published_at=1), _item("a2", published_at=2)])
    assert (await db.get_source(s1))["unread_count"] == 2
    assert {c["name"]: c["unread_count"] for c in await db.list_categories()}["News"] == 2
    a1 = next(i for i in (await db.list_items())[0] if i["guid"] == "a1")
    await db.set_item_read(a1["id"], True)
    assert (await db.get_source(s1))["unread_count"] == 1
    assert {c["name"]: c["unread_count"] for c in await db.list_categories()}["News"] == 1
    # dismissed items leave the unread pool
    await db.set_dismissed(a1["id"], True)
    assert (await db.get_source(s1))["unread_count"] == 1  # a1 already read; a2 still unread
    a2 = next(i for i in (await db.list_items())[0] if i["guid"] == "a2")
    await db.set_dismissed(a2["id"], True)
    assert (await db.get_source(s1))["unread_count"] == 0
    assert (await db.counts())["unread_items"] == 0


@pytest.mark.asyncio
async def test_list_date_range_and_ids(db):
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    now = time.time()
    await db.upsert_items(sid, [
        _item("old", published_at=now - 3000),
        _item("mid", published_at=now - 200),
        _item("fresh", published_at=now - 10),
        _item("nodate", published_at=None),
    ])
    lst, total = await db.list_items(since=now - 300)
    assert {i["guid"] for i in lst} == {"mid", "fresh", "nodate"}
    # until is exclusive; undated items fall back to first_seen (≈ now)
    lst, total = await db.list_items(until=now - 100)
    assert {i["guid"] for i in lst} == {"old", "mid"}
    lst, total = await db.list_items(since=now - 300, until=now - 50)
    assert {i["guid"] for i in lst} == {"mid"}
    # ids narrow to an explicit set and compose with the rest
    g_old = next(i for i in (await db.list_items())[0] if i["guid"] == "old")
    g_fresh = next(i for i in (await db.list_items())[0] if i["guid"] == "fresh")
    lst, total = await db.list_items(ids=[g_old["id"], g_fresh["id"]])
    assert total == 2
    lst, total = await db.list_items(ids=[g_old["id"], g_fresh["id"]], since=now - 300)
    assert [i["guid"] for i in lst] == ["fresh"]
    assert (await db.list_items(ids=[]))[1] == 0


@pytest.mark.asyncio
async def test_migrate_backfills_read_at(db):
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    await db.upsert_items(sid, [_item("g1"), _item("g2")])
    # simulate a pre-read_at database, then re-migrate: old rows must come
    # back marked read so upgrading never presents a huge "unread" backlog
    with sqlite3.connect(db.path) as conn:
        # every index that mentions read_at has to go with the column
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql LIKE '%read_at%'"
        ).fetchall():
            conn.execute(f"DROP INDEX IF EXISTS {row[0]}")
        conn.execute("ALTER TABLE items DROP COLUMN read_at")
        conn.commit()
    with sqlite3.connect(db.path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
        assert "read_at" not in cols
        Database._add_columns(conn)
        conn.executescript(_SCHEMA)   # as startup does: it recreates the indexes
        cols = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
        assert "read_at" in cols
        n = conn.execute("SELECT COUNT(*) FROM items WHERE read_at IS NULL").fetchone()[0]
        assert n == 0
    # new items, though, arrive unread
    await db.upsert_items(sid, [_item("g3", published_at=time.time())])
    assert (await db.list_items(unread=True))[1] == 1


@pytest.mark.asyncio
async def test_a_database_without_the_new_columns_opens(tmp_path):
    """A database from before first_seen_at and read_at existed must still
    open: the columns are added before the indexes that refer to them."""
    path = str(tmp_path / "ancient.db")
    db = Database(path)
    await db.init()
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    await db.upsert_items(sid, [_item("g1", published_at=time.time())])
    await db.close()

    # roll the table back to the shape it had before those columns
    with sqlite3.connect(path) as conn:
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql LIKE '%first_seen_at%'"
            " OR sql LIKE '%read_at%'"
        ).fetchall():
            conn.execute(f"DROP INDEX IF EXISTS {row[0]}")
        conn.execute("ALTER TABLE items DROP COLUMN read_at")
        conn.execute("ALTER TABLE items DROP COLUMN first_seen_at")
        conn.execute("PRAGMA user_version = 0")
        conn.commit()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
        assert "read_at" not in cols and "first_seen_at" not in cols

    # the new code has to bring it along
    db2 = Database(path)
    await db2.init()
    try:
        conn = await db2.get_conn()
        async with conn.execute("PRAGMA table_info(items)") as cur:
            cols = {r["name"] for r in await cur.fetchall()}
        assert "read_at" in cols and "first_seen_at" in cols
        items, total = await db2.list_items(source_id=sid)
        assert total == 1
        # the old row is dated as "first seen" and counts as read, so upgrading
        # does not present a backlog of unread articles
        assert items[0]["published_at"] is not None
        async with conn.execute("SELECT COUNT(*) FROM items WHERE read_at IS NULL") as cur:
            assert (await cur.fetchone())[0] == 0
    finally:
        await db2.close()


#: the schema the first release created, before any column or index was added
_FIRST_RELEASE_SCHEMA = """
CREATE TABLE sources (
    id                INTEGER PRIMARY KEY,
    url               TEXT NOT NULL UNIQUE,
    name              TEXT NOT NULL DEFAULT '',
    plugin            TEXT NOT NULL DEFAULT '',
    category_id       INTEGER,
    enabled           INTEGER NOT NULL DEFAULT 1,
    config            TEXT NOT NULL DEFAULT '{}',
    etag              TEXT NOT NULL DEFAULT '',
    last_modified     TEXT NOT NULL DEFAULT '',
    last_refreshed_at REAL,
    last_status       TEXT NOT NULL DEFAULT '',
    last_error        TEXT NOT NULL DEFAULT '',
    created_at        REAL NOT NULL
);
CREATE TABLE items (
    id           INTEGER PRIMARY KEY,
    source_id    INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    guid         TEXT NOT NULL,
    url          TEXT NOT NULL DEFAULT '',
    title        TEXT NOT NULL DEFAULT '',
    summary      TEXT NOT NULL DEFAULT '',
    content      TEXT NOT NULL DEFAULT '',
    author       TEXT NOT NULL DEFAULT '',
    published_at REAL,
    image_url    TEXT NOT NULL DEFAULT '',
    extra        TEXT NOT NULL DEFAULT '{}',
    fetched_at   REAL NOT NULL,
    starred      INTEGER NOT NULL DEFAULT 0,
    dismissed    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (source_id, guid)
);
CREATE INDEX idx_items_source_pub ON items (source_id, published_at DESC);
CREATE INDEX idx_items_pub ON items (published_at DESC);
CREATE TABLE tags (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);
CREATE TABLE item_tags (
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    tag_id  INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (item_id, tag_id)
);
CREATE INDEX idx_item_tags_tag ON item_tags (tag_id);
CREATE VIRTUAL TABLE items_fts USING fts5(
    title, summary, content,
    content='items', content_rowid='id',
    tokenize='porter unicode61'
);
CREATE TRIGGER items_fts_ai AFTER INSERT ON items BEGIN
    INSERT INTO items_fts (rowid, title, summary, content)
    VALUES (new.id, new.title, new.summary, new.content);
END;
CREATE TRIGGER items_fts_ad AFTER DELETE ON items BEGIN
    INSERT INTO items_fts (items_fts, rowid, title, summary, content)
    VALUES ('delete', old.id, old.title, old.summary, old.content);
END;
CREATE TRIGGER items_fts_au AFTER UPDATE OF title, summary, content ON items BEGIN
    INSERT INTO items_fts (items_fts, rowid, title, summary, content)
    VALUES ('delete', old.id, old.title, old.summary, old.content);
    INSERT INTO items_fts (rowid, title, summary, content)
    VALUES (new.id, new.title, new.summary, new.content);
END;
"""


@pytest.mark.asyncio
async def test_a_database_from_the_first_release_opens(tmp_path):
    """The oldest shape: no read_at, first_seen_at, hidden or error_streak.
    It has to open, keep its articles and their state, and come out of the
    upgrade with the current columns."""
    path = str(tmp_path / "first.db")
    with sqlite3.connect(path) as conn:
        conn.executescript(_FIRST_RELEASE_SCHEMA)
        conn.execute("INSERT INTO sources (url, name, plugin, created_at) "
                     "VALUES ('https://example.com/rss', 'Ex', 'rss', ?)", (time.time(),))
        for guid, starred, dismissed in [("g1", 1, 0), ("g2", 0, 0), ("g3", 0, 1)]:
            conn.execute(
                "INSERT INTO items (source_id, guid, url, title, fetched_at, starred, dismissed) "
                "VALUES (1, ?, ?, ?, ?, ?, ?)",
                (guid, f"https://example.com/{guid}", guid.upper(), time.time(),
                 starred, dismissed))
        conn.commit()

    for boot in range(2):   # the second boot must leave the same state behind
        db = Database(path)
        await db.init()
        try:
            conn = await db.get_conn()
            async with conn.execute("PRAGMA table_info(items)") as cur:
                item_cols = {r["name"] for r in await cur.fetchall()}
            async with conn.execute("PRAGMA table_info(sources)") as cur:
                source_cols = {r["name"] for r in await cur.fetchall()}
            assert {"read_at", "first_seen_at"} <= item_cols
            assert {"hidden", "error_streak"} <= source_cols

            items, total = await db.list_items()
            assert total == 2                       # the dismissed one stays hidden
            assert {i["guid"] for i in items} == {"g1", "g2"}
            starred, _ = await db.list_items(starred=True)
            assert [i["guid"] for i in starred] == ["g1"]
            # the articles were read before the upgrade, so they are not
            # presented as a backlog of unread ones
            assert (await db.list_items(unread=True))[1] == 0
            counts = await db.counts()
            assert counts["items"] == 2
            assert counts["starred_items"] == 1
            assert counts["unread_items"] == 0
        finally:
            await db.close()


#: The feed order, as list_items spells it. The index that serves it has to
#: match this exactly, or the planner sorts the whole filtered set again.
_FEED_ORDER = ("ORDER BY (i.published_at IS NULL) ASC, i.published_at DESC, "
               "i.first_seen_at DESC, i.id DESC")


@pytest_asyncio.fixture()
async def library(db):
    """Twenty sources and 3000 items, with planner statistics collected."""
    now = time.time()
    for s in range(20):
        sid = await db.create_source(f"https://e{s}.example/rss", f"E{s}", "rss")
        await db.upsert_items(sid, [
            _item(f"g{s}-{i}", url=f"https://e{s}.example/{i}", content="c" * 200,
                  published_at=(now - i * 60) if i % 7 else None)
            for i in range(150)])
    await db.analyze()
    return db


async def _plan(db, sql, params=()):
    conn = await db.get_conn()
    async with conn.execute("EXPLAIN QUERY PLAN " + sql, list(params)) as cur:
        return " | ".join(r[-1] for r in await cur.fetchall())


@pytest.mark.asyncio
async def test_feed_order_is_served_by_its_index(library):
    plan = await _plan(library, f"SELECT i.id FROM items i WHERE i.dismissed = 0 "
                                f"{_FEED_ORDER} LIMIT 50")
    assert "idx_items_sort" in plan and "TEMP B-TREE" not in plan


@pytest.mark.asyncio
async def test_starred_view_is_served_by_its_index(library):
    starred = await _plan(library, f"SELECT i.id FROM items i WHERE i.dismissed = 0 "
                                   f"AND i.starred = 1 {_FEED_ORDER} LIMIT 50")
    assert "idx_items_starred" in starred and "TEMP B-TREE" not in starred


def test_forced_index_is_named_only_for_the_plain_views():
    forced = Database._forced_index
    assert forced("new", False, False, None, None, None, None, None) == "idx_items_sort"
    assert forced("new", False, True, None, None, None, None, None) == "idx_items_unread"
    assert forced("new", True, False, None, None, None, None, None) == "idx_items_starred"
    # starred and unread together: the starred index also covers the query
    assert forced("new", True, True, None, None, None, None, None) == "idx_items_starred"
    # everything else keeps the planner's choice
    assert forced("new", False, False, None, 3, None, None, None) is None      # one source
    assert forced("new", False, False, None, None, 3, None, None) is None      # one category
    assert forced("new", False, False, None, None, None, "news", None) is None # one tag
    assert forced("new", False, False, None, None, None, None, [1, 2]) is None # explicit ids
    assert forced("new", False, False, "term", None, None, None, None) is None # search
    assert forced("old", False, False, None, None, None, None, None) is None   # oldest first


@pytest.mark.asyncio
async def test_forced_views_return_exactly_what_the_feed_order_says(library):
    """list_items reads the plain views through a named index. The rows and
    their order must still be the feed order over the visible items."""
    async with library.write() as conn:
        await conn.execute("UPDATE items SET read_at = first_seen_at WHERE id % 3 = 0")
        await conn.execute("UPDATE items SET starred = 1 WHERE id % 5 = 0")
        await conn.execute("UPDATE items SET dismissed = 1 WHERE id % 11 = 0")
    conn = await library.get_conn()
    async with conn.execute(
        "SELECT i.id, i.published_at, i.first_seen_at, i.read_at, i.starred "
        "FROM items i JOIN sources s ON s.id = i.source_id "
        "WHERE i.dismissed = 0 AND s.hidden = 0") as cur:
        rows = [dict(r) for r in await cur.fetchall()]

    def expected(keep):
        picked = [r for r in rows if keep(r)]
        picked.sort(key=lambda r: (r["published_at"] is None,
                                   -(r["published_at"] or 0), -r["first_seen_at"], -r["id"]))
        return [r["id"] for r in picked]

    views = {
        "feed": ({}, expected(lambda r: True)),
        "unread": ({"unread": True}, expected(lambda r: r["read_at"] is None)),
        "starred": ({"starred": True}, expected(lambda r: r["starred"] == 1)),
    }
    for name, (kwargs, want) in views.items():
        got = []
        offset = 0
        while True:   # page through the whole view, as the UI does
            page, total = await library.list_items(limit=200, offset=offset, **kwargs)
            got += [i["id"] for i in page]
            offset += len(page)
            if not page:
                break
        assert total == len(want), name
        assert got == want, name


@pytest.mark.asyncio
async def test_counters_and_lookups_use_covering_indexes(library):
    unread_count = await _plan(
        library, "SELECT COUNT(*) FROM items i JOIN sources s ON s.id = i.source_id "
                 "WHERE i.read_at IS NULL AND i.dismissed = 0 AND s.hidden = 0")
    assert "COVERING INDEX idx_items_dismissed_source" in unread_count
    url_lookup = await _plan(library, "SELECT DISTINCT url FROM items WHERE url IN (?, ?)",
                             ["https://a", "https://b"])
    assert "idx_items_url" in url_lookup
    backlog = await _plan(
        library, "SELECT guid FROM items WHERE source_id = ? AND dismissed = 0 "
                 "AND (published_at IS NULL OR content = '') "
                 "ORDER BY first_seen_at ASC, id ASC LIMIT 100", [1])
    assert "idx_items_backfill" in backlog and "TEMP B-TREE" not in backlog


@pytest.mark.asyncio
async def test_forced_index_plan_has_no_sort_step(library):
    """The statement list_items builds for the plain feed, with the index it
    names, reads the index in order and does not sort."""
    join, clause, args, _ = Database._filter_items()
    sql = f"{Database._SELECT_LIST} {join} {clause} {_FEED_ORDER} LIMIT ? OFFSET ?"
    sql = sql.replace("FROM items i", "FROM items i INDEXED BY idx_items_sort", 1)
    plan = await _plan(library, sql, [*args, 50, 0])
    # the feed is walked through the index; the only sort left is the one
    # inside each row's tag subquery
    assert plan.startswith("SCAN i USING INDEX idx_items_sort")


@pytest.mark.asyncio
async def test_list_items_carries_extra_for_cards(db):
    sid = await db.create_source("https://reddit.com/r/x", "r/x", "reddit")
    await db.upsert_items(sid, [_item("t3_a", extra={"kind": "reddit", "score": 250})])
    items, _ = await db.list_items(source_id=sid)
    assert items[0]["extra"] == {"kind": "reddit", "score": 250}
    full, _ = await db.list_items(source_id=sid, include_content=True)
    assert full[0]["extra"]["score"] == 250
    assert "content" in full[0]


@pytest.mark.asyncio
async def test_indexes_cover_the_hot_paths(db):
    conn = await db.get_conn()
    async with conn.execute("PRAGMA index_list(items)") as cur:
        names = {r["name"] for r in await cur.fetchall()}
    for idx in ("idx_items_url", "idx_items_sort", "idx_items_sort_source",
                "idx_items_sort_asc", "idx_items_dismissed_source",
                "idx_items_unread", "idx_items_starred", "idx_items_backfill"):
        assert idx in names, f"missing index {idx}"


@pytest.mark.asyncio
async def test_backfill_items_lists_what_still_needs_enrichment(db):
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    now = time.time()
    # undated, no body -> needs a fetch
    await db.upsert_items(sid, [_item("undated", published_at=None)])
    # dated but no body -> still needs one
    await db.upsert_items(sid, [_item("bodyless", published_at=now)])
    # dated with a body -> complete
    await db.upsert_items(sid, [_item("done", published_at=now, content="x" * 400)])
    items, _ = await db.list_items(source_id=sid)
    done = next(i for i in items if i["guid"] == "done")
    # a complete item has a body and a date
    assert done["published_at"]

    backlog = await db.backfill_items(sid)
    guids = {b["guid"] for b in backlog}
    assert "undated" in guids
    assert "bodyless" in guids
    assert "done" not in guids
    # every backlog row carries what is already stored, so merging what is
    # fetched back into it cannot erase anything
    row = next(b for b in backlog if b["guid"] == "undated")
    assert row["title"]
    assert row["tags"] == []
    assert isinstance(row["extra"], dict)


@pytest.mark.asyncio
async def test_backfill_items_is_oldest_first_and_skips_dismissed(db):
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    for i in range(3):
        await db.upsert_items(sid, [_item(f"g{i}", published_at=None)])
    # age them: g0 was seen first, g2 last
    async with db.write() as conn:
        await conn.execute(
            "UPDATE items SET first_seen_at=? WHERE guid='g0'", (time.time() - 300,))
        await conn.execute(
            "UPDATE items SET first_seen_at=? WHERE guid='g1'", (time.time() - 200,))
        await conn.execute(
            "UPDATE items SET first_seen_at=? WHERE guid='g2'", (time.time() - 100,))
    backlog = await db.backfill_items(sid)
    assert [b["guid"] for b in backlog] == ["g0", "g1", "g2"]

    # dismissed items are not worth fetching again
    items, _ = await db.list_items(source_id=sid)
    await db.set_dismissed(items[0]["id"], True)
    backlog = await db.backfill_items(sid)
    assert items[0]["guid"] not in {b["guid"] for b in backlog}


@pytest.mark.asyncio
async def test_backfill_items_skips_items_given_up_on(db):
    """An article page that cannot be fetched is tried a few times and then
    left alone, so it cannot use up the budget for ever."""
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    await db.upsert_items(sid, [_item("g1", published_at=None)])
    await db.upsert_items(sid, [_item("g2", published_at=None)])
    async with db.write() as conn:
        await conn.execute(
            "UPDATE items SET extra=? WHERE guid='g1'",
            ('{"enrich_attempts": %d}' % MAX_ENRICH_ATTEMPTS,))
    backlog = await db.backfill_items(sid)
    assert [b["guid"] for b in backlog] == ["g2"]


@pytest.mark.asyncio
async def test_upsert_merges_extra_instead_of_replacing_it(db):
    """Two copies of the same item must not lose each other's fields: the
    listing copy has no attempt counter, the backfill copy has one."""
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    await db.upsert_items(sid, [_item("g1", extra={"kind": "reddit", "score": 10})])
    await db.upsert_items(sid, [_item("g1", extra={"enrich_attempts": 2})])
    items, _ = await db.list_items(source_id=sid)
    assert items[0]["extra"]["kind"] == "reddit"
    assert items[0]["extra"]["score"] == 10
    assert items[0]["extra"]["enrich_attempts"] == 2


@pytest.mark.asyncio
async def test_a_broken_json_column_does_not_break_the_library(db):
    """A row written by an old version (or edited by hand) may hold
    something that is not JSON; one broken row must not take everything
    down with it."""
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    await db.upsert_items(sid, [_item("g1")])
    async with db.write() as conn:
        await conn.execute("UPDATE items SET extra='not json at all' WHERE guid='g1'")
    items, total = await db.list_items(source_id=sid)
    assert total == 1
    assert items[0]["extra"] == {}
    # and the row can still be written to
    await db.upsert_items(sid, [_item("g1", extra={"enrich_attempts": 1})])
    items, _ = await db.list_items(source_id=sid)
    assert items[0]["extra"]["enrich_attempts"] == 1


@pytest.mark.asyncio
async def test_saved_source_per_category_is_reused_and_reattached(tmp_path):
    db = Database(str(tmp_path / "saved.db"))
    await db.init()
    try:
        first = await db.saved_source_id("Read it later")
        assert await db.saved_source_id("Read it later") == first
        other = await db.saved_source_id("Research")
        assert other != first

        # deleting the category unassigns its saved source; the next save heals it
        cat = (await db.create_category("Read it later"))["id"]
        await db.delete_category(cat)
        assert (await db.get_source(first))["category_id"] is None
        assert await db.saved_source_id("Read it later") == first
        src = await db.get_source(first)
        assert src["category_name"] == "Read it later" and src["plugin"] == "manual"
    finally:
        await db.close()
