"""Offline parser tests: fixture HTML/XML snippets, no network."""
import json
import time

import pytest
import httpx

from crawler import find_plugin
from crawler.base import FetchContext, FetchError
from crawler.plugins import youtube as yt_mod
from crawler.plugins.arxiv import ArxivPlugin, parse_abs_page, parse_search_page
from crawler.plugins.reddit import RedditPlugin, parse_listing as reddit_parse_listing
from crawler.plugins.reddit import parse_target as reddit_parse_target
from crawler.plugins.reddit import challenge_solution, is_challenge_page
from crawler.plugins.rss import _parse_feed
from crawler.plugins.web import WebPlugin, extract_article, extract_listing

RSS_XML = """<?xml version="1.0"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel>
<title>Sample Feed</title>
<item>
  <title>First post</title>
  <link>https://ex.com/1</link>
  <guid>https://ex.com/1</guid>
  <pubDate>Tue, 15 Sep 2026 10:00:00 GMT</pubDate>
  <description>Hello &lt;b&gt;world&lt;/b&gt;</description>
  <category>tech</category>
  <media:content url="https://ex.com/img.jpg" />
</item>
<item>
  <title>Second post</title>
  <link>https://ex.com/2</link>
  <guid>tag:ex.com,2026:2</guid>
  <content:encoded xmlns:content="http://purl.org/rss/1.0/modules/content/">Full body text here</content:encoded>
</item>
</channel></rss>"""

# Webflow-style cards: two anchors per card + description + date
WEBFLOW_HTML = """
<html><body>
<nav><a href="/about">About</a></nav>
<div class="collection-list">
  <div class="work-card-wrapper w-dyn-item"><div class="card in-grid">
    <a href="/blog/post-one" class="title-link"><h3>Post One Title</h3></a>
    <div><a href="#" class="category-pill"><p>Red Team</p></a>
         <p class="date">Apr 24, 2026</p></div>
    <p class="desc">Description of post one.</p>
    <a href="/blog/post-one" class="button">Read post</a>
  </div></div>
  <div class="work-card-wrapper w-dyn-item"><div class="card in-grid">
    <a href="/blog/post-two" class="title-link"><h3>Post Two Title</h3></a>
    <div><p class="date">May 1, 2026</p></div>
    <p class="desc">Description of post two.</p>
    <a href="/blog/post-two" class="button">Read post</a>
  </div></div>
</div>
<footer><a href="/blog/post-one">Post One Title</a></footer>
</body></html>"""

# Tailwind-style cards: image link with aria-label + text link, shared parent
TAILWIND_HTML = """
<html><body>
<div class="grid">
  <div class="card-root">
    <a aria-label="Read full article: Cool Research Post" href="/blog/cool-post">
      <img src="https://cdn.ex/img.png" srcset="https://cdn.ex/img.png 480w"></a>
    <a href="/blog/tag/launch"><span>Product Launch</span></a>
    <a href="/blog/cool-post">
      <p class="big">Cool Research Post</p>
      <p class="small">A spicy description of the post.</p>
      <span>Sep 10, 2026</span><span>4 min read</span>
    </a>
    <a href="/blog/cool-post">Read full article: Cool Research Post</a>
  </div>
</div>
</body></html>"""

MENU_HTML = """
<html><body>
<div class="mega-menu">
  <a href="/pricing">Pricing</a>
  <a href="/contact-sales">Request a demo</a>
  <a href="/compass">Compass Intelligent search</a>
</div>
<div class="list"><a href="/blog/only-post"><h3>The Only Post</h3><p>Text of it.</p><p>Aug 2, 2026</p></a></div>
</body></html>"""

THIN_WITH_FEED = """
<html><head>
<link rel="alternate" type="application/rss+xml" href="/feed.xml">
</head><body>
<div class="list"><a href="/blog/only-post"><h3>The Only Post</h3><p>Text of it.</p><p>Aug 2, 2026</p></a></div>
</body></html>"""

# Drupal/Tailwind cards (posit.co style): the title anchor lives inside an h3
# and carries a generic CTA aria-label; image is a root-relative <picture>;
# date is a plain-text ISO string (no <time> element).
POSIT_HTML = """
<html><body>
<nav role="navigation"><a href="/events">Events</a></nav>
<div class="view-content grid">
  <div class="views-row"><article class="group">
    <div><picture><img src="/sites/f/p1.png?itok=1" alt="cover"></picture></div>
    <div><span> AI </span></div>
    <div> 2026-09-15 </div>
    <h3 class="h3"><a aria-label="Read more" title="Read more" href="/blog/prove-it">Prove It With Posit</a></h3>
  </article></div>
  <div class="views-row"><article class="group">
    <div><picture><img src="/sites/f/p2.png?itok=2"></picture></div>
    <div> 2026-09-14 </div>
    <h3 class="h3"><a aria-label="Read more" href="/blog/snowflake">R, meet Snowflake</a></h3>
  </article></div>
  <div class="views-row"><article class="group">
    <div><picture><img src="/sites/f/p3.png?itok=3"></picture></div>
    <div> 2026-09-04 </div>
    <h3 class="h3"><a title="Read more" href="/blog/shiny">Publish Shiny apps</a></h3>
  </article></div>
</div>
<div class="promo">
  <a class="stretch" href="/events"><h4>Upcoming events</h4></a>
  <img src="/sites/f/event.png">
  <p>Join us at an upcoming event.</p>
</div>
</body></html>"""

