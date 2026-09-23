"""Unit tests for the SQLite data layer and FTS search."""
import sqlite3
import time

import pytest
import pytest_asyncio

from models.database import Database, fts_query


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
    # list responses stay slim: full content only comes from the detail query
    assert all("content" not in i and "extra" not in i for i in lst)
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
    # re-run the migration; it is idempotent and must heal the bad row only
    with sqlite3.connect(db.path) as conn:
        conn.execute("UPDATE items SET published_at=? WHERE guid='g2'", (future,))
        Database._migrate(conn)
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
        conn.execute("DROP INDEX IF EXISTS idx_items_read")
        conn.execute("ALTER TABLE items DROP COLUMN read_at")
        conn.commit()
    with sqlite3.connect(db.path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
        assert "read_at" not in cols
        Database._migrate(conn)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
        assert "read_at" in cols
        n = conn.execute("SELECT COUNT(*) FROM items WHERE read_at IS NULL").fetchone()[0]
        assert n == 0
    # new items, though, arrive unread
    await db.upsert_items(sid, [_item("g3", published_at=time.time())])
    assert (await db.list_items(unread=True))[1] == 1
