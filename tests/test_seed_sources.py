"""Tests for scripts/seed_sources.py (run against a temporary database)."""
import asyncio
import importlib.util
import sys
from pathlib import Path

from models.database import Database

ROOT = Path(__file__).resolve().parents[1]


def _load_seed():
    spec = importlib.util.spec_from_file_location("seed_sources", ROOT / "scripts" / "seed_sources.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _expected_count(seed):
    return sum(len(v) for v in seed.SEED.values()), len(seed.SEED)


def _run_seed(seed, monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["seed_sources.py", *args])
    asyncio.run(seed.main())


def _counts(db_path):
    async def go():
        db = Database(db_path)
        await db.init()
        try:
            return len(await db.list_sources()), len(await db.list_categories())
        finally:
            await db.close()
    return asyncio.run(go())


def test_seed_creates_all_sources_and_categories(tmp_path, monkeypatch, capsys):
    seed = _load_seed()
    db_path = str(tmp_path / "seed.db")

    _run_seed(seed, monkeypatch, "--db", db_path)

    sources, categories = _expected_count(seed)
    out = capsys.readouterr().out
    assert f"sources: {sources} categories: {categories}" in out
    assert "!! no plugin" not in out
    assert _counts(db_path) == (sources, categories)


def test_seed_fresh_replaces_rather_than_duplicates(tmp_path, monkeypatch):
    seed = _load_seed()
    db_path = str(tmp_path / "seed.db")

    _run_seed(seed, monkeypatch, "--db", db_path, "--fresh")
    _run_seed(seed, monkeypatch, "--db", db_path, "--fresh")  # must not double the rows

    sources, categories = _expected_count(seed)
    assert _counts(db_path) == (sources, categories)
