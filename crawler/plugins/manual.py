"""Manually saved articles ("read it later").

Articles added through the web UI are stored under a hidden-plumbing source
per category, using this plugin. The plugin never matches a URL (so it can't
steal sources from the real plugins) and has nothing to refresh: its items
are written by the save endpoint, not fetched.
"""
from crawler import register
from crawler.base import FetchContext, FetchResult, Plugin


@register
class ManualPlugin(Plugin):
    name = "manual"
    label = "Saved articles"
    patterns = ()          # never auto-selected for a URL

    async def fetch(self, ctx: FetchContext) -> FetchResult:
        return FetchResult()
