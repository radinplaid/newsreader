"""Live tests against the real sources (network required).

Deselected by default: run with  pytest -m live
"""
import asyncio

import httpx
import pytest

import crawler
from crawler.base import FetchContext
from newsreader.fetcher import USER_AGENT

REQUIRED_SOURCES = [
    ("https://www.aisi.gov.uk/blog", "web", 10),
    ("https://www.aisi.gov.uk/research", "web", 10),
    ("https://metr.org/research/", "web", 10),
    ("https://www.youtube.com/playlist?list=PL9HYL-VRX0oQOXEh8rBFtLNsJxwC_Ta7A",
     "youtube", 10),
    ("https://cohere.com/blog", "web", 10),
    ("https://www.theguardian.com/technology/artificialintelligenceai/rss", "rss", 10),
    ("https://arxiv.org/search/?query=%22machine+translation%22&searchtype=all"
     "&abstracts=show&order=-announced_date_first&size=50", "arxiv", 10),
]


def _fetch(url, config=None):
    crawler.discover()
    plugin = crawler.find_plugin(url)
    cfg = {**plugin.default_config(), **(config or {})}

    async def go():
        async with httpx.AsyncClient(timeout=60, follow_redirects=True,
                                     headers={"User-Agent": USER_AGENT,
                                              "Accept-Encoding": "gzip, deflate"}) as http:
            ctx = FetchContext(source={"id": 0, "url": url, "name": "", "etag": "",
                                       "last_modified": "", "config": {}},
                               client=http, known_dates={},
                               config=cfg,
                               logger=__import__("logging").getLogger("live"))
            result = await plugin.fetch(ctx)
        return result

    return plugin, asyncio.run(go())


@pytest.mark.live
@pytest.mark.parametrize("url,plugin_name,min_items", REQUIRED_SOURCES,
                         ids=[u.split("//")[1][:40] for u, _, _ in REQUIRED_SOURCES])
def test_required_source_parses(url, plugin_name, min_items):
    plugin, result = _fetch(url)
    assert plugin.name == plugin_name
    assert len(result.items) >= min_items, f"only {len(result.items)} items"
    titles = [i.title for i in result.items]
    assert all(t.strip() for t in titles)
    dated = sum(1 for i in result.items if i.published_at)
    assert dated >= len(result.items) * 0.5, f"only {dated} dated of {len(result.items)}"
    assert all(i.url.startswith("http") for i in result.items)


@pytest.mark.live
def test_youtube_items_have_dates_and_urls():
    plugin, result = _fetch(
        "https://www.youtube.com/playlist?list=PL9HYL-VRX0oQOXEh8rBFtLNsJxwC_Ta7A")
    assert plugin.name == "youtube"
    for item in result.items:
        assert item.url.startswith("https://www.youtube.com/watch?v=")
        assert item.image_url
    assert all(i.published_at for i in result.items)


@pytest.mark.live
def test_guardian_feed_items_rich():
    _, result = _fetch(
        "https://www.theguardian.com/technology/artificialintelligenceai/rss")
    assert len(result.items) >= 10
    assert result.source_name
    assert all(i.published_at for i in result.items)
    assert sum(1 for i in result.items if i.content) >= len(result.items) / 2


@pytest.mark.live
def test_reddit_top_week_min_score():
    plugin, result = _fetch(
        "https://www.reddit.com/r/ottawa/top/?screen_view_count=6&t=week",
        config={"min_score": 100, "page_delay": 0})
    assert plugin.name == "reddit"
    assert result.source_name == "r/ottawa · top · week"
    assert result.items, "no r/ottawa post made 100+ upvotes this week"
    for item in result.items:
        assert item.extra["score"] >= 100
        assert item.title.strip()
        assert item.url.startswith("https://www.reddit.com/r/")
        assert item.published_at is not None
    for item in (i for i in result.items if i.extra.get("external")):
        assert item.extra.get("link_url", "").startswith("http")
        assert item.extra["link_url"].split("?")[0] in item.content
