"""Generic HTML news/blog listing scraper.

Strategy (no per-site code needed):
1. Collect candidate detail-page anchors (same host, not nav/footer/tag
   pages, not pagination).
2. Group anchors by target URL; the lowest common ancestor of a URL's
   anchors is the "card" containing it (works for Webflow CMS cards,
   Tailwind article cards, and classic <article> listings alike).
3. Extract title/summary/date/image/tags heuristically from each card.
4. If nothing repeats (or the page is a single article), fall back to
   single-article extraction.

Per-site overrides via source config: item_selector, title_selector,
date_format are respected if present; otherwise heuristics are used.
"""
from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from urllib.parse import parse_qs, unquote, urlparse

from crawler import register
from crawler.base import Plugin, FetchContext, FetchResult, ParsedItem
from crawler.pool import run_parse
from crawler.textutil import (absolutize, clean, first_image_src, find_date_in_text,
                              host_of, parse_date, soup_from, text_of)

CHROME_TAGS = ("nav", "header", "footer", "aside", "script", "style", "noscript", "form", "template")

_BLOCK_PATH = re.compile(
    r"/(tag|tags|category|categories|author|authors|page|pages|users?|members?|"
    r"jobs?|careers?|about|contact|privacy|terms|search|login|signup|share)(/|$)", re.I)
_SKIP_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".css", ".ico",
             ".xml", ".rss", ".json", ".zip", ".mp3", ".mp4", ".avi", ".mov", ".woff", ".woff2")
_PAGINATION_QS = {"page", "p", "paged", "pg", "start", "from", "offset"}
_TITLE_PRIO_HEADING, _TITLE_PRIO_ARIA, _TITLE_PRIO_ATTR, _TITLE_PRIO_ALT, _TITLE_PRIO_TEXT = 0, 1, 2, 3, 4
_TITLE_PRIO_NONE = 5

# menu/CTA prefixes that pollute titles scraped from links
_CTA_PREFIX = re.compile(
    r"^(read( full)?( the)?( article| post| more| story)?|continue reading|learn more|"
    r"see (all|more)|view (all|more)|explore( now)?|more about|watch now|listen now|"
    r"open( article)?)\s*[:\-–—]?\s*",
    re.I)


def _clean_title(text: str) -> str:
    text = clean(text)
    for _ in range(2):
        stripped = _CTA_PREFIX.sub("", text).strip(" :-–—")
        if stripped == text:
            break
        text = stripped
    return text


def _is_chrome(node) -> bool:
    while node is not None:
        if node.name in CHROME_TAGS or node.get("role") in ("navigation", "banner", "contentinfo"):
            return True
        node = node.parent
    return False


def _same_site(base_netloc: str, netloc: str) -> bool:
    if not netloc:
        return True
    return host_of(f"https://{netloc}") == base_netloc


def _anchor_title(a) -> tuple[int, str]:
    for level in range(1, 7):
        h = a.find(f"h{level}")
        if h is not None:
            text = text_of(h)
            if text:
                return (_TITLE_PRIO_HEADING, text)
    # aria-label/title attributes are often generic CTAs ("Read more") that
    # clean to nothing — fall through to img alt / link text instead.
    for prio, attr in ((_TITLE_PRIO_ARIA, "aria-label"), (_TITLE_PRIO_ATTR, "title")):
        raw = clean(a.get(attr))
        if _clean_title(raw):
            return (prio, raw)
    img = a.find("img")
    if img is not None:
        alt = clean(img.get("alt"))
        if len(alt) >= 4:
            return (_TITLE_PRIO_ALT, alt)
    text = text_of(a)
    if text:
        return (_TITLE_PRIO_TEXT, text)
    # textless card link (Webflow/overlay "stretch" anchors): keep it and let
    # card extraction resolve the title from the card's own heading
    return (_TITLE_PRIO_NONE, "")


