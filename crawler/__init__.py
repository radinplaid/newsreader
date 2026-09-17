"""Crawler plugin registry.

Plugins live in crawler/plugins/*.py and register themselves with the
@register decorator. Any module dropped into that package that defines a
crawler.base.Plugin subclass and decorates it with @register is discovered
automatically at startup - that is the plug-in architecture extension point.
"""
from __future__ import annotations

import importlib
import pkgutil

from .base import FetchContext, FetchResult, FetchError, ParsedItem, Plugin

_REGISTRY: list[Plugin] = []
_DISCOVERED = False


def register(cls: type[Plugin]) -> type[Plugin]:
    """Class decorator: add a plugin to the registry."""
    _REGISTRY.append(cls())
    return cls


def discover(force: bool = False) -> None:
    global _DISCOVERED
    if _DISCOVERED and not force:
        return
    import crawler.plugins as pkg
    for mod in pkgutil.iter_modules(pkg.__path__):
        importlib.import_module(f"crawler.plugins.{mod.name}")
    _DISCOVERED = True


def all_plugins() -> list[Plugin]:
    discover()
    return list(_REGISTRY)


def find_plugin(url: str) -> Plugin | None:
    """Pick the highest-priority plugin whose URL patterns match."""
    best: Plugin | None = None
    for plugin in all_plugins():
        if plugin.can_handle(url) and (best is None or plugin.priority > best.priority):
            best = plugin
    return best


def plugin_by_name(name: str) -> Plugin | None:
    discover()
    for plugin in _REGISTRY:
        if plugin.name == name:
            return plugin
    return None