# Overlay-link cards (DeepMind/LangChain style): the only anchor per card is
# a textless "stretch" link; title/image/date live in the card container.
OVERLAY_HTML = """
<html><body>
<main><div class="grid">
  <div class="card-wrap"><article class="card">
    <img src="/img/a.png">
    <h3>Overlay Card One</h3>
    <div class="card-date">September 2026</div>
    <a class="card__overlay-link" href="/blog/post-one"></a>
  </article></div>
  <div class="card-wrap"><article class="card">
    <img src="/img/b.png">
    <h3>Overlay Card Two</h3>
    <a class="stretch" href="/blog/post-two"></a>
  </article></div>
  <div class="card-wrap"><article class="card">
    <img src="/img/c.png">
    <h3>Overlay Card Three</h3>
    <a class="stretch" href="/blog/post-three"></a>
  </article></div>
</div></main>
</body></html>"""

ARTICLE_HTML = """
<html><head>
<meta property="og:title" content="The Article">
<meta property="article:published_time" content="2026-09-01T10:00:00Z">
<meta name="author" content="Jane Doe">
<meta property="og:image" content="https://ex.com/og.jpg">
<meta name="description" content="Meta description here.">
</head><body>
<nav>ignore me</nav>
<article>
  <h1>The Article</h1>
  <p>First paragraph with plenty of text to be considered real content.</p>
  <p>Second paragraph also with plenty of text to be considered real content.</p>
</article>
</body></html>"""

ARXIV_RESULT = """
<li class="arxiv-result">
  <p class="list-title"><a href="https://arxiv.org/abs/2609.14795">arXiv:2609.14795</a></p>
  <p class="title is-5 mathjax">A Great Paper on <span class="search-hit">Machine</span> Translation</p>
  <p class="authors"><span>Authors:</span> <a>Ada Lovelace</a>, <a>Grace Hopper</a></p>
  <p class="abstract mathjax"><span>Abstract</span>:
    <span class="abstract-full">We study translation quality across languages.</span></p>
  <p class="is-size-7"><span>Submitted</span> 13 September, 2026; <span>originally announced</span> September 2026.</p>
  <p class="comments"><span>Comments:</span> <span>Accepted at WMT26</span></p>
  <span class="tag" data-tooltip="Computation and Language">cs.CL</span>
</li>"""


def test_rss_parse_feed():
    items, feed_title = _parse_feed(RSS_XML)
    assert feed_title == "Sample Feed"
    assert len(items) == 2
    first = items[0]
    assert first.guid == "https://ex.com/1"
    assert first.title == "First post"
    assert first.summary == "Hello world"
    assert first.image_url == "https://ex.com/img.jpg"
    assert first.tags == ["tech"]
    assert first.published_at is not None
    second = items[1]
    assert second.content == "Full body text here"


@pytest.mark.asyncio
async def test_parse_pool_roundtrip():
    """run_parse executes off the loop (process pool by default) and matches
    the direct call. A worker that dies degrades to a thread, so a pool
    failure must not change results."""
    import asyncio
    from crawler.pool import run_parse, shutdown_parse_pool

    async def hammer():
        return await asyncio.gather(*(
            run_parse(extract_listing, WEBFLOW_HTML, "https://ex.com/blog")
            for _ in range(4)))

    pooled, direct = await asyncio.gather(hammer(), asyncio.to_thread(
        extract_listing, WEBFLOW_HTML, "https://ex.com/blog"))
    assert pooled == [direct] * 4
    shutdown_parse_pool()


def test_web_listing_webflow_cards():
    items = extract_listing(WEBFLOW_HTML, "https://ex.com/blog")
    by_url = {i["url"]: i for i in items}
    assert set(by_url) == {"https://ex.com/blog/post-one", "https://ex.com/blog/post-two"}
    one = by_url["https://ex.com/blog/post-one"]
    assert one["title"] == "Post One Title"
    assert one["summary"] == "Description of post one."
    assert one["published_at"] is not None
    assert "Red Team" in one["tags"]


def test_web_listing_tailwind_cards_and_cta_strip():
    items = extract_listing(TAILWIND_HTML, "https://ex.com/blog")
    assert len(items) == 1
    card = items[0]
    assert card["url"] == "https://ex.com/blog/cool-post"
    assert card["title"] == "Cool Research Post"  # "Read full article:" stripped
    assert card["summary"] == "A spicy description of the post."
    assert card["image_url"].startswith("https://cdn.ex/")
    assert "Product Launch" in card["tags"]
    assert card["published_at"] is not None


