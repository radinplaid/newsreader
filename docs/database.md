# Database and upgrades

The library is one SQLite file (`NR_DB_PATH`, default `data/newsreader.db`) in WAL
mode, so readers do not block the writer. Full-text search is an FTS5
external-content index kept in sync by triggers.

Data model: `categories → sources → items`, plus `tags` and `item_tags` (many to
many). Deleting a source or item cascades cleanly.

## Upgrading

Upgrading needs nothing from you beyond restarting the server on the new code.
Keep a copy of `data/newsreader.db` before a major upgrade.

On start-up the server:

* adds the columns that later versions introduced, if they are missing, before
  creating the indexes that use them;
* creates indexes, or recreates those whose shape changed;
* runs the one-time data cleanup, once. The schema version is stored in the
  database (`PRAGMA user_version`), so this work is not repeated on every start.

Articles, tags, and read and starred state are left alone. Articles from before
unread tracking existed count as read, so an upgrade never presents a backlog of
unread articles. Databases from the very first release open as well.

The first start after an upgrade that adds or changes indexes builds them, which
takes longer on a large library. Later starts do not rebuild them.