def _candidate_anchors(soup, base_url: str) -> list[tuple]:
    base = urlparse(base_url)
    base_netloc = host_of(base_url)
    base_path = (base.path or "/").rstrip("/")
    out = []
    for a in soup.find_all("a", href=True):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("mailto:", "javascript:", "tel:", "data:")):
            continue
        if href.startswith("#"):
            continue
        absu = absolutize(base_url, href)
        p = urlparse(absu)
        if p.scheme not in ("http", "https") or not _same_site(base_netloc, p.netloc):
            continue
        path = unquote(p.path)
        if not path or path == "/":
            continue
        lower = path.lower()
        if _BLOCK_PATH.search(lower) or lower.endswith(_SKIP_EXT):
            continue
        if path.rstrip("/") == base_path and not p.query:
            continue
        qs = parse_qs(p.query)
        if qs and all(k in _PAGINATION_QS for k in qs):
            continue  # pagination link
        prio, title = _anchor_title(a)
        title = _clean_title(title)
        if prio == _TITLE_PRIO_NONE:
            pass  # textless card link — title comes from the card heading
        elif not title or len(title) < 3 or len(title) > 300:
            continue
        if _is_chrome(a):
            continue
        img = first_image_src(a, base_url)
        out.append((a, absu, (prio, title), img))
    return out


def _parent_chain(node) -> list:
    chain = []
    while node is not None:
        chain.append(node)
        node = node.parent
    return chain


def _lca(nodes: list) -> object | None:
    if not nodes:
        return None
    chains = [_parent_chain(n) for n in nodes]
    common = None
    for depth in range(min(len(c) for c in chains)):
        candidate = chains[0][-1 - depth]
        if all(c[-1 - depth] is candidate for c in chains):
            common = candidate
        else:
            break
    return common


def _detail_link_count(root) -> int:
    return len(root.find_all("a", href=True))


def _card_date(root) -> float | None:
    t = root.find("time")
    if t is not None:
        dt = t.get("datetime") or text_of(t)
        ts = parse_date(dt)
        if ts:
            return ts
    meta = root.find("meta", attrs={"property": re.compile("article:published_time|og:updated_time")})
    if meta is not None and meta.get("content"):
        ts = parse_date(meta["content"])
        if ts:
            return ts
    return find_date_in_text(text_of(root))


def _card_tags(root) -> list[str]:
    tags = []
    for a in root.find_all("a", href=True):
        href = (a.get("href") or "").lower()
        if "/tag/" in href or "/tags/" in href or "/category/" in href or "/categories/" in href:
            text = text_of(a)
            if text and 1 < len(text) < 40:
                tags.append(text)
    # class-based pills/badges (e.g. Webflow category pills)
    for el in root.find_all(attrs={"class": re.compile(r"\b(tag|pill|badge|category|chip)\b", re.I)}):
        if el.name in ("time", "span") or el.find("time"):
            continue
        text = text_of(el)
        if text and 1 < len(text) < 40 and find_date_in_text(text) is None:
            tags.append(text)
    return list(dict.fromkeys(tags))[:6]


def _extract_from_root(root, url: str, group_title: tuple[int, str],
                       group_img: str, base_url: str = "") -> dict | None:
    text = text_of(root)
    if not text or len(text) > 4000:
        return None
    title = ""
    for level in range(1, 7):
        h = root.find(f"h{level}")
        if h is not None:
            t = text_of(h)
            if t:
                title = t
                break
    if not title:
        aria = clean(root.get("aria-label")) if root.name == "a" else ""
        title = aria or group_title[1]
    title = _clean_title(title)
    if not title or len(title) < 3:
        return None

    title_norm = title.strip().lower()
    desc = ""
    paras = [text_of(p) for p in root.find_all("p")]
    paras = [t for t in paras if t and t.strip().lower() != title_norm
             and find_date_in_text(t) is None]
    paras = [t for t in paras if 15 <= len(t) <= 500]
    if paras:
        desc = max(paras, key=len)
    if not desc:
        for el in root.find_all(attrs={"class": re.compile("description|excerpt|summary|deck|subtitle", re.I)}):
            t = text_of(el)
            if 15 <= len(t) <= 500:
                desc = t
                break
    published = _card_date(root)
    image = first_image_src(root, base_url) or group_img
    author_el = root.find(attrs={"class": re.compile("author|byline", re.I)})
    author = text_of(author_el) if author_el is not None else ""
    if len(author) > 80:
        author = ""
    return {
        "guid": url, "url": url, "title": clean(title), "summary": desc,
        "published_at": published, "image_url": image, "author": author,
        "tags": _card_tags(root),
    }


def _richness(item: dict) -> int:
    return (2 if item.get("published_at") else 0) + (2 if item.get("summary") else 0) \
        + (1 if item.get("image_url") else 0) + (1 if item.get("author") else 0)


