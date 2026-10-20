# Features

## Sources

* **Plugin architecture.** RSS/Atom, YouTube playlists and channels, arXiv
  searches, Reddit subreddits, and a generic blog/news listing scraper ship in
  the box. New source types are single-file plugins that are discovered
  automatically (see [Plugins](plugins.md)).
* **Parallel refresh.** Every refresh runs all enabled sources concurrently
  (bounded semaphore, 24 parallel fetches by default), with ETag and
  Last-Modified conditional requests so unchanged feeds are nearly free.
  Parsing happens in a small process pool, so the API stays responsive while
  large pages are being parsed.
* **Failure notifications.** When a source fails to update, the web client
  shows a persistent notification naming the source. The HTTP status, URL and a
  response excerpt are included, collapsed behind a "Details" toggle. The
  sidebar marks failing sources with a red ⚠ badge (hover for the failure count
  and last error). Enabled sources with no successful refresh in 3+ days get an
  amber ⚠. A refresh summary toast counts the failures.
* **Reliable dates.** Dates are parsed from feeds, listing cards (`<time>`,
  meta tags, text patterns) and article pages. Items still missing a date are
  backfilled by scraping the article page or video info, bounded per refresh.
  The backlog is worked through in turn, so an item that has scrolled off a
  listing is still finished off, and one article page that cannot be fetched
  never holds up the others (it is tried three times, then left alone).
  Dates in the future are treated as missing (feeds whose CMS mislabels local
  time as UTC), so they never make an article look "just now".
  Anything still undated falls back to the date it was first seen, and undated
  items sort after dated ones in both sort orders.

## Reading

* **Reader-first UI.** Three panes: sidebar, article list, reading pane. Opening
  an article never loses your place in the list. Below ~1180 px the reader slides
  in over the list; below ~900 px the sidebar becomes off-canvas.
* **Appearance.** Dark and light theme, comfortable and compact list density,
  infinite scroll, search-term highlighting.
* **Navigation.** Prefetching of the next and previous article makes navigation
  instant. Sources nest under their category in the sidebar (expand and collapse
  with the chevron; feeds without a category sit under "Uncategorized"). The
  list/reader divider can be dragged to resize (double-click resets it).
* **Hover actions.** Open, copy, star and dismiss appear over the article
  thumbnail. Long tag names truncate, with the full name on hover.
* **Video.** YouTube items, and YouTube links inside article bodies, render as
  click-to-load facades: a thumbnail and play button, with a
  youtube-nocookie iframe appearing only on tap.
* **Reddit.** Reddit items always link to their thread. Image, gallery and video
  posts embed their media in the detail view and carry a thumbnail and the post
  score on the list cards.

## Organising

* **Full-text search.** FTS5 index over title, summary and article content, with
  prefix matching (`translat` finds "translation"), quoted phrases, multi-token
  AND and BM25 relevance ranking.
* **Tags.** Every item can be tagged, and tags are filterable in the UI. Some
  sources tag items automatically (arXiv categories, feed categories, card pills).
* **Star and dismiss.** Star articles to find them later (sidebar filter).
  Dismiss hides an article from every list, search and count. Dismissal is
  undoable.
* **Hide sources.** An eye toggle in the sidebar hides a source's articles from
  the feed, searches and counts. Clicking the source itself still shows its items.
* **Categories.** Add and remove categories and assign sources to them. Items
  inherit their category through their source.
* **Date scoping.** "Today / 7 days / 30 days" chips narrow any view, and compose
  with search, tags and sources. "All time" lifts the window again.
* **Read it later.** The bookmark button in the sidebar footer saves any article
  by URL. It lands unread and starred in "Read it later" by default. Pick another
  category in the dialog, and untick either box to change the defaults. Saved
  articles are ordinary items.

## Unread tracking

* Read state is stored on the server (`items.read_at`), so it follows the
  database, not the browser.
* Unread badges per source and category, an Unread view, per-row read toggles,
  and bulk "Mark read" over the current filter.
* Articles that existed before unread tracking count as read, so an upgrade never
  presents a backlog.

## Triage

* Star, dismiss and read are one tap. On touch screens, list rows swipe left to
  dismiss and right to star.
* Refreshes never move the list out from under you. Fresh arrivals show as an
  "N new" pill that prepends them in place.

## Keyboard

| Key | Action |
|---|---|
| `j` / `k` (or `↑` / `↓` / `←` / `→`) | walk the list |
| `Enter` or `o` | open the original |
| `s` | star |
| `u` | toggle read |
| `d` | dismiss (undoable) |
| `c` | copy the link |
| `b` | show or hide the sidebar |
| `/` | search |
| `r` | refresh |
| `t` | toggle theme |
| `?` | shortcut sheet |

On touch screens, the article walk is a left/right swipe on the open article.
Edge swipes stay with the browser's back gesture. Filters and the open article
are reflected in the URL hash, so views can be shared and back/forward works.

## Sharing sources

Import and export source lists as OPML. See [Sharing sources](sharing.md).
