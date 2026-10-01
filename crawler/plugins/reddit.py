"""Reddit sources: subreddit listings (.../r/<sub>/top|hot|new|rising|controversial).

Reddit serves any listing page as JSON by appending ``.json`` (public,
unauthenticated), so no API keys or third-party libraries are needed. Logged-out
requests from some networks are gated behind a JS verification page (a hidden
form whose "solution" is the seed string doubled); it is solved automatically
and the request retried, exactly what a browser does. Posts are filtered by a
per-source score threshold ("min_score": only posts with at least that many
upvotes become items); "external_only" keeps just link posts pointing off
Reddit. Every item links to its reddit thread; link posts put the outbound
article link at the top of the item body.
Post media rides along: image and gallery posts embed their pictures and
hosted videos an HTML5 player, each with a thumbnail for the feed cards.
The listing and top-window come from the source URL (`/r/ottawa/top/?t=week`)
and can be overridden with "listing" / "time"; `top` defaults to the past
week when the URL carries no time window.
"""
from __future__ import annotations

import asyncio
import html
import json
import re
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from crawler import register
from crawler.base import FetchError, FetchContext, FetchResult, ParsedItem, Plugin

LISTINGS = ("top", "hot", "new", "rising", "controversial")
#: listed by score descending: once a page drops under min_score, later
#: pages cannot recover and pagination can stop
SCORE_ORDERED = ("top", "controversial")
#: listed by recency: pagination runs into items the database already has
DATE_ORDERED = ("new", "hot", "rising")
TIME_FILTERS = ("hour", "day", "week", "month", "year", "all")

_PATH_RE = re.compile(
    r"^/r/(?P<sub>[A-Za-z0-9_+]+)(?:/(?P<listing>top|hot|new|rising|controversial))?/?$",
    re.I)
_REDDIT_HOST_RE = re.compile(
    r"^https?://(?:[\w-]+\.)?(?:reddit\.com|redd\.it)/", re.I)
_FORM_RE = re.compile(r"<form[^>]*>(.*?)</form>", re.I | re.S)
_ACTION_RE = re.compile(r'action="([^"]*)"', re.I)
_INPUT_RE = re.compile(r"<input\b[^>]*>", re.I)
_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')
_SEED_RE = re.compile(r'(\w+)\s*\+\s*\1\)\s*\(\s*"([0-9a-f]+)"')
_IMG_EXT_RE = re.compile(r"\.(?:jpe?g|png|gif|webp)(?:\?|$)", re.I)


def parse_target(url: str) -> tuple[str, str, str]:
    """Split a subreddit listing URL into (subreddit, listing, time filter)."""
    parts = urlparse(url)
    m = _PATH_RE.match(parts.path)
    if not m:
        raise FetchError(
            f"reddit plugin needs a subreddit listing URL like "
            f"https://www.reddit.com/r/ottawa/top/?t=week (got {url})")
    listing = (m.group("listing") or "hot").lower()
    t = (parse_qs(parts.query).get("t") or [""])[0].lower()
    return m.group("sub"), listing, t


def is_challenge_page(text: str) -> bool:
    return "<form" in text and "js_challenge" in text and "jsc_token" in text


def challenge_solution(text: str, page_url: str) -> str | None:
    """URL that answers reddit's logged-out JS verification (solution = seed+seed)."""
    seed_m = _SEED_RE.search(text)
    form_m = _FORM_RE.search(text)
    if not (seed_m and form_m):
        return None
    inputs: dict[str, str] = {}
    for tag_m in _INPUT_RE.finditer(form_m.group(1)):
        attrs = dict(_ATTR_RE.findall(tag_m.group(0)))
        if "name" in attrs:
            inputs[attrs["name"]] = attrs.get("value", "")
    inputs["solution"] = seed_m.group(2) * 2
    action_m = _ACTION_RE.search(text)
    target = urljoin(page_url, action_m.group(1) if action_m else urlparse(page_url).path)
    return str(httpx.URL(target).copy_with(
        params={**dict(httpx.URL(page_url).params), **inputs}))


def _preview_image(data: dict) -> str:
    for img in (data.get("preview") or {}).get("images") or []:
        src = (img.get("source") or {}).get("url") or ""
        if src.startswith("http"):
            return src.strip()
    return ""


