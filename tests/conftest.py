"""Shared fixtures for the API tests."""
import importlib
import time

import pytest_asyncio
from fastapi.testclient import TestClient

import api.app as app_module
from newsreader.fetcher import Fetcher


@pytest_asyncio.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("NR_DB_PATH", str(tmp_path / "api.db"))
    from newsreader.config import settings
    settings.nr_db_path = str(tmp_path / "api.db")
    monkeypatch.setenv("NR_WEB_DIR", str(tmp_path / "noweb"))
    importlib.reload(app_module)

    # mock the per-source refresh so POST /api/refresh never hits the network
    async def fake_refresh_one(self, client_httpx, src, sem):
        await self.db.upsert_items(src["id"], [{
            "guid": "fake-1", "url": src["url"], "title": "Fake item",
            "summary": "s", "published_at": time.time(),
        }])
        await self.db.update_source(src["id"], name="Example Feed", last_status="ok",
                              last_error="", last_refreshed_at=time.time())
        return {"source_id": src["id"], "source": src["name"] or src["url"],
                "url": src["url"], "plugin": "mock", "inserted": 1, "updated": 0,
                "status": "ok", "error": "", "requests": 0, "duration": 0.0}

    monkeypatch.setattr(Fetcher, "_refresh_one", fake_refresh_one)

    with TestClient(app_module.app) as tc:
        yield tc