def test_web_listing_drops_menu_links():
    items = extract_listing(MENU_HTML, "https://ex.com/blog")
    urls = {i["url"] for i in items}
    assert "https://ex.com/pricing" not in urls
    assert "https://ex.com/contact-sales" not in urls
    assert "https://ex.com/compass" not in urls
    assert "https://ex.com/blog/only-post" in urls
    post = next(i for i in items if i["url"].endswith("only-post"))
    assert post["title"] == "The Only Post"
    assert post["published_at"] is not None


def test_web_listing_posit_style_cards():
    import calendar
    items = extract_listing(POSIT_HTML, "https://posit.co/blog")
    by_url = {i["url"]: i for i in items}
    # undated cross-section promo tile is dropped once a dominant section exists
    assert set(by_url) == {"https://posit.co/blog/prove-it",
                           "https://posit.co/blog/snowflake",
                           "https://posit.co/blog/shiny"}
    one = by_url["https://posit.co/blog/prove-it"]
    # real link text wins over the "Read more" aria-label/title CTA
    assert one["title"] == "Prove It With Posit"
    # root-relative <img src> is absolutized against the listing URL
    assert one["image_url"] == "https://posit.co/sites/f/p1.png?itok=1"
    # plain-text ISO date in the card is parsed
    assert one["published_at"] == calendar.timegm((2026, 9, 15, 12, 0, 0))


def test_web_listing_overlay_card_links():
    import calendar
    items = extract_listing(OVERLAY_HTML, "https://ex.com/blog")
    by_url = {i["url"]: i for i in items}
    assert set(by_url) == {"https://ex.com/blog/post-one",
                           "https://ex.com/blog/post-two",
                           "https://ex.com/blog/post-three"}
    one = by_url["https://ex.com/blog/post-one"]
    # title resolved from the card heading despite the textless anchor
    assert one["title"] == "Overlay Card One"
    assert one["image_url"] == "https://ex.com/img/a.png"
    # month-year card dates parse to the first of the month
    assert one["published_at"] == calendar.timegm((2026, 9, 1, 12, 0, 0))


def test_extract_article():
    article = extract_article(ARTICLE_HTML, "https://ex.com/the-article")
    assert article["title"] == "The Article"
    assert article["author"] == "Jane Doe"
    assert article["image_url"] == "https://ex.com/og.jpg"
    assert article["summary"] == "Meta description here."
    assert "First paragraph" in article["content"]
    assert article["published_at"] is not None


def test_arxiv_parse_search_page():
    items = parse_search_page(ARXIV_RESULT, "https://arxiv.org/search/?query=x")
    assert len(items) == 1
    p = items[0]
    assert p["guid"] == "2609.14795"
    assert p["url"] == "https://arxiv.org/abs/2609.14795"
    assert p["title"] == "A Great Paper on Machine Translation"
    assert p["author"] == "Ada Lovelace, Grace Hopper"
    assert p["content"].startswith("We study translation")
    assert p["published_at"] is not None
    assert "cs.CL" in p["tags"]
    assert p["extra"]["comments"] == "Accepted at WMT26"


def test_registry_routing():
    assert find_plugin("https://www.theguardian.com/x/rss").name == "rss"
    assert find_plugin("https://metr.org/feed.xml").name == "rss"
    # segment-style feed paths (e.g. CBC) must not fall through to the scraper
    assert find_plugin("https://www.cbc.ca/webfeed/rss/rss-topstories").name == "rss"
    assert find_plugin("https://example.org/rss/headlines").name == "rss"
    assert find_plugin("https://www.youtube.com/playlist?list=PLxyz").name == "youtube"
    assert find_plugin("https://arxiv.org/search/?query=mt").name == "arxiv"
    assert find_plugin("https://arxiv.org/abs/2609.14795").name == "arxiv"
    assert find_plugin("https://www.aisi.gov.uk/blog").name == "web"
    assert find_plugin("https://some-blog.example/posts").name == "web"
    assert find_plugin(
        "https://www.reddit.com/r/ottawa/top/?screen_view_count=6&t=week").name == "reddit"
    assert find_plugin("https://reddit.com/r/programming").name == "reddit"


def _mock_ctx(handler, url="https://example.com/x", known_dates=None, config=None):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    source = {"id": 0, "url": url, "name": "", "etag": "", "last_modified": "",
              "config": {}}
    return FetchContext(source=source, client=client,
                        known_dates=known_dates or {}, config=config or {})


@pytest.mark.asyncio
async def test_web_plugin_parses_feed_document():
    """A URL registered as a generic page can still serve RSS/Atom XML
    (e.g. cbc.ca/webfeed/rss/...): the scraper must parse it as a feed,
    not as HTML."""
    def handler(request):
        return httpx.Response(200, text=RSS_XML)

    ctx = _mock_ctx(handler, url="https://www.cbc.ca/webfeed/rss/rss-topstories")
    result = await WebPlugin().fetch(ctx)
    await ctx.client.aclose()
    assert result.source_name == "Sample Feed"
    assert [i.title for i in result.items] == ["First post", "Second post"]


