# Sources

## Seeding the default sources

`scripts/seed_sources.py` adds the project's seven sources, in five categories,
to the database. Run it once on a new install:

```bash
uv run python scripts/seed_sources.py          # or: python scripts/seed_sources.py
```

`--fresh` deletes **all sources, categories and their articles** before seeding.
It does not delete the database file. Do not use it on a library you want to keep.

Check that every source parses on its own:

```bash
python scripts/check_sources.py                              # all sources
python scripts/check_sources.py --url https://example.com/feed   # one URL
```

## Default sources

| Source | Plugin |
|---|---|
| https://www.aisi.gov.uk/blog | generic web scraper |
| https://www.aisi.gov.uk/research | generic web scraper |
| https://metr.org/research/ | generic web scraper |
| https://cohere.com/blog | generic web scraper |
| https://www.youtube.com/playlist?list=PL9HYL-VRX0oQOXEh8rBFtLNsJxwC_Ta7A | youtube |
| https://www.theguardian.com/technology/artificialintelligenceai/rss | rss |
| https://arxiv.org/search/?query=%22machine+translation%22&searchtype=all&abstracts=show&order=-announced_date_first&size=50 | arxiv |

Notes on the trickier ones:

* **AISI, METR, Cohere** have no usable RSS for these pages. The generic web
  scraper clusters repeated card structures (Webflow CMS cards, Tailwind article
  cards), extracts title, summary, date, image and tags heuristically, and fetches
  each new article's page for full text, og:image and bylines.
* **YouTube playlists** are parsed with yt-dlp. The flat listing is fast. The
  upload date and description of each *new* video are fetched with a bounded
  parallel extraction, once per video.
* **arXiv search URLs** are parsed from the result pages, following pagination up
  to `max_results`, with arXiv's requested 3 s delay between requests.

## Per-source options

Options live in the source's `config` and are merged with the plugin defaults.
Examples:

* Web sources: `{"max_items": 500, "fetch_content": true, "content_fetch_limit": 12}`
* YouTube: `{"fetch_video_dates": true, "date_fetch_limit": 60}`
* arXiv: `{"max_results": 200}`
* Paginated scrapers stop following pages once a page reaches items already in the
  database. `"stop_on_known": false` turns this off.
* YouTube stops its per-video date fetches after `"date_fetch_max_errors"`
  failures in one refresh. Bot-check and members-only failures are remembered in
  `date_fetch_skip` for 3 days.

Two cursors are stored on the source row. Do not edit them by hand:

* `date_fetch_cursor` (YouTube) and `backfill_cursor` (web scraper) record where
  the next refresh starts in the backlog. A refresh that gives up early, because
  YouTube is bot-checking or a page does not exist, still moves on to the next
  items rather than retrying the same ones forever.

## Reddit

Reddit sources get a dialog (for adding and editing) with:

* subreddit, sort and time window;
* a required **minimum score**: posts below it are never added;
* `external_only`, which keeps only link posts.

The dialog builds the listing URL itself. `top` defaults to the past week, even
when a bare URL is pasted.

Reddit is read through its public `.json` listings with no API keys. When Reddit
gates logged-out access with its JS verification page, the plugin solves it and
retries automatically.
