# Configuration

Settings are environment variables. The defaults suit a single-user install.
Per-source options are stored on each source (see [Sources](sources.md#per-source-options)).

| Variable | Default | Meaning |
|---|---|---|
| `NR_DB_PATH` | `./data/newsreader.db` | SQLite database file |
| `NR_WEB_DIR` | `./web` | static client directory |
| `NR_HOST` / `NR_PORT` | `127.0.0.1` / `8000` | bind address; also `--host` / `--port` on `python -m api` |
| `NR_LOG_LEVEL` | `info` | uvicorn log level; also `--log-level` |
| `NR_MAX_CONCURRENCY` | `24` | parallel source fetches per refresh |
| `NR_REQUEST_TIMEOUT` | `30` | per-request timeout (seconds) |
| `NR_DB_READERS` | `4` | pooled SQLite read connections (writes stay serialised) |
| `NR_ALLOWED_ORIGINS` | (empty) | comma-separated origins allowed to call the API from a browser; empty means this server's own origin only. See [Security](security.md). |
| `NR_PARSE_WORKERS` | `min(4, cores)` | worker processes for HTML/XML parsing (`0` parses in threads) |
| `NR_REFRESH_INTERVAL_MIN` | `0` (off) | background auto-refresh interval, in minutes |
| `NR_MIN_REFRESH_MINUTES` | `15` | skip sources refreshed more recently than this (refresh-all and auto-refresh; `0` disables it; a per-source refresh with `"force": true` bypasses it) |
