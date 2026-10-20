# newsreader

A self-hosted news aggregator and reader. A Python server (FastAPI and SQLite)
serves a plain HTML5 web client with no build step.

## Features

* **Many kinds of source:** RSS and Atom, YouTube playlists, arXiv searches,
  Reddit, and generic blog or news pages. New source types are single-file plugins.
* **Fast refresh:** every source updates in parallel, and unchanged feeds cost
  almost nothing.
* **Reliable dates,** with failing sources flagged in the sidebar.
* **Reader-first UI:** a three-pane layout, dark and light themes, keyboard
  shortcuts, and it works on phones.
* **Organise:** full-text search, tags, categories, star, dismiss, hide sources,
  read tracking, and "read it later".
* **Share:** import and export source lists as OPML.
* **Yours:** your library is one SQLite file on your own machine.

Details are in [docs/features.md](docs/features.md).

## Install and run

You need Python 3.12 or newer.

With [uv](https://docs.astral.sh/uv/) (recommended):

```bash
uv sync
uv run python scripts/seed_sources.py    # first run only: adds the default sources
uv run python -m api                     # then open http://127.0.0.1:8000
```

With pip:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
python scripts/seed_sources.py           # first run only
python -m api
```

Open the page, press **Refresh all**, and read.

Upgrading: restart the server on the new code. The database is upgraded on
start-up. Keep a copy of `data/newsreader.db` before a major upgrade. See
[docs/database.md](docs/database.md).

## Configuration

The defaults suit a single-user install. The settings you are most likely to change:

| Variable | Default | Meaning |
|---|---|---|
| `NR_DB_PATH` | `./data/newsreader.db` | database file |
| `NR_HOST` / `NR_PORT` | `127.0.0.1` / `8000` | address to listen on |
| `NR_ALLOWED_ORIGINS` | (empty) | other sites allowed to use the API, for a client hosted elsewhere |
| `NR_REFRESH_INTERVAL_MIN` | `0` (off) | refresh automatically every N minutes |

The full list is in [docs/configuration.md](docs/configuration.md).

## Documentation

* [Features](docs/features.md)
* [Sources](docs/sources.md): the default sources, options and Reddit
* [Sharing sources](docs/sharing.md): OPML import and export
* [Writing a source plugin](docs/plugins.md)
* [API](docs/api.md)
* [Security and deployment](docs/security.md)
* [Configuration](docs/configuration.md)
* [Database and upgrades](docs/database.md)
* [Performance](docs/performance.md)
* [Architecture](docs/architecture.md)
* [Testing](docs/testing.md)
* [Changelog](docs/CHANGELOG.md)