@pytest.mark.asyncio
async def test_web_plugin_persists_discovered_feed_url():
    def handler(request):
        if request.url.path.endswith("feed.xml"):
            return httpx.Response(200, text=RSS_XML)
        return httpx.Response(200, text=THIN_WITH_FEED)

    ctx = _mock_ctx(handler, url="https://ex.com/blog",
                    config={"fetch_content": False})
    result = await WebPlugin().fetch(ctx)
    await ctx.client.aclose()
    assert result.config_updates == {"feed_url": "https://ex.com/feed.xml"}
    assert result.source_name == "Sample Feed"
    assert [i.url for i in result.items] == ["https://ex.com/1", "https://ex.com/2"]


@pytest.mark.asyncio
async def test_web_plugin_reports_no_feed_url_when_none_discovered():
    def handler(request):
        return httpx.Response(200, text=MENU_HTML)

    ctx = _mock_ctx(handler, url="https://ex.com/blog",
                    config={"fetch_content": False})
    result = await WebPlugin().fetch(ctx)
    await ctx.client.aclose()
    assert result.config_updates == {}
    assert [i.guid for i in result.items] == ["https://ex.com/blog/only-post"]


@pytest.mark.asyncio
async def test_get_text_retries_transient_errors(monkeypatch):
    monkeypatch.setattr("crawler.base.RETRY_BACKOFF", (0.0, 0.0))
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return httpx.Response(200, text="hello")

    ctx = _mock_ctx(handler)
    assert await ctx.get_text("https://example.com/x") == "hello"
    assert len(calls) == 2 and ctx.requests == 2
    await ctx.client.aclose()


@pytest.mark.asyncio
async def test_get_text_gives_up_after_two_retries(monkeypatch):
    monkeypatch.setattr("crawler.base.RETRY_BACKOFF", (0.0, 0.0))
    calls = []

    def handler(request):
        calls.append(str(request.url))
        raise httpx.ConnectError("")

    ctx = _mock_ctx(handler)
    with pytest.raises(httpx.ConnectError):
        await ctx.get_text("https://example.com/x")
    assert len(calls) == 3  # initial attempt + 2 retries
    await ctx.client.aclose()


@pytest.mark.asyncio
async def test_get_text_does_not_retry_http_errors():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(500)

    ctx = _mock_ctx(handler)
    with pytest.raises(httpx.HTTPStatusError):
        await ctx.get_text("https://example.com/x")
    assert len(calls) == 1  # the server answered: retrying would not help
    await ctx.client.aclose()


def _listing_page(names, next_href=None):
    cards = "".join(
        f'<div class="card-wrap"><article class="card">'
        f'<img src="/img/{n}.png">'
        f'<h3>Post {n.title()}</h3>'
        f'<div class="card-date">September 2026</div>'
        f'<a class="stretch" href="/blog/{n}"></a></article></div>'
        for n in names)
    nxt = f'<a rel="next" href="{next_href}">Next</a>' if next_href else ""
    return f'<html><body><main><div class="grid">{cards}</div>{nxt}</main></body></html>'


def _paged_handler(page1, page2, fetched):
    def handler(request):
        fetched.append(str(request.url))
        page = request.url.params.get("page")
        return httpx.Response(200, text=page1 if not page else page2)
    return handler


@pytest.mark.asyncio
async def test_web_pagination_stops_at_known_items():
    """A page holding items the DB already has ends pagination: everything
    deeper is older, while new items on that page are still collected."""
    fetched = []
    page1 = _listing_page(["p1", "p2", "p3", "p4", "p5"], "/blog?page=2")
    page2 = _listing_page(["p6", "p7", "p8", "p9", "p10"], "/blog?page=3")
    ctx = _mock_ctx(_paged_handler(page1, page2, fetched), url="https://ex.com/blog",
                    known_dates={"https://ex.com/blog/p5": 1.0},
                    config={"fetch_content": False})
    result = await WebPlugin().fetch(ctx)
    await ctx.client.aclose()
    assert fetched == ["https://ex.com/blog"]
    assert {i.guid for i in result.items} == \
        {f"https://ex.com/blog/p{i}" for i in range(1, 6)}


@pytest.mark.asyncio
async def test_web_pagination_continues_when_all_items_new():
    fetched = []
    page1 = _listing_page(["p1", "p2", "p3", "p4", "p5"], "/blog?page=2")
    page2 = _listing_page(["p6", "p7", "p8", "p9", "p10"])
    ctx = _mock_ctx(_paged_handler(page1, page2, fetched), url="https://ex.com/blog",
                    config={"fetch_content": False})
    result = await WebPlugin().fetch(ctx)
    await ctx.client.aclose()
    assert fetched == ["https://ex.com/blog", "https://ex.com/blog?page=2"]
    assert len(result.items) == 10


