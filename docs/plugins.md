# Writing a source plugin

A plugin turns one kind of URL into items. To add one, create a single file in
`crawler/plugins/`, for example `crawler/plugins/myplugin.py`:

```python
import httpx
from crawler import register
from crawler.base import FetchContext, FetchResult, ParsedItem


@register
class MyPlugin:
    name = "myplugin"                      # stored on the source row
    label = "My news thing"
    patterns = (r"example\.com/news",)     # regexes matched against the URL
    priority = 10                          # higher wins when several match

    def default_config(self) -> dict:
        return {"max_items": 100}

    async def fetch(self, ctx: FetchContext) -> FetchResult:
        text = await ctx.get_text(ctx.source["url"])      # httpx client + UA handled
        items = [ParsedItem(guid=url, url=url, title=title, published_at=ts)
                 for url, title, ts in parse(text)]
        return FetchResult(items=items[:100], source_name="Example News")
```

That is all. The module is imported at startup, `POST /api/sources` picks the
best plugin for a URL by its patterns, and refreshes run through the shared
parallel fetcher.

What the plugin gets from `FetchContext`:

* `ctx.known_dates`: a map of the guids already stored for this source, to their
  stored date (`None` if undated). Use it to skip work on items you already have.
* `ctx.backlog`: stored items that still need enrichment (no date or no body),
  oldest first. A plugin that fetches article pages works through this, so an
  item that has scrolled off the listing still gets filled in.
* `ctx.config`: the plugin's defaults merged with the source's own options.

A plugin returns items. It does not write to the database; the fetcher stores
them.

Existing plugins to read for examples: `rss.py`, `web.py`, `youtube.py`,
`arxiv.py`, `reddit.py`.
