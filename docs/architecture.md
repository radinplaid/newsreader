# Architecture

```
web/            static HTML5 client (vanilla JS/CSS, works from any static host)
api/            FastAPI app: JSON API under /api, serves web/ at /
newsreader/     core package: config.py (settings) · fetcher.py (parallel refresh
                orchestrator, semaphore-bounded, per-source isolation) ·
                opml.py (OPML 2.0 import/export)
crawler/
  base.py       Plugin, FetchContext, FetchResult, ParsedItem
  pool.py       process pool for CPU-bound parsing (GIL-free, thread fallback)
  __init__.py   plugin registry and auto-discovery
  textutil.py   date parsing, cleaning, image extraction helpers
  plugins/      rss.py · web.py · youtube.py · arxiv.py · reddit.py
models/
  database.py   SQLite schema and repository (WAL, FTS5, tags, categories)
scripts/        seed_sources.py · check_sources.py
tests/          pytest suite (offline by default; live tests with -m live)
```

The server is one process: the FastAPI app, a background refresh task, and the
parsing pool. The web client talks to it over `/api` and listens to the
server-sent events stream at `/api/events` for refresh progress.

Refreshes go through `newsreader/fetcher.py`, which runs every enabled source
concurrently under a semaphore and records each source's result separately.
Plugins never touch the database directly: they return `FetchResult` objects, and
the fetcher stores them.

To add a source type, see [Writing a source plugin](plugins.md).
