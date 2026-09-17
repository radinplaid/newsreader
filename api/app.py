"""FastAPI application: JSON API + static hosting for the web client."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

import crawler
from crawler.base import FetchError
from fastapi import FastAPI, HTTPException, Query, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config import settings
from fetcher import Fetcher, RefreshBusy
from models.database import Database

log = logging.getLogger("newsreader.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    crawler.discover()
    db = Database()
    await db.init()
    app.state.db = db
    app.state.fetcher = Fetcher(app.state.db)
    app.state.auto_task = None
    if settings.nr_refresh_interval_min > 0:
        app.state.auto_task = asyncio.create_task(_auto_refresh(app))
    log.info("newsreader ready: %s plugins registered", len(crawler.all_plugins()))
    yield
    if app.state.auto_task:
        app.state.auto_task.cancel()
    await app.state.db.close()


async def _auto_refresh(app: FastAPI) -> None:
    while True:
        try:
            await asyncio.sleep(settings.nr_refresh_interval_min * 60)
            fetcher: Fetcher = app.state.fetcher
            if not fetcher.running:
                await fetcher.run_blocking()
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("auto refresh failed")


app = FastAPI(title="Newsreader API", version="1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# ----------------------------------------------------------------- models
class SourceIn(BaseModel):
    url: str
    name: str = ""
    category_id: int | None = None
    category_name: str | None = None
    config: dict = Field(default_factory=dict)


class SourcePatch(BaseModel):
    name: str | None = None
    category_id: int | None = None
    enabled: bool | None = None
    hidden: bool | None = None
    config: dict | None = None
    url: str | None = None


class CategoryIn(BaseModel):
    name: str


class TagsIn(BaseModel):
    tags: list[str]


class RefreshIn(BaseModel):
    source_id: int | None = None
    force: bool = False  # bypass the NR_MIN_REFRESH_MINUTES throttle


class ImportSource(BaseModel):
    url: str
    name: str = ""
    plugin: str = ""
    category: str | None = None
    enabled: bool = True
    hidden: bool = False
    config: dict = Field(default_factory=dict)


class ImportIn(BaseModel):
    sources: list[ImportSource]


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_fetcher(request: Request) -> Fetcher:
    return request.app.state.fetcher


async def _source_row(db: Database, source_id: int) -> dict:
    src = await db.get_source(source_id)
    if src is None:
        raise HTTPException(404, "source not found")
    return src


# ----------------------------------------------------------------- health
@app.get("/api/health")
async def health(db: Database = Depends(get_db)):
    return {"ok": True, "counts": await db.counts()}


# ----------------------------------------------------------------- items
@app.get("/api/items")
async def list_items(q: str | None = None, source_id: int | None = None,
               category_id: int | None = None, tag: str | None = None,
               starred: bool = False,
               sort: str = Query("new", pattern="^(new|old|rank)$"),
               limit: int = Query(50, ge=1, le=200),
               offset: int = Query(0, ge=0),
               db: Database = Depends(get_db)):
    items, total = await db.list_items(
        q=q, source_id=source_id, category_id=category_id, tag=tag,
        starred=starred, sort=sort, limit=limit, offset=offset,
    )
    return {"items": items, "total": total, "offset": offset, "limit": limit}


@app.get("/api/items/{item_id}")
async def get_item(item_id: int, db: Database = Depends(get_db)):
    item = await db.get_item(item_id)
    if item is None:
        raise HTTPException(404, "item not found")
    return item


@app.post("/api/items/{item_id}/tags")
async def add_tags(item_id: int, body: TagsIn, db: Database = Depends(get_db)):
    if await db.get_item(item_id) is None:
        raise HTTPException(404, "item not found")
    return {"tags": await db.add_item_tags(item_id, body.tags)}


@app.put("/api/items/{item_id}/tags")
async def set_tags(item_id: int, body: TagsIn, db: Database = Depends(get_db)):
    if await db.get_item(item_id) is None:
        raise HTTPException(404, "item not found")
    return {"tags": await db.set_item_tags(item_id, body.tags)}


@app.delete("/api/items/{item_id}/tags/{tag}")
async def remove_tag(item_id: int, tag: str, db: Database = Depends(get_db)):
    if await db.get_item(item_id) is None:
        raise HTTPException(404, "item not found")
    return {"tags": await db.remove_item_tag(item_id, tag)}


@app.post("/api/items/{item_id}/star")
async def star_item(item_id: int, db: Database = Depends(get_db)):
    if not await db.set_starred(item_id, True):
        raise HTTPException(404, "item not found")
    return {"starred": True}


@app.delete("/api/items/{item_id}/star")
async def unstar_item(item_id: int, db: Database = Depends(get_db)):
    if not await db.set_starred(item_id, False):
        raise HTTPException(404, "item not found")
    return {"starred": False}


@app.post("/api/items/{item_id}/dismiss")
async def dismiss_item(item_id: int, db: Database = Depends(get_db)):
    if not await db.set_dismissed(item_id, True):
        raise HTTPException(404, "item not found")
    return {"dismissed": True}


@app.get("/api/tags")
async def list_tags(db: Database = Depends(get_db)):
    return {"tags": await db.list_tags()}


# ----------------------------------------------------------------- sources
@app.get("/api/sources")
async def list_sources(db: Database = Depends(get_db)):
    return {"sources": await db.list_sources(),
            "plugins": [{"name": p.name, "label": p.label} for p in crawler.all_plugins()]}


# ----------------------------------------------------------------- export/import
@app.get("/api/sources/export")
async def export_sources(db: Database = Depends(get_db)):
    """All source definitions (no articles) as a portable JSON document."""
    sources = await db.list_sources()
    return {
        "format": "newsreader-sources",
        "version": 1,
        "exported_at": time.time(),
        "sources": [
            {
                "url": s["url"],
                "name": s["name"],
                "plugin": s["plugin"],
                "category": s["category_name"],
                "enabled": s["enabled"],
                "hidden": s["hidden"],
                "config": s["config"],
            }
            for s in sources
        ],
    }


@app.post("/api/sources/import")
async def import_sources(body: ImportIn, db: Database = Depends(get_db)):
    """Import sources from an export document. Existing URLs are updated."""
    cat_cache: dict[str, int] = {}
    created = updated = skipped = 0
    all_sources = await db.list_sources()
    by_url = {s["url"]: s for s in all_sources}
    for imp in body.sources:
        try:
            url = _normalize_url(imp.url)
        except HTTPException:
            skipped += 1
            continue
        category_id = None
        if imp.category:
            category_id = cat_cache.get(imp.category.lower())
            if category_id is None:
                cat = await db.create_category(imp.category)
                category_id = cat["id"]
                cat_cache[imp.category.lower()] = category_id
        existing = by_url.get(url)
        if existing:
            await db.update_source(existing["id"], name=imp.name, category_id=category_id,
                                   enabled=imp.enabled, hidden=imp.hidden, config=imp.config)
            updated += 1
            continue
        plugin = crawler.find_plugin(url)
        if plugin is None and imp.plugin:
            plugin = crawler.plugin_by_name(imp.plugin)
        if plugin is None:
            skipped += 1
            continue
        await db.create_source(url=url, name=imp.name, plugin=plugin.name,
                               category_id=category_id, config=imp.config)
        created += 1
    return {"ok": True, "created": created, "updated": updated, "skipped": skipped}


@app.get("/api/sources/{source_id}")
async def get_source(source_id: int, db: Database = Depends(get_db)):
    return await _source_row(db, source_id)




def _normalize_url(url: str) -> str:
    """Validate/coerce a user-supplied source URL. Raises 422 when unusable."""
    from urllib.parse import urlparse
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or "." not in parsed.netloc:
        raise HTTPException(422, "source URL must be an http(s) URL")
    return url


@app.post("/api/sources")
async def create_source(body: SourceIn, db: Database = Depends(get_db)):
    url = _normalize_url(body.url)
    plugin = crawler.find_plugin(url)
    if plugin is None:
        raise HTTPException(422, "no plugin can handle this URL")
    category_id = body.category_id
    if category_id is None and body.category_name:
        cat = await db.create_category(body.category_name)
        category_id = cat["id"]
    source_id = await db.create_source(
        url=url, name=body.name, plugin=plugin.name,
        category_id=category_id, config=body.config,
    )
    return await _source_row(db, source_id)


@app.patch("/api/sources/{source_id}")
async def patch_source(source_id: int, body: SourcePatch, db: Database = Depends(get_db)):
    await _source_row(db, source_id)
    fields = {k: v for k, v in body.model_dump(exclude_unset=True).items()
              if k != "category_id" or v is not None}
    if fields.get("url"):
        fields["url"] = _normalize_url(fields["url"])
        plugin = crawler.find_plugin(fields["url"])
        if plugin is None:
            raise HTTPException(422, "no plugin can handle this URL")
        # the resource changed: re-resolve the plugin, drop stale conditional state
        fields["plugin"] = plugin.name
        fields["etag"] = ""
        fields["last_modified"] = ""
        fields["last_status"] = ""
        fields["last_error"] = ""
    await db.update_source(source_id, **fields)
    return await _source_row(db, source_id)


@app.delete("/api/sources/{source_id}")
async def delete_source(source_id: int, db: Database = Depends(get_db)):
    if not await db.delete_source(source_id):
        raise HTTPException(404, "source not found")
    return {"ok": True}


# ----------------------------------------------------------------- categories
@app.get("/api/categories")
async def list_categories(db: Database = Depends(get_db)):
    return {"categories": await db.list_categories()}


@app.post("/api/categories")
async def create_category(body: CategoryIn, db: Database = Depends(get_db)):
    try:
        return await db.create_category(body.name)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


@app.patch("/api/categories/{category_id}")
async def rename_category(category_id: int, body: CategoryIn, db: Database = Depends(get_db)):
    if not await db.update_category(category_id, body.name):
        raise HTTPException(404, "category not found")
    return {"ok": True}


@app.delete("/api/categories/{category_id}")
async def delete_category(category_id: int, db: Database = Depends(get_db)):
    if not await db.delete_category(category_id):
        raise HTTPException(404, "category not found")
    return {"ok": True}


# ----------------------------------------------------------------- refresh
@app.post("/api/refresh")
async def refresh(body: RefreshIn, db: Database = Depends(get_db), fetcher: Fetcher = Depends(get_fetcher)):
    if body.source_id is not None:
        await _source_row(db, body.source_id)
    try:
        fetcher.start([body.source_id] if body.source_id else None,
                                force=body.force)
    except RefreshBusy as exc:
        return JSONResponse({"error": str(exc), "status": fetcher.snapshot()},
                            status_code=409)
    return {"ok": True, "status": fetcher.snapshot()}


@app.get("/api/refresh/status")
async def refresh_status(fetcher: Fetcher = Depends(get_fetcher)):
    return fetcher.snapshot()


# ----------------------------------------------------------------- static
if os.path.isdir(settings.nr_web_dir):
    app.mount("/", StaticFiles(directory=settings.nr_web_dir, html=True), name="web")
else:
    @app.get("/")
    async def no_web():
        return {"error": f"web dir not found: {settings.nr_web_dir}"}
