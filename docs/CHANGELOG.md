# Changelog

Earlier releases are not recorded here; this log starts with the unreleased work.

## Unreleased

### Added
* `NR_ALLOWED_ORIGINS`: origins other than this server's own that may call the API
  from a browser. Set it for a web client hosted elsewhere. See [Security](security.md).
* Writes from another website are refused with `403`. Requests without an
  `Origin` header (curl, scripts) are unaffected.
* Enrichment attempts are limited to three per item. An article page that cannot
  be fetched no longer holds up the others.

### Changed
* The web scraper fills in stored items that have scrolled off the listing, oldest
  first. New listing items still get their share of each refresh's budget.
* The YouTube date backlog rotates across refreshes, so videos behind a run that
  gives up early still get their turn.
* Upserting an item merges its stored `extra` fields rather than replacing them.
* The article list's feed, unread and starred views are read through their
  partial indexes. Feed pages at 100,000 items dropped from about 40 ms to about 7 ms.
* `counts()` is one query, not eight.
* Startup no longer rewrites every stored URL or scans for future dates on every
  boot; those run once per database, tracked by `PRAGMA user_version`.

### Fixed
* Databases from before `first_seen_at` and `read_at` existed failed to open. The
  columns are now added before the indexes that use them.
* A stored value that is not valid JSON no longer breaks the whole list. It reads
  as empty.
* A successful article fetch clears the failure count, so the item gets its full
  set of attempts again if it is ever undated.
* The YouTube rotation advances only past videos it actually tried.
