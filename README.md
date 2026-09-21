# newsreader

A self-hosted news aggregator and reader. Python server (FastAPI + SQLite),
serverless HTML5 web client (plain static files — no build step, no framework).

## Features

* **Plug-in architecture for sources** — RSS/Atom, YouTube playlists/channels,
  arXiv searches, and a generic blog/news listing scraper ship in the box.
  New source types are single-file plugins that are discovered automatically
  (see [Adding a plugin](#adding-a-plugin)).
* **Many sources, parallel downloads** — every refresh runs all enabled
  sources concurrently (bounded semaphore, default 24 parallel fetches),
  with ETag/Last-Modified conditional requests so unchanged feeds are nearly free.
  Parsing happens in a small process pool, so the API stays responsive
  even while large pages are being parsed.
* **Failure notifications** — when a source fails to update, the web client
  shows a persistent notification (no timeout; dismissed manually) naming the
  source. The HTTP status, URL and a response excerpt are included for
  debugging, collapsed behind a "Details" toggle by default. The sidebar
  marks failing sources with a ⚠ badge, and a refresh summary toast counts
  the failures.
* **Reliable dates** — dates are parsed from feeds, listing cards (`<time>`,
  meta tags, text patterns) and article pages. Items still missing a date are
  backfilled by scraping the article page / video info, bounded per refresh
  and prioritized until the backlog is resolved. Anything that still has no
  date falls back to the date the item was first seen, and undated items sort
  after dated ones in both sort orders.
* **Many items, fast** — SQLite (WAL) with indexes handles 10,000s of items
  comfortably; the listing endpoint is paginated and index-driven.
* **Full text search** — FTS5 index over title, summary and article content,
  with prefix matching, phrase support and BM25 relevance ranking.
* **Tagging** — every item can be tagged; tags are filterable in the UI.
  Some sources auto-tag items (arXiv categories, feed categories, card pills).
* **Star & dismiss** — star articles to find them later (sidebar filter), and
  dismiss articles so they disappear from every list, search and count.
* **Hide sources** — keep fetching a source but tuck its articles away: an eye
  toggle in the sidebar hides a source from the feed, searches and counts
  (clicking the source itself still shows its items).
* **Categories of sources** — add/remove categories, assign sources to them;
  items inherit their category through their source.
* **Responsive web viewer** — dark/light theme, card grid on desktop, single
  column on mobile, off-canvas sidebar, infinite scroll, detail drawer with
  an inline tag editor.

## Quickstart

With [uv](https://docs.astral.sh/uv/) (recommended):

```bash
uv sync                # creates .venv, installs the app + dev deps (pytest)

# create the database and add the default sources (all seven project sources)
uv run python scripts/seed_sources.py --fresh

# start the server (serves the API and the web client)
uv run python -m api            # or just: uv run newsreader
# → http://127.0.0.1:8000
```

With plain pip:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .                # or: pip install -r requirements.txt
pip install pytest              # only needed to run the test suite
```

then run the same seed/serve commands with plain `python`.

Open http://127.0.0.1:8000, press **Refresh all**, and read.

To verify that every source parses correctly on its own:

```bash
python scripts/check_sources.py            # all sources in the db
python scripts/check_sources.py --url https://example.com/feed   # one URL
```

## Sources (seeded by `scripts/seed_sources.py`)

| Source | Plugin |
|---|---|
| https://www.aisi.gov.uk/blog | generic web scraper |
| https://www.aisi.gov.uk/research | generic web scraper |
| https://metr.org/research/ | generic web scraper |
| https://cohere.com/blog | generic web scraper |
| https://www.youtube.com/playlist?list=PL9HYL-VRX0oQOXEh8rBFtLNsJxwC_Ta7A | youtube |
| https://www.theguardian.com/technology/artificialintelligenceai/rss | rss |
| https://arxiv.org/search/?query=%22machine+translation%22&searchtype=all&abstracts=show&order=-announced_date_first&size=50 | arxiv |

Notes on the tricky ones:

* **AISI / METR / Cohere** have no usable RSS for these pages, so the generic
  web scraper clusters repeated card structures (Webflow CMS cards, Tailwind
  article cards), then extracts title/summary/date/image/tags heuristically and
  fetches each new article's page for full text, og:image and bylines.
* **YouTube playlists** are parsed with yt-dlp. The flat listing is fast; the
  upload date and description of each *new* video are fetched with a bounded
  parallel full extraction (only once per video).
* **arXiv search URLs** are parsed from the result pages, following pagination
  up to `max_results` with arXiv's requested 3 s delay between requests.

## Adding a plugin

Create `crawler/plugins/myplugin.py`:

```python
import httpx
from crawler import register
from crawler.base import FetchContext, FetchResult, ParsedItem


@register
class MyPlugin:
    name = "myplugin"                      # stored on the source row
    label = "My news thing"
    patterns = (r"example\.com/news",)     # regexes matched against the URL
    priority = 10                          # higher wins when several match

    def default_config(self) -> dict:
        return {"max_items": 100}

    async def fetch(self, ctx: FetchContext) -> FetchResult:
        text = await ctx.get_text(ctx.source["url"])      # httpx client + UA handled
        items = [ParsedItem(guid=url, url=url, title=title, published_at=ts)
                 for url, title, ts in parse(text)]
        return FetchResult(items=items[:100], source_name="Example News")
```

That is all — the module is imported at startup, `POST /api/sources` picks the
best plugin by URL, and refreshes run through the shared parallel fetcher.
`ctx.known_guids` lets you skip work for items already stored.

## Architecture

```
web/            static HTML5 client (vanilla JS/CSS, works from any static host)
api/            FastAPI app: JSON API under /api, serves web/ at /
fetcher.py      parallel refresh orchestrator (semaphore-bounded, per-source isolation)
crawler/
  base.py       Plugin, FetchContext, FetchResult, ParsedItem
  pool.py       process pool for CPU-bound parsing (GIL-free, thread fallback)
  __init__.py   plugin registry + auto-discovery
  textutil.py   date parsing, cleaning, image extraction helpers
  plugins/      rss.py · web.py · youtube.py · arxiv.py (drop more files here)
models/
  database.py   SQLite schema + repository (WAL, FTS5, tags, categories)
scripts/        seed_sources.py · check_sources.py
tests/          pytest suite (offline by default, live tests with -m live)
```

Data model: `categories → sources → items`, plus `tags`/`item_tags`
(many-to-many) and an FTS5 external-content index `items_fts` kept in sync by
triggers. Deleting a source or item cascades cleanly.

## API

```
GET    /api/health                     counts
GET    /api/items?q&category_id&source_id&tag&starred&sort=new|old|rank&limit&offset
GET    /api/items/{id}
PUT    /api/items/{id}/tags            {"tags": [...]}   replace
POST   /api/items/{id}/tags            {"tags": [...]}   add
DELETE /api/items/{id}/tags/{tag}
POST   /api/items/{id}/star            star an article
DELETE /api/items/{id}/star            unstar
POST   /api/items/{id}/dismiss         hide the article everywhere
GET    /api/tags
GET    /api/sources                    (also lists available plugins)
POST   /api/sources                    {"url", "name"?, "category_id"?, "category_name"?}
PATCH  /api/sources/{id}               {"name"?, "category_id"?, "enabled"?, "hidden"?, "config"?}
DELETE /api/sources/{id}
GET    /api/sources/export             portable JSON of all sources (no articles)
POST   /api/sources/import             {"sources": [...]} from an export document
GET    /api/categories
POST   /api/categories                 {"name"}
PATCH  /api/categories/{id}            {"name"}
DELETE /api/categories/{id}
POST   /api/refresh                    {"source_id"?}  — starts a background run
GET    /api/refresh/status             progress + per-source results
GET    /api/events                     server-sent events: refresh start/progress/
                                       completion + per-source failures (SSE)
```

CORS is open, so the `web/` folder can be hosted anywhere (GitHub Pages, S3, …)
and pointed at a running server — the client is purely static.

## Sharing sources

The sidebar footer has **Export** / **Import** buttons. Export downloads a
JSON document with every source's url, name, plugin, category, enabled/hidden
state and per-source config — no articles. Send the file to someone else and
they can import it: sources are matched by URL, so re-importing updates
existing entries in place rather than duplicating them. The same round-trip
works over the API via `GET /api/sources/export` and
`POST /api/sources/import`.

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `NR_DB_PATH` | `./data/newsreader.db` | SQLite database file |
| `NR_WEB_DIR` | `./web` | static client directory |
| `NR_HOST` / `NR_PORT` | `127.0.0.1` / `8000` | bind address (`python -m api`, or override with `--host` / `--port`) |
| `NR_MAX_CONCURRENCY` | `24` | parallel source fetches per refresh |
| `NR_REQUEST_TIMEOUT` | `30` | per-request timeout (s) |
| `NR_DB_READERS` | `4` | pooled SQLite read connections (writes stay serialized) |
| `NR_PARSE_WORKERS` | `min(4, cores)` | worker processes for HTML/XML parsing (`0` = parse in threads) |
| `NR_REFRESH_INTERVAL_MIN` | `0` (off) | background auto-refresh interval |
| `NR_MIN_REFRESH_MINUTES` | `15` | skip sources refreshed more recently than this (refresh-all / auto-refresh; `0` disables, per-source refresh with `"force": true` bypasses) |

Per-source knobs live in `source.config` (merged with plugin defaults), e.g.
`{"max_items": 500, "fetch_content": true, "content_fetch_limit": 12}` for web
sources, `{"fetch_video_dates": true, "date_fetch_limit": 60}` for YouTube,
`{"max_results": 200}` for arXiv.

## Performance notes

* 1000+ sources: one `asyncio` task per source, capped by a shared semaphore;
  a failing/slow source never blocks or breaks others (per-source error state
  is recorded, shown in the UI and surfaced as a dismissible notification).
  Conditional GETs make steady-state refreshes cheap.
* The server never freezes during refreshes: lxml/BeautifulSoup parsing runs
  in a small process pool (`crawler/pool.py`, GIL-free — the event loop
  measured p95 3 ms while a 1.5 s parse of a 0.7 MB page was in flight),
  pure-Python feed/yt-dlp work uses worker threads, and the SQLite layer
  keeps a dedicated writer plus a pool of reader connections so API reads
  don't queue behind refresh writes (WAL).
* 10,000+ items: pagination with covering indexes, `COUNT(*)` only over the
  filtered set, FTS5 external-content index (no duplicated blobs), WAL mode for
  concurrent readers.
* Full-text search supports prefixes (`translat` → translation), quoted
  phrases (`"machine translation"`), and multi-token AND with BM25 ranking.

## Tests

```bash
python -m pytest            # offline unit tests (db, parsers, API)
python -m pytest -m live    # live tests against the real sources
```