def _media(data: dict, thread_url: str = "") -> tuple[str, str, bool]:
    """(thumbnail, media HTML for the reader, is_video) for one post."""
    def esc(u: str) -> str:
        return html.escape(u, quote=True)

    if data.get("is_gallery") and data.get("media_metadata"):
        meta = data["media_metadata"]
        order = [it.get("media_id") for it in
                 (data.get("gallery_data") or {}).get("items") or []]
        ids = [i for i in order if i in meta] or list(meta)
        thumb, parts = "", []
        for mid in ids[:8]:
            src = (meta.get(mid) or {}).get("s") or {}
            url = (src.get("u") or src.get("gif") or src.get("mp4") or "").strip()
            if not url.startswith("http"):
                continue
            thumb = thumb or url
            parts.append(f'<figure class="md-media"><img src="{esc(url)}" alt="" loading="lazy"></figure>')
        return thumb, "".join(parts), False

    rv = ((data.get("secure_media") or data.get("media") or {}).get("reddit_video") or {})
    fallback = (rv.get("fallback_url") or "").split("?")[0]
    if fallback.startswith("http"):
        thumb = _preview_image(data) or \
            (data.get("thumbnail") or "").strip()
        if not thumb.startswith("http"):
            thumb = ""
        poster = f' poster="{esc(thumb)}"' if thumb else ""
        player = (f'<video class="md-media" controls preload="metadata"{poster}>'
                  f'<source src="{esc(fallback)}" type="video/mp4"></video>')
        if rv.get("has_audio") and thread_url:
            player += (f'<p class="media-note"><a href="{esc(thread_url)}">'
                       f'Watch with sound on Reddit ↗</a></p>')
        return thumb, player, True

    url = (data.get("url") or "").strip()
    if _IMG_EXT_RE.search(url) or url.startswith("https://i.redd.it/"):
        return url, f'<figure class="md-media"><img src="{esc(url)}" alt="" loading="lazy"></figure>', False

    thumb = _preview_image(data)
    if not thumb:
        cand = (data.get("thumbnail") or "").strip()
        thumb = cand if cand.startswith("http") else ""
    return thumb, "", False


def parse_post(data: dict) -> dict | None:
    pid = data.get("name") or data.get("id")
    if not pid:
        return None
    permalink = data.get("permalink") or ""
    thread_url = f"https://www.reddit.com{permalink}" if permalink else ""
    outbound = (data.get("url") or "").strip()
    external = bool(outbound) and not data.get("is_self") and \
        not _REDDIT_HOST_RE.match(outbound)
    flair = (data.get("link_flair_text") or "").strip()
    score = int(data.get("score") or data.get("ups") or 0)
    thumb, media_html, is_video = _media(data, thread_url)
    link_html = ""
    if external:
        domain = (data.get("domain") or "").strip() or urlparse(outbound).netloc
        link_html = (f'<p class="md-link"><a href="{html.escape(outbound, quote=True)}"'
                     f' title="{html.escape(outbound, quote=True)}">'
                     f'{html.escape(domain or "linked article")} ↗</a></p>')
    body = link_html + media_html + (data.get("selftext_html") or "").strip()
    extra = {
        "kind": "reddit",
        "subreddit": data.get("subreddit") or "",
        "score": score,
        "num_comments": int(data.get("num_comments") or 0),
        "permalink": thread_url,
        "domain": data.get("domain") or "",
        "external": external,
    }
    if is_video:
        extra["video"] = True
    if external:
        extra["link_url"] = outbound
    return {
        "guid": str(pid),
        "url": thread_url or outbound,
        "title": (data.get("title") or "").strip(),
        "summary": (data.get("selftext") or "").strip()[:500],
        "content": body,
        "author": (data.get("author") or "").strip(),
        "published_at": float(data["created_utc"]) if data.get("created_utc") else None,
        "image_url": thumb,
        "tags": [flair] if flair else [],
        "score": score,
        "extra": extra,
    }


def parse_listing(payload: dict | str) -> list[dict]:
    if isinstance(payload, (str, bytes)):
        payload = json.loads(payload)
    out = []
    for child in (payload.get("data") or {}).get("children") or []:
        if (child.get("kind") or "t3") != "t3":
            continue
        post = parse_post(child.get("data") or {})
        if post and post["title"]:
            out.append(post)
    return out


