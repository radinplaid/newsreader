"""Offline parser tests: fixture HTML/XML snippets, no network."""
import pytest
import httpx

from crawler import find_plugin
from crawler.base import FetchContext
from crawler.plugins.arxiv import parse_abs_page, parse_search_page
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


def _mock_ctx(handler, url="https://example.com/x"):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    source = {"id": 0, "url": url, "name": "", "etag": "", "last_modified": "",
              "config": {}}
    return FetchContext(source=source, client=client, known_dates={}, config={})


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