@pytest.mark.asyncio
async def test_web_pagination_stop_on_known_can_be_disabled():
    fetched = []
    page1 = _listing_page(["p1", "p2", "p3", "p4", "p5"], "/blog?page=2")
    page2 = _listing_page(["p6", "p7", "p8", "p9", "p10"])
    ctx = _mock_ctx(_paged_handler(page1, page2, fetched), url="https://ex.com/blog",
                    known_dates={"https://ex.com/blog/p5": 1.0},
                    config={"fetch_content": False, "stop_on_known": False})
    result = await WebPlugin().fetch(ctx)
    await ctx.client.aclose()
    assert fetched == ["https://ex.com/blog", "https://ex.com/blog?page=2"]
    assert len(result.items) == 10


@pytest.mark.asyncio
async def test_arxiv_search_pagination_stops_at_known_items():
    fetched = []

    def handler(request):
        fetched.append(str(request.url))
        start = int(request.url.params.get("start", 0))
        return httpx.Response(
            200, text=ARXIV_RESULT.replace("2609.14795", f"2609.0000{start + 1}"))

    url = "https://arxiv.org/search/?query=mt&size=1"
    ctx = _mock_ctx(handler, url=url, known_dates={"2609.00001": 1.0},
                    config={"max_results": 2, "page_delay": 0})
    result = await ArxivPlugin().fetch(ctx)
    await ctx.client.aclose()
    assert len(fetched) == 1
    assert [i.guid for i in result.items] == ["2609.00001"]


@pytest.mark.asyncio
async def test_arxiv_search_pagination_continues_when_all_items_new():
    fetched = []

    def handler(request):
        fetched.append(str(request.url))
        start = int(request.url.params.get("start", 0))
        return httpx.Response(
            200, text=ARXIV_RESULT.replace("2609.14795", f"2609.0000{start + 1}"))

    url = "https://arxiv.org/search/?query=mt&size=1"
    ctx = _mock_ctx(handler, url=url,
                    config={"max_results": 2, "page_delay": 0})
    result = await ArxivPlugin().fetch(ctx)
    await ctx.client.aclose()
    assert len(fetched) == 2
    assert [i.guid for i in result.items] == ["2609.00001", "2609.00002"]


def test_sticky_failure_detection():
    assert yt_mod._sticky_failure(Exception(
        "ERROR: [youtube] x: Sign in to confirm you're not a bot."))
    assert yt_mod._sticky_failure(Exception(
        "This video is available to this channel's members on level: X"))
    assert yt_mod._sticky_failure(Exception(
        "Join this channel from your computer or mobile app"))
    assert not yt_mod._sticky_failure(Exception("HTTP Error 500: Internal Server Error"))
    assert not yt_mod._sticky_failure(TimeoutError("timed out"))


def _yt_playlist(entries):
    return {"_type": "playlist", "title": "Chan",
            "entries": [{"id": v, "title": f"Video {v}",
                         "url": f"https://www.youtube.com/watch?v={v}"}
                        for v in entries]}


@pytest.mark.asyncio
async def test_youtube_date_fetch_bails_on_error_streak(monkeypatch):
    """Once several per-video extractions fail (bot check), the run stops
    hammering YouTube and remembers the failures for later refreshes."""
    vids = [f"v{i}" for i in range(20)]
    calls = []

    def fake_extract(url, opts):
        calls.append(opts)
        if opts.get("extract_flat"):
            assert opts["playlistend"] == 20
            return _yt_playlist(vids)
        raise Exception("ERROR: [youtube] x: Sign in to confirm you're not a bot.")

    monkeypatch.setattr(yt_mod, "_extract", fake_extract)
    ctx = _mock_ctx(lambda r: httpx.Response(200),
                    url="https://www.youtube.com/@chan/videos",
                    config={"max_items": 20, "date_fetch_max_errors": 2})
    result = await yt_mod.YouTubePlugin().fetch(ctx)
    await ctx.client.aclose()
    full = [c for c in calls if not c.get("extract_flat")]
    assert len(calls) == 1 + len(full)
    assert 0 < len(full) < 20
    skip = result.config_updates["date_fetch_skip"]
    assert set(skip) == set(vids[:len(full)])


@pytest.mark.asyncio
async def test_youtube_date_fetch_marks_sticky_failures(monkeypatch):
    def fake_extract(url, opts):
        if opts.get("extract_flat"):
            return _yt_playlist(["v1", "v2", "v3"])
        raise Exception("Join this channel from your computer or mobile app")

    monkeypatch.setattr(yt_mod, "_extract", fake_extract)
    ctx = _mock_ctx(lambda r: httpx.Response(200),
                    url="https://www.youtube.com/@chan/videos",
                    config={"date_fetch_max_errors": 10})
    result = await yt_mod.YouTubePlugin().fetch(ctx)
    await ctx.client.aclose()
    assert set(result.config_updates["date_fetch_skip"]) == {"v1", "v2", "v3"}