def _card_from_anchor(a, url: str, title: tuple[int, str], img: str,
                      base_url: str = "") -> dict | None:
    """Extract a card from an anchor, climbing to enclosing containers while
    they still look like a single card (few links, bounded text)."""
    node = a
    best: dict | None = None
    while node is not None and node.name not in ("html", "body", "[document]"):
        if len(text_of(node)) > 4000 or _detail_link_count(node) > 6:
            break
        card = _extract_from_root(node, url, title, img, base_url)
        if card:
            if best is None or _richness(card) > _richness(best):
                best = card
            if card.get("published_at") and card.get("summary"):
                break
        node = node.parent
    return best


def extract_listing(html: str, base_url: str, max_items: int = 300) -> list[dict]:
    soup = soup_from(html)
    anchors = _candidate_anchors(soup, base_url)
    by_url: dict[str, list[tuple]] = defaultdict(list)
    for a, url, title, img in anchors:
        by_url[url].append((a, title, img))

    results: list[dict] = []
    for url, group in by_url.items():
        best_title = min((t for _, t, _ in group), key=lambda x: x[0])
        best_img = next((img for _, _, img in group if img), "")
        card: dict | None = None
        if len(group) > 1:
            root = _lca([a for a, _, _ in group])
            if root is not None and root.name not in ("html", "body", "[document]"):
                n_links = _detail_link_count(root)
                if n_links <= max(6, len(group) * 2):
                    card = _extract_from_root(root, url, best_title, best_img, base_url)
        if card is None:
            card = _card_from_anchor(group[0][0], url, best_title, best_img, base_url)
        if card and card.get("title"):
            results.append(card)

    # prefer the richest card per URL and drop dupes
    best_by_url: dict[str, dict] = {}
    for item in results:
        cur = best_by_url.get(item["url"])
        if cur is None or _richness(item) > _richness(cur):
            best_by_url[item["url"]] = item

    # Drop menu/product singles: keep an anchor-only item only when it has a
    # date, or it lives in the dominant section of the listing (e.g. /blog/*),
    # or it looks like a full card (summary + image).
    def first_seg(url: str) -> str:
        path = urlparse(url).path.strip("/")
        return path.split("/")[0] if path else ""

    seg_counts: dict[str, int] = defaultdict(int)
    for url in best_by_url:
        seg_counts[first_seg(url)] += 1
    dominant_seg = max(seg_counts, key=lambda s: seg_counts[s]) if seg_counts else ""
    dominant_ok = dominant_seg and seg_counts[dominant_seg] >= 3

    items = []
    for url, item in best_by_url.items():
        if item.get("published_at") or (dominant_ok and first_seg(url) == dominant_seg) \
                or (not dominant_ok and item.get("summary") and item.get("image_url")):
            items.append(item)
    items.sort(key=lambda x: (x.get("published_at") or 0), reverse=True)
    return items[:max_items]


def extract_article(html: str, url: str) -> dict:
    soup = soup_from(html)
    for bad in soup(CHROME_TAGS):
        bad.decompose()
    for el in soup.find_all(attrs={"role": ["navigation", "banner", "contentinfo"]}) or []:
        el.decompose()

    title = ""
    og = soup.find("meta", attrs={"property": "og:title"})
    if og is not None and og.get("content"):
        title = clean(og["content"])
    if not title:
        h1 = soup.find("h1")
        title = text_of(h1) or clean(soup.title.get_text() if soup.title else "") or url

    published = None
    for sel, attr in ((("meta", {"property": "article:published_time"}), "content"),
                      (("meta", {"name": "date"}), "content"),
                      (("time", {}), "datetime")):
        el = soup.find(*sel)
        if el is not None and el.get(attr):
            published = parse_date(el[attr])
            if published:
                break
    if not published:
        published = find_date_in_text(text_of(soup))

    author = ""
    for sel in (("meta", {"name": "author"}), ("meta", {"property": "article:author"})):
        el = soup.find(*sel)
        if el is not None and el.get("content"):
            author = clean(el["content"])
            break
    if not author:
        el = soup.find(attrs={"class": re.compile("author|byline", re.I)})
        author = text_of(el)

    image = ""
    for sel in (("meta", {"property": "og:image"}), ("meta", {"name": "twitter:image"})):
        el = soup.find(*sel)
        if el is not None and el.get("content"):
            image = absolutize(url, el["content"])
            break
    if not image:
        main = soup.find("article") or soup.find("main") or soup
        image = first_image_src(main, url)

    summary = ""
    for sel in (("meta", {"name": "description"}), ("meta", {"property": "og:description"})):
        el = soup.find(*sel)
        if el is not None and el.get("content"):
            summary = clean(el["content"])
            break

    container = soup.find("article") or soup.find("main") or soup
    paras = [text_of(p) for p in container.find_all(["p", "h2", "h3", "li", "blockquote", "pre"])]
    paras = [p for p in paras if len(p) > 40]
    content = "\n\n".join(paras)[:12000]
    if not summary and paras:
        summary = paras[0][:300]
    return {
        "guid": url, "url": url, "title": title, "summary": summary,
        "content": content, "author": author, "published_at": published,
        "image_url": image,
    }


