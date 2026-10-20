# API

The server exposes a JSON API under `/api` and serves the web client at `/`.
Interactive docs are available from FastAPI at `/docs`.

Browser access is limited to this server's own origin by default. See
[Security and deployment](security.md).

```
GET    /api/health                     counts (incl. unread_items)
GET    /api/items?q&category_id&source_id&tag&starred&unread&since&until&ids
                                        &sort=new|old|rank&limit&offset
                                        since/until: epoch seconds or ISO date
                                        (a date-only until covers that whole day);
                                        ids: comma-separated explicit set
GET    /api/items/{id}
PUT    /api/items/{id}/tags            {"tags": [...]}   replace
POST   /api/items/{id}/tags            {"tags": [...]}   add
DELETE /api/items/{id}/tags/{tag}
POST   /api/items/{id}/star            star an article
DELETE /api/items/{id}/star            unstar
POST   /api/items/{id}/read            mark read (stamps read_at)
DELETE /api/items/{id}/read            mark unread
POST   /api/items/read-all             {"read": true, "ids"?|filter?} bulk mark
POST   /api/items/{id}/dismiss         hide the article everywhere
DELETE /api/items/{id}/dismiss         undo a dismissal
GET    /api/tags
GET    /api/sources                    (also lists available plugins)
POST   /api/sources                    {"url", "name"?, "category_id"?, "category_name"?}
PATCH  /api/sources/{id}               {"name"?, "category_id"?, "enabled"?, "hidden"?, "config"?}
DELETE /api/sources/{id}
GET    /api/sources/export             portable JSON of all sources (no articles)
POST   /api/sources/import             {"sources": [...]} from an export document
GET    /api/sources/export.opml        OPML 2.0 of all sources (feed-reader compatible)
POST   /api/sources/import.opml        raw OPML body; same match-by-URL semantics
GET    /api/categories
POST   /api/categories                 {"name"}
PATCH  /api/categories/{id}            {"name"}
DELETE /api/categories/{id}
POST   /api/refresh                    {"source_id"?}  — starts a background run
GET    /api/refresh/status             progress + per-source results
                                        (each result carries inserted_ids)
GET    /api/events                     server-sent events: refresh start/progress/
                                       completion + per-source failures (SSE)
```
