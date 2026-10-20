# Performance

## Many sources

* One `asyncio` task per source, capped by a shared semaphore. A failing or slow
  source never blocks or breaks the others. Per-source error state is recorded,
  shown in the UI and surfaced as a dismissible notification.
* Conditional GETs make steady-state refreshes cheap.
* Sources that fail repeatedly back off exponentially (30 minutes, doubling up to
  a day). `force` bypasses the backoff.

## The server stays responsive

* HTML and XML parsing runs in a small process pool (`crawler/pool.py`), so it
  does not hold the GIL. While a 1.5 s parse of a 0.7 MB page was in flight, the
  event loop's p95 latency measured 3 ms.
* Pure-Python feed and yt-dlp work runs in worker threads.
* SQLite has one dedicated writer and a pool of reader connections, so API reads
  do not queue behind refresh writes.

## Large libraries

The plain feed, unread and starred views are read through their partial indexes.
They are named with `INDEXED BY` in `Database._forced_index`, because the query
planner did not choose them on its own and sorted the whole library instead.

Measured on 100,000 items across 100 sources:

| Request | Time |
|---|---|
| Feed, one page of 50 (includes the total count) | about 7 ms |
| Unread view | about 8 ms |
| Starred view | under 1 ms |
| One source | under 1 ms (planner's choice; forcing the feed index made it 10x slower) |
| Search | about 10 ms |
| `counts()` | about 9 ms |

Without the hint, the feed page took about 36 ms before the count.

Trade-off: `INDEXED BY` turns a missing index into an error rather than a slow
query. `init()` always creates these indexes, so this only matters if they are
removed from the schema.

The total count is `COUNT(*)` over the filtered set. The FTS5 index is
external-content, so article text is not stored twice.