@register
class RedditPlugin(Plugin):
    name = "reddit"
    label = "Reddit subreddit"
    patterns = (r"https?://(?:[\w-]+\.)?reddit\.com/r/",)
    priority = 25

    def default_config(self) -> dict:
        return {"min_score": 0, "max_items": 100, "external_only": False,
                "listing": "", "time": "", "page_delay": 2.0, "stop_on_known": True}

    async def fetch(self, ctx: FetchContext) -> FetchResult:
        sub, listing, t = parse_target(ctx.source["url"])
        listing = str(ctx.config.get("listing") or listing).lower()
        t = str(ctx.config.get("time") or t).lower()
        if listing not in LISTINGS:
            raise FetchError(f"unknown reddit listing {listing!r} (use one of {LISTINGS})")
        if t and t not in TIME_FILTERS:
            raise FetchError(f"unknown reddit time filter {t!r} (use one of {TIME_FILTERS})")
        if listing in SCORE_ORDERED and not t:
            t = "week"
        min_score = int(ctx.config.get("min_score", 0))
        max_items = int(ctx.config.get("max_items", 100))
        external_only = bool(ctx.config.get("external_only", False))
        stop_on_known = bool(ctx.config.get("stop_on_known", True))
        delay = float(ctx.config.get("page_delay", 2.0))

        items: list[dict] = []
        seen: set[str] = set()
        after = ""
        while True:
            page_url = self._listing_url(sub, listing, t, after)
            text = await self._load_json(ctx, page_url, conditional=(not after))
            if text is None:
                break
            try:
                payload = json.loads(text)
            except ValueError as exc:
                raise FetchError(f"reddit returned non-JSON for {page_url}: {exc}")
            posts = [p for p in parse_listing(payload) if p["guid"] not in seen]
            for p in posts:
                seen.add(p["guid"])
            items.extend(p for p in posts
                         if p["score"] >= min_score
                         and (not external_only or p["extra"]["external"]))
            if len(items) >= max_items:
                break
            if not posts:
                break
            if listing in SCORE_ORDERED and min_score and posts[-1]["score"] < min_score:
                break
            if (stop_on_known and listing in DATE_ORDERED
                    and any(p["guid"] in ctx.known_dates for p in posts)):
                break
            after = (payload.get("data") or {}).get("after") or ""
            if not after:
                break
            await asyncio.sleep(delay)

        items = items[:max_items]
        return FetchResult(
            items=[ParsedItem(**{k: v for k, v in i.items()
                                if k in ParsedItem.__dataclass_fields__})
                   for i in items],
            source_name=self._source_name(sub, listing, t))

    @staticmethod
    def _listing_url(sub: str, listing: str, t: str, after: str) -> str:
        qs = ["limit=100", "raw_json=1"]
        if t:
            qs.append(f"t={t}")
        if after:
            qs.append(f"after={after}")
        return f"https://www.reddit.com/r/{sub}/{listing}.json?" + "&".join(qs)

    async def _load_json(self, ctx: FetchContext, json_url: str,
                         conditional: bool = False) -> str | None:
        page_url = json_url.replace(".json?", "/?").replace(".json", "/")
        try:
            text = await ctx.get_text(json_url, conditional=conditional)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in (401, 403):
                raise
            text = ""
        if text is None:
            return None
        if text and not is_challenge_page(text):
            return text
        if not await self._solve_challenge(ctx, page_url):
            raise FetchError(
                "reddit rejected this request (401/403) and served no "
                "verification page to solve: it is blocking unauthenticated "
                "requests from this network")
        return await ctx.get_text(json_url)

    async def _solve_challenge(self, ctx: FetchContext, page_url: str) -> bool:
        ctx.requests += 1
        try:
            resp = await ctx.client.get(page_url)
        except httpx.HTTPError:
            return False
        body = resp.text
        if resp.status_code >= 400 or not is_challenge_page(body):
            return False
        solve_url = challenge_solution(body, str(resp.url))
        if not solve_url:
            return False
        ctx.requests += 1
        try:
            solved = await ctx.client.get(solve_url)
        except httpx.HTTPError:
            return False
        return solved.status_code < 400

    @staticmethod
    def _source_name(sub: str, listing: str, t: str) -> str:
        name = f"r/{sub} · {listing}"
        return f"{name} · {t}" if t and listing in SCORE_ORDERED else name