def _discover_feed_url(soup, base_url: str) -> str | None:
    """Find an RSS/Atom <link rel="alternate"> advertised by the page."""
    base_netloc = host_of(base_url)
    for link in soup.find_all("link", rel=lambda v: v and "alternate" in v):
        ftype = (link.get("type") or "").lower()
        href = link.get("href")
        if not href or ("rss" not in ftype and "atom" not in ftype and "xml" not in ftype):
            continue
        feed = absolutize(base_url, href)
        if host_of(feed) == base_netloc:
            return feed
    return None


def _discover_feed_url_from(page_text: str, base_url: str) -> str | None:
    """Same as _discover_feed_url but takes raw HTML (off-loop friendly)."""
    return _discover_feed_url(soup_from(page_text), base_url)


_FEED_PROBE_RE = re.compile(r"<(?:rss|feed|rdf)\b", re.I)


def _looks_like_feed(text: str) -> bool:
    """True when the fetched document is itself an RSS/Atom/RDF feed.

    Sites sometimes keep feeds under non-obvious paths (e.g.
    cbc.ca/webfeed/rss/rss-topstories), so a source registered as a generic
    page can receive XML. Parsing that as HTML yields junk, so detect it
    and hand the document to the feed parser instead."""
    head = text.lstrip()[:600]
    if head.startswith("<?xml"):
        return True
    return bool(_FEED_PROBE_RE.search(head))


def _find_next_page(selector: str | None, html: str, current_url: str) -> str | None:
    soup = soup_from(html)
    node = soup.select_one(selector) if selector else soup.find("link", rel=lambda v: v and "next" in v)
    if node is None:
        for a in soup.find_all("a", rel=True):
            rels = a.get("rel") or []
            if any(r.lower() == "next" for r in rels) and a.get("href"):
                node = a
                break
    if node is None or not node.get("href"):
        return None
    href = node["href"]
    if href == current_url:
        return None
    return absolutize(current_url, href)


async def _fetch_feed(ctx, feed_url: str) -> tuple[list, str | None]:
    """Fetch and parse a discovered feed (delegates to the rss parser)."""
    from crawler.plugins.rss import _parse_feed
    text = await ctx.get_text(feed_url, conditional=True)
    if text is None:
        return [], None
    return await asyncio.to_thread(_parse_feed, text)