@pytest.mark.asyncio
async def test_youtube_date_fetch_skips_remembered_failures(monkeypatch):
    fetched = []

    def fake_extract(url, opts):
        if opts.get("extract_flat"):
            return _yt_playlist(["v1", "v2", "v3"])
        fetched.append(url)
        return {"timestamp": 1758400000, "description": "desc",
                "uploader": "Chan", "thumbnail": "https://i.ytimg.com/x.jpg"}

    monkeypatch.setattr(yt_mod, "_extract", fake_extract)
    ctx = _mock_ctx(lambda r: httpx.Response(200),
                    url="https://www.youtube.com/@chan/videos",
                    config={"date_fetch_skip": {"v1": time.time()}})
    result = await yt_mod.YouTubePlugin().fetch(ctx)
    await ctx.client.aclose()
    assert [u.rsplit("=", 1)[1] for u in fetched] == ["v2", "v3"]
    assert result.config_updates == {}
    by_guid = {i.guid: i for i in result.items}
    assert by_guid["v2"].published_at is not None
    assert by_guid["v1"].published_at is None


def _reddit_child(pid, **kw):
    data = {
        "kind": "t3", "name": f"t3_{pid}", "id": pid, "title": f"Post {pid}",
        "permalink": f"/r/ottawa/comments/{pid}/post_{pid}/",
        "url": f"https://www.reddit.com/r/ottawa/comments/{pid}/post_{pid}/",
        "is_self": True, "selftext": "", "author": "someone",
        "created_utc": 1758400000, "score": 100, "num_comments": 5,
        "subreddit": "ottawa", "domain": "self.ottawa",
        "thumbnail": "self", "link_flair_text": None,
    }
    data.update(kw)
    return {"kind": "t3", "data": data}


def _reddit_payload(children, after=None):
    return json.dumps({"kind": "Listing",
                       "data": {"after": after, "children": children}})


def test_reddit_parse_target():
    sub, listing, t = reddit_parse_target(
        "https://www.reddit.com/r/ottawa/top/?screen_view_count=6&t=week")
    assert (sub, listing, t) == ("ottawa", "top", "week")
    assert reddit_parse_target("https://reddit.com/r/programming") == \
        ("programming", "hot", "")
    assert reddit_parse_target("https://old.reddit.com/r/ottawa+canada/new/") == \
        ("ottawa+canada", "new", "")
    with pytest.raises(FetchError):
        reddit_parse_target("https://www.reddit.com/r/ottawa/comments/abc/a_post/")
    with pytest.raises(FetchError):
        reddit_parse_target("https://www.reddit.com/user/someone/")


def test_reddit_parse_listing_maps_fields():
    posts = reddit_parse_listing(_reddit_payload([
        _reddit_child("a", score=250, is_self=False,
                      url="https://ottawacitizen.com/news/local",
                      domain="ottawacitizen.com", link_flair_text="News",
                      thumbnail="https://b.thumbs.redditmedia.com/x.jpg"),
        _reddit_child("b", score=120, selftext="Long self post."),
        _reddit_child("c", score=80, is_self=False,
                      url="https://www.reddit.com/gallery/xyz",
                      domain="reddit.com"),
        {"kind": "t1", "data": {"id": "ignored"}},
    ]))
    by = {p["guid"]: p for p in posts}
    assert set(by) == {"t3_a", "t3_b", "t3_c"}
    a = by["t3_a"]
    assert a["url"] == "https://www.reddit.com/r/ottawa/comments/a/post_a/"
    assert a["title"] == "Post a"
    assert a["tags"] == ["News"]
    assert a["image_url"] == "https://b.thumbs.redditmedia.com/x.jpg"
    assert a["published_at"] == 1758400000
    assert a["extra"] == {"kind": "reddit", "subreddit": "ottawa", "score": 250,
                          "num_comments": 5,
                          "permalink": "https://www.reddit.com/r/ottawa/comments/a/post_a/",
                          "domain": "ottawacitizen.com", "external": True,
                          "link_url": "https://ottawacitizen.com/news/local"}
    assert a["content"] == ('<p class="md-link"><a href="https://ottawacitizen.com/news/local"'
                            ' title="https://ottawacitizen.com/news/local">'
                            'ottawacitizen.com ↗</a></p>')
    b = by["t3_b"]
    assert b["url"] == "https://www.reddit.com/r/ottawa/comments/b/post_b/"
    assert b["summary"] == "Long self post."
    assert b["extra"]["external"] is False
    assert b["content"] == ""
    c = by["t3_c"]
    assert c["extra"]["external"] is False
    assert c["url"] == "https://www.reddit.com/r/ottawa/comments/c/post_c/"
    assert "reddit.com/gallery/xyz" not in c["content"]


