# Sharing sources

The sidebar footer's **transfer** button opens the source manager: a filterable
list of every source, showing its category, plugin, status (paused, failing,
last successful refresh) and article count. Each row can be refreshed, paused,
hidden from the feed, or edited. The **+** button in the footer opens the add
dialog.

Import and export live in the collapsed **Import & export** section at the bottom
of the same dialog.

* **Export sources** downloads OPML with source URLs, names, types, categories,
  enabled and hidden state, and configuration.
* **Import sources…** accepts OPML/XML or older JSON exports, detected
  automatically. Confirm inside the dialog before importing. The result shows how
  many sources were added, updated or skipped.

## How matching works

* Sources match by normalised URL.
* A matching source updates only the settings supplied in the file. Omitted
  settings stay unchanged, so a basic OPML file does not erase scraper options or
  disabled and hidden state.
* Supplied configuration replaces the whole configuration dictionary.
* Articles and read/starred state are not included and not changed. This is
  source transfer, not a database backup.

## Compatibility

* OPML custom attributes keep all five Newsreader source types and their
  settings between Newsreader installations. Categories also appear as folder
  outlines.
* Other readers may discard the custom metadata or not support YouTube, Reddit,
  arXiv or web sources.
* Export files may contain private information (source URLs and settings). Share
  them with care.

## API

* `GET /api/sources/export.opml` and `POST /api/sources/import.opml` (raw XML body).
* The JSON endpoints `GET /api/sources/export` and `POST /api/sources/import`
  remain supported.