@register
class WebPlugin(Plugin):
    """Generic scraper for blog/news listing pages."""
    name = "web"
    label = "Web page (generic scraper)"
    patterns = (r"^https?://",)   # fallback for any http(s) URL
    priority = 0

    def default_config(self) -> dict:
        return {"max_items": 300, "fetch_content": True, "content_fetch_limit": 24,
                "max_pages": 3, "stop_on_known": True}

    async def fetch(self, ctx: FetchContext) -> FetchResult:
        items: list[dict] = []
        source_name = None
        url = ctx.source["url"]

        # a feed discovered on an earlier refresh: parse it directly
        feed_url = ctx.config.get("feed_url")
        if feed_url:
            items, source_name = await self._items_from_feed(ctx, feed_url, url)

        if not items:
            pages = 0
            next_url: str | None = url
            last_text: str | None = None
            max_pages = int(ctx.config.get("max_pages", 3))
            stop_on_known = bool(ctx.config.get("stop_on_known", True))
            while next_url and pages < max_pages:
                text = await ctx.get_text(next_url, conditional=(pages == 0))
                if text is None:
                    break
                last_text = text
                if pages == 0 and _looks_like_feed(text):
                    # the "page" is actually a feed document: parse it as one
                    from crawler.plugins.rss import _parse_feed
                    parsed, source_name = await asyncio.to_thread(_parse_feed, text)
                    if parsed:
                        limit = int(ctx.config.get("max_items", 300))
                        return FetchResult(
                            items=[ParsedItem(**i.to_row()) for i in parsed[:limit]],
                            source_name=source_name)
                    break   # XML but not a usable feed: fall through to scraping
                # lxml parsing holds the GIL in long C chunks: parse in a
                # worker process so the event loop never stalls
                page_items = await run_parse(extract_listing, text, next_url)
                seen_guids = {i["guid"] for i in items}
                items.extend(i for i in page_items if i["guid"] not in seen_guids)
                pages += 1
                # listings are newest-first: a page holding items the DB
                # already has means the rest of the pagination is older still
                hit_known = any(i["guid"] in ctx.known_dates for i in page_items)
                if (stop_on_known and hit_known) or len(page_items) < 5:
                    next_url = None
                else:
                    next_url = await run_parse(_find_next_page,
                                               ctx.config.get("next_page_selector"),
                                               text, next_url)
            if not items and last_text is not None:
                # maybe the URL is a single article page
                article = await run_parse(extract_article, last_text, url)
                if article.get("title") and (article.get("published_at") or article.get("content")):
                    items = [article]

            # many sites are WordPress/etc. with a feed the homepage advertises:
            # prefer it over heuristic scraping when the listing looks thin
            if last_text is not None and (len(items) < 3 or not items):
                feed_items, feed_name = await self._try_discovered_feed(ctx, last_text, url)
                if feed_items:
                    items = feed_items
                    source_name = feed_name

        limit = int(ctx.config.get("max_items", 300))
        items = items[:limit]

        if ctx.config.get("fetch_content", True):
            await self._enrich_from_pages(ctx, items)

        return FetchResult(items=[ParsedItem(**i) for i in items], source_name=source_name)

    async def _items_from_feed(self, ctx, feed_url: str, page_url: str) -> tuple[list[dict], str | None]:
        from crawler.plugins.rss import _parse_feed
        text = await ctx.get_text(feed_url, conditional=True)
        if text is None:
            return [], None
        parsed, name = await asyncio.to_thread(_parse_feed, text)
        limit = int(ctx.config.get("max_items", 300))
        return [i.to_row() for i in parsed[:limit]], name or ctx.source.get("name") or None

    async def _try_discovered_feed(self, ctx, page_text: str, page_url: str):
        """If the page advertises a same-host feed, try to use it. On success
        the feed URL is persisted in the source config so later refreshes skip
        the discovery round-trip."""
        feed_url = await run_parse(_discover_feed_url_from, page_text, page_url)
        if not feed_url or feed_url == page_url:
            return [], None
        parsed, name = await _fetch_feed(ctx, feed_url)
        if not parsed:
            return [], None
        return [i.to_row() for i in parsed], name

    async def _enrich_from_pages(self, ctx: FetchContext, items: list[dict]) -> None:
        """Fetch article pages for new items and for items still missing a
        date in the DB (backfill): content/date/image land in the DB via
        the upsert. Items the DB already dates are left alone, so the
        per-refresh budget reaches the undated stragglers."""
        limit = int(ctx.config.get("content_fetch_limit", 24))

        def needs(i: dict) -> bool:
            stored = ctx.known_dates.get(i["guid"])
            return (i["guid"] not in ctx.known_dates
                    or (stored is None and not i.get("published_at")))

        need = [i for i in items if needs(i)][:limit]
        if not need:
            return
        sem = asyncio.Semaphore(6)

        async def one(item: dict) -> None:
            async with sem:
                try:
                    text = await ctx.get_text(item["url"])
                    if text is None:
                        return
                    article = await run_parse(extract_article, text, item["url"])
                except Exception as exc:  # noqa: BLE001 - enrichment is best effort
                    ctx.logger.debug("enrich failed for %s: %s", item["url"], exc)
                    return
                if article.get("content") and len(article["content"]) > len(item.get("summary", "")):
                    item["content"] = article["content"]
                if not item.get("summary") and article.get("summary"):
                    item["summary"] = article["summary"][:500]
                if not item.get("published_at") and article.get("published_at"):
                    item["published_at"] = article["published_at"]
                if not item.get("image_url") and article.get("image_url"):
                    item["image_url"] = article["image_url"]
                if not item.get("author") and article.get("author"):
                    item["author"] = article["author"]

        await asyncio.gather(*(one(i) for i in need), return_exceptions=True)