@pytest.mark.asyncio
async def test_reddit_min_score_filter_and_json_url():
    fetched = []
    children = [
        _reddit_child("a", score=250, is_self=False,
                      url="https://ottawacitizen.com/news/local",
                      domain="ottawacitizen.com"),
        _reddit_child("b", score=120, selftext="Long self post."),
        _reddit_child("c", score=80, is_self=False,
                      url="https://www.ex.com/story", domain="ex.com"),
        _reddit_child("d", score=15),
    ]

    def handler(request):
        fetched.append(str(request.url))
        return httpx.Response(200, text=_reddit_payload(children))

    url = "https://www.reddit.com/r/ottawa/top/?screen_view_count=6&t=week"
    ctx = _mock_ctx(handler, url=url, config={"min_score": 100, "page_delay": 0})
    result = await RedditPlugin().fetch(ctx)
    await ctx.client.aclose()
    assert len(fetched) == 1
    req = httpx.URL(fetched[0])
    assert req.host == "www.reddit.com" and req.path == "/r/ottawa/top.json"
    assert req.params["t"] == "week"
    assert req.params["raw_json"] == "1"
    assert req.params["limit"] == "100"
    assert "screen_view_count" not in req.params
    assert [i.guid for i in result.items] == ["t3_a", "t3_b"]
    assert all(i.extra["score"] >= 100 for i in result.items)
    assert result.items[0].url == "https://www.reddit.com/r/ottawa/comments/a/post_a/"
    assert "https://ottawacitizen.com/news/local" in result.items[0].content
    assert result.source_name == "r/ottawa · top · week"


@pytest.mark.asyncio
async def test_reddit_external_only():
    def handler(request):
        return httpx.Response(200, text=_reddit_payload([
            _reddit_child("a", score=250, is_self=False,
                          url="https://www.ex.com/story", domain="ex.com"),
            _reddit_child("b", score=200, selftext="A self post."),
        ]))

    ctx = _mock_ctx(handler, url="https://www.reddit.com/r/ottawa/top/",
                    config={"external_only": True, "page_delay": 0})
    result = await RedditPlugin().fetch(ctx)
    await ctx.client.aclose()
    assert [i.guid for i in result.items] == ["t3_a"]


@pytest.mark.asyncio
async def test_reddit_top_stops_paging_below_min_score():
    """top is score-descending: once a page falls under min_score, deeper
    pages cannot contain qualifying posts and pagination stops."""
    fetched = []

    def handler(request):
        fetched.append(str(request.url))
        if request.url.params.get("after"):
            return httpx.Response(200, text=_reddit_payload(
                [_reddit_child("d", score=40), _reddit_child("e", score=10)]))
        return httpx.Response(200, text=_reddit_payload(
            [_reddit_child("a", score=300), _reddit_child("b", score=200),
             _reddit_child("c", score=50)], after="t3_c"))

    ctx = _mock_ctx(handler, url="https://www.reddit.com/r/ottawa/top/?t=week",
                    config={"min_score": 100, "page_delay": 0})
    result = await RedditPlugin().fetch(ctx)
    await ctx.client.aclose()
    assert len(fetched) == 1
    assert [i.guid for i in result.items] == ["t3_a", "t3_b"]


@pytest.mark.asyncio
async def test_reddit_new_stops_paging_on_known_items():
    fetched = []

    def handler(request):
        fetched.append(str(request.url))
        if request.url.params.get("after"):
            return httpx.Response(200, text=_reddit_payload([_reddit_child("c")]))
        return httpx.Response(200, text=_reddit_payload(
            [_reddit_child("a"), _reddit_child("b")], after="t3_b"))

    ctx = _mock_ctx(handler, url="https://www.reddit.com/r/ottawa/new/",
                    known_dates={"t3_b": 1.0}, config={"page_delay": 0})
    result = await RedditPlugin().fetch(ctx)
    await ctx.client.aclose()
    assert len(fetched) == 1
    assert [i.guid for i in result.items] == ["t3_a", "t3_b"]


CHALLENGE_HTML = """<html><head><title>reddit</title></head><body>
<form hidden method="GET" action="/r/ottawa/top/">
<input type="hidden" name="solution" />
<input type="hidden" name="js_challenge" value="1"/>
<input type="hidden" name="jsc_token" value="tok123"/>
<input type="hidden" name="jsc_orig_r" value=""/>
</form>
<script nonce="x">document.addEventListener("DOMContentLoaded",async function(){
var e=document.forms[0],n=(e.onsubmit=function(t){return true},
await(async e=>e+e)("e7354e52cd020c8a"));e.elements.namedItem("solution").value=n,
e.requestSubmit()},{once:true});</script>
</body></html>"""


def test_reddit_challenge_solution():
    assert is_challenge_page(CHALLENGE_HTML)
    assert not is_challenge_page("<html><body>Blocked</body></html>")
    url = challenge_solution(CHALLENGE_HTML, "https://www.reddit.com/r/ottawa/top/?t=week")
    assert url is not None
    parsed = httpx.URL(url)
    assert parsed.path == "/r/ottawa/top/"
    assert parsed.params["t"] == "week"
    assert parsed.params["solution"] == "e7354e52cd020c8ae7354e52cd020c8a"
    assert parsed.params["js_challenge"] == "1"
    assert parsed.params["jsc_token"] == "tok123"
    assert parsed.params["jsc_orig_r"] == ""
    assert challenge_solution("<html></html>", "https://ex.com/x") is None


