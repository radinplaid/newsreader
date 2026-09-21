# TODO.md

- [x] Loads of items have an "unknown" date (46% overall) — fixed. Census showed the
  backlog was dominated by YouTube sources (~1300 items) and a few web scrapes:
  date enrichment only ran for brand-new items (YouTube: first 60 per refresh ever;
  web: first 12), so older items never got dates, and "Oldest first" showed undated
  items on top. Now enrichment targets known-but-undated items (YouTube 100/refresh,
  web 24/refresh) until the backlog resolves; the API falls back to the first-seen
  date (`COALESCE(published_at, first_seen_at)`), so no item shows "date unknown";
  undated items sort after dated ones in both orders, ordered by added date.
  Verified live: Weaviate 12/12 stragglers healed in one backlog pass, LanceDB 24/93
  per pass, YouTube dates 6 distinct videos per pass with real upload dates.
- [x] App is unresponsive when sources are being updated — fixed: refresh already
  ran as a background task, but lxml parsing held the GIL on the event loop and
  all API reads queued behind refresh writes on one SQLite connection. HTML/XML
  parsing now runs in a process pool (`crawler/pool.py`, `NR_PARSE_WORKERS`),
  and reads use a connection pool (`NR_DB_READERS`) beside a dedicated writer.
  Measured p95 3 ms API latency during a 1.5 s parse of a 0.7 MB page (was ~450 ms stalls).
- [x] No proper notification when fetching a source fails — fixed: failed sources
  raise a persistent notification in the web client (manual dismiss, no timeout);
  the HTTP status, URL and response excerpt are shown for debugging, collapsed
  behind a "Details" toggle by default. A ⚠ badge in the sidebar marks failing
  sources; errors are deduplicated per source + error text.
