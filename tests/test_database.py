"""Unit tests for the SQLite data layer and FTS search."""
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
    assert await db.upsert_items(sid, items) == (2, 0)
    # same guids again: updated, not duplicated
    assert await db.upsert_items(sid, items) == (0, 2)
    # longer content should win on update
    items2 = [_item("g1", title="First", content="longer content than before")]
    await db.upsert_items(sid, items2)
    row = db.get_source  # noqa: F841
    lst, total = await db.list_items(source_id=sid)
    assert total == 2
    g1 = next(i for i in lst if i["guid"] == "g1")
    assert g1["content"] == "longer content than before"
    assert g1["published_at"] == pytest.approx(now - 10)
    assert sorted(g1["tags"]) == ["tag1"]


@pytest.mark.asyncio
async def test_upsert_skips_empty_and_duplicate_guids(db):
    sid = await db.create_source("https://example.com/rss", "Ex", "rss")
    items = [_item("g1"), _item("g1"), _item(""), _item("  ")]
    assert await db.upsert_items(sid, items) == (1, 0)


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
                         _item("b2", title=None or "four")])  # no date
    lst, total = await db.list_items()
    assert total == 4
    assert [i["title"] for i in lst] == ["four", "two", "three", "one"]  # nulls last
    lst, total = await db.list_items(sort="old")
    assert lst[0]["title"] in ("one", "three", "two")  # dated items first, oldest first
    assert lst[0]["published_at"] == t - 30
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