@pytest.mark.asyncio
async def test_reddit_solves_js_challenge_when_blocked():
    """Reddit gates logged-out .json access with a verification page on some
    networks: the plugin solves it and retries, once."""
    calls = []

    def handler(request):
        u = str(request.url)
        calls.append(u)
        if request.url.path.endswith(".json"):
            if any("solution=" in c for c in calls):
                return httpx.Response(200, text=_reddit_payload(
                    [_reddit_child("a", score=250)]))
            return httpx.Response(403, text="<html><body>blocked</body></html>")
        if "solution=" in u:
            return httpx.Response(200, text="<html><body>real page</body></html>")
        return httpx.Response(200, text=CHALLENGE_HTML)

    ctx = _mock_ctx(handler, url="https://www.reddit.com/r/ottawa/top/?t=week",
                    config={"page_delay": 0})
    result = await RedditPlugin().fetch(ctx)
    await ctx.client.aclose()
    assert [i.guid for i in result.items] == ["t3_a"]
    assert len(calls) == 4  # .json, challenge page, solve, .json retry
    solve = next(c for c in calls if "solution=" in c)
    assert "solution=e7354e52cd020c8ae7354e52cd020c8a" in solve
    assert "js_challenge=1" in solve and "jsc_token=tok123" in solve


@pytest.mark.asyncio
async def test_reddit_blocked_without_challenge_fails_clearly():
    def handler(request):
        return httpx.Response(403, text="<html><body>blocked</body></html>")

    ctx = _mock_ctx(handler, url="https://www.reddit.com/r/ottawa/top/",
                    config={"page_delay": 0})
    with pytest.raises(FetchError, match="blocking unauthenticated"):
        await RedditPlugin().fetch(ctx)
    await ctx.client.aclose()


def test_reddit_media_gallery_video_image():
    gallery = _reddit_child(
        "g", is_self=False, url="https://www.reddit.com/gallery/g", domain="reddit.com",
        is_gallery=True,
        gallery_data={"items": [{"media_id": "m2"}, {"media_id": "m1"}]},
        media_metadata={
            "m1": {"s": {"u": "https://i.redd.it/one.jpg"}},
            "m2": {"s": {"u": "https://i.redd.it/two.jpg"}},
        })
    video = _reddit_child(
        "v", is_self=False, url="https://v.redd.it/abc", domain="v.redd.it",
        secure_media={"reddit_video": {
            "fallback_url": "https://v.redd.it/abc/DASH_720.mp4?source=fallback"}},
        preview={"images": [{"source": {"url": "https://i.redd.it/prev.jpg"}}]})
    image = _reddit_child("i", is_self=False, url="https://i.redd.it/pic.png",
                          domain="i.redd.it")
    text = _reddit_child("s", selftext="plain text",
                         selftext_html='<div class="md"><p>rich &amp; good</p></div>')
    by = {p["guid"]: p for p in reddit_parse_listing(
        _reddit_payload([gallery, video, image, text]))}

    g = by["t3_g"]
    assert g["image_url"] == "https://i.redd.it/two.jpg"  # gallery order
    assert g["content"].count("<img") == 2
    assert g["content"].index("two.jpg") < g["content"].index("one.jpg")
    assert "video" not in g["extra"]

    v = by["t3_v"]
    assert v["extra"]["video"] is True
    assert v["image_url"] == "https://i.redd.it/prev.jpg"
    assert '<video class="md-media" controls preload="metadata"' in v["content"]
    assert 'poster="https://i.redd.it/prev.jpg"' in v["content"]
    assert '<source src="https://v.redd.it/abc/DASH_720.mp4" type="video/mp4">' in v["content"]

    i = by["t3_i"]
    assert i["image_url"] == "https://i.redd.it/pic.png"
    assert '<img src="https://i.redd.it/pic.png"' in i["content"]
    assert "video" not in i["extra"]

    s = by["t3_s"]
    assert "rich &amp; good" in s["content"]
    assert s["content"].startswith("<div class=\"md\">")
    assert s["image_url"] == ""


@pytest.mark.asyncio
async def test_reddit_top_defaults_to_past_week():
    fetched = []

    def handler(request):
        fetched.append(str(request.url))
        return httpx.Response(200, text=_reddit_payload([]))

    ctx = _mock_ctx(handler, url="https://www.reddit.com/r/ottawa/top/",
                    config={"page_delay": 0})
    result = await RedditPlugin().fetch(ctx)
    await ctx.client.aclose()
    assert httpx.URL(fetched[0]).params["t"] == "week"
    assert result.source_name == "r/ottawa · top · week"
