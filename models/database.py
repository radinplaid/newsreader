"""SQLite data layer for newsreader.

One module holds the schema, connection management and all repository
functions (sources, categories, items, tags). SQLite runs in WAL mode so
readers never block writers; a process-wide write lock serializes write
transactions. Full text search is an FTS5 external-content index over
items(title, summary, content) kept in sync with triggers.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from contextlib import asynccontextmanager
from typing import Iterable

import aiosqlite

from config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    id                INTEGER PRIMARY KEY,
    url               TEXT NOT NULL UNIQUE,
    name              TEXT NOT NULL DEFAULT '',
    plugin            TEXT NOT NULL DEFAULT '',
    category_id       INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    enabled           INTEGER NOT NULL DEFAULT 1,
    hidden            INTEGER NOT NULL DEFAULT 0,
    config            TEXT NOT NULL DEFAULT '{}',
    etag              TEXT NOT NULL DEFAULT '',
    last_modified     TEXT NOT NULL DEFAULT '',
    last_refreshed_at REAL,
    last_status       TEXT NOT NULL DEFAULT '',
    last_error        TEXT NOT NULL DEFAULT '',
    created_at        REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id           INTEGER PRIMARY KEY,
    source_id    INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    guid         TEXT NOT NULL,
    url          TEXT NOT NULL DEFAULT '',
    title        TEXT NOT NULL DEFAULT '',
    summary      TEXT NOT NULL DEFAULT '',
    content      TEXT NOT NULL DEFAULT '',
    author       TEXT NOT NULL DEFAULT '',
    published_at REAL,
    image_url    TEXT NOT NULL DEFAULT '',
    extra        TEXT NOT NULL DEFAULT '{}',
    fetched_at   REAL NOT NULL,
    first_seen_at REAL,
    starred      INTEGER NOT NULL DEFAULT 0,
    dismissed    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (source_id, guid)
);
CREATE INDEX IF NOT EXISTS idx_items_source_pub ON items (source_id, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_items_pub ON items (published_at DESC);

CREATE TABLE IF NOT EXISTS tags (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS item_tags (
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    tag_id  INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (item_id, tag_id)
);
CREATE INDEX IF NOT EXISTS idx_item_tags_tag ON item_tags (tag_id);

CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    title, summary, content,
    content='items', content_rowid='id',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS items_fts_ai AFTER INSERT ON items BEGIN
    INSERT INTO items_fts (rowid, title, summary, content)
    VALUES (new.id, new.title, new.summary, new.content);
END;
CREATE TRIGGER IF NOT EXISTS items_fts_ad AFTER DELETE ON items BEGIN
    INSERT INTO items_fts (items_fts, rowid, title, summary, content)
    VALUES ('delete', old.id, old.title, old.summary, old.content);
END;
CREATE TRIGGER IF NOT EXISTS items_fts_au AFTER UPDATE OF title, summary, content ON items BEGIN
    INSERT INTO items_fts (items_fts, rowid, title, summary, content)
    VALUES ('delete', old.id, old.title, old.summary, old.content);
    INSERT INTO items_fts (rowid, title, summary, content)
    VALUES (new.id, new.title, new.summary, new.content);
END;
"""

_fts_token = re.compile(r'"[^"]*"|\w+')

# A parsed date more than this far in the future is untrustworthy (feeds from
# CMSes with broken timezones, scheduled posts): treat it as missing so the
# first-seen fallback applies and date enrichment can look for a real date.
_FUTURE_DATE_TOLERANCE = 300  # seconds of allowed clock skew


def fts_query(q: str) -> str | None:
    """Build a forgiving FTS5 MATCH expression from user input.

    Tokens (words or quoted phrases) are ANDed together with prefix
    matching, so 'machine transl' finds 'machine translation'.
    """
    q = (q or "").strip()
    if not q:
        return None
    tokens = _fts_token.findall(q)
    if not tokens:
        return None
    parts = []
    for tok in tokens[:12]:
        if tok.startswith('"'):
            parts.append(f'{tok}*')      # prefix inside the phrase: "foo bar"*
        else:
            parts.append(f'"{tok}"*')
    return " AND ".join(parts)


_ITEM_FROM = """
    FROM items i
    JOIN sources s ON s.id = i.source_id
    LEFT JOIN categories c ON c.id = s.category_id
"""
_ITEM_TAGS = ("(SELECT json_group_array(t.name) FROM"
              " (SELECT t2.name FROM item_tags jt JOIN tags t2 ON t2.id = jt.tag_id"
              "  WHERE jt.item_id = i.id ORDER BY t2.name) t) AS tags")
_ITEM_COLS = f"""
    i.id, i.guid, i.title, i.summary, i.url, i.image_url, i.author,
    COALESCE(i.published_at, i.first_seen_at) AS published_at,
    i.fetched_at, i.starred,
    i.source_id, s.name AS source_name, s.url AS source_url,
    c.id AS category_id, c.name AS category_name,
    {_ITEM_TAGS}
"""


class Database:
    """Repository over aiosqlite.

    Reads go through a small pool of connections so API requests never queue
    behind refresh writes on a single worker thread (WAL lets readers and the
    writer work concurrently); all writes share one dedicated connection so
    write transactions stay serialized.
    """

    def __init__(self, path: str | None = None):
        self.path = os.path.abspath(path or settings.nr_db_path)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._writer: aiosqlite.Connection | None = None
        self._readers: list[aiosqlite.Connection] = []
        self._next_reader = 0

    async def init(self):
        """Initialize the schema synchronously at startup if needed, then setup async."""
        conn = sqlite3.connect(self.path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        self._migrate(conn)
        conn.commit()
        conn.close()
        # build the pools up front so requests never race a lazy init
        self._readers = [await self._open_conn()
                         for _ in range(max(1, settings.nr_db_readers))]
        self._writer = await self._open_conn()

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Add columns introduced after the initial schema, heal bad data."""
        cols = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
        if "starred" not in cols:
            conn.execute("ALTER TABLE items ADD COLUMN starred INTEGER NOT NULL DEFAULT 0")
        if "dismissed" not in cols:
            conn.execute("ALTER TABLE items ADD COLUMN dismissed INTEGER NOT NULL DEFAULT 0")
        if "first_seen_at" not in cols:
            conn.execute("ALTER TABLE items ADD COLUMN first_seen_at REAL")
            # existing rows: last fetch is the best available "added" date
            conn.execute("UPDATE items SET first_seen_at = fetched_at "
                         "WHERE first_seen_at IS NULL")
        # items stored with a future date (feeds with broken CMS timezones):
        # never trustworthy, so drop them; first_seen takes over as fallback
        conn.execute("UPDATE items SET published_at = NULL "
                     "WHERE published_at > strftime('%s','now') + ?",
                     (_FUTURE_DATE_TOLERANCE,))
        scols = {r[1] for r in conn.execute("PRAGMA table_info(sources)")}
        if "hidden" not in scols:
            conn.execute("ALTER TABLE sources ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")

    # -- connections ------------------------------------------------------
    async def _open_conn(self) -> aiosqlite.Connection:
        conn = await aiosqlite.connect(self.path, timeout=30)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.execute("PRAGMA busy_timeout=30000")
        return conn

    async def get_conn(self) -> aiosqlite.Connection:
        """A pooled read connection (round-robin over nr_db_readers)."""
        if not self._readers:
            self._readers = [await self._open_conn()]
        conn = self._readers[self._next_reader % len(self._readers)]
        self._next_reader += 1
        return conn

    async def close(self):
        conns = [*self._readers, self._writer]
        self._readers = []
        self._writer = None
        for conn in conns:
            if conn:
                await conn.close()

    @asynccontextmanager
    async def write(self):
        """Transaction context manager over the dedicated write connection."""
        if self._writer is None:
            self._writer = await self._open_conn()
        try:
            yield self._writer
            await self._writer.commit()
        except Exception:
            await self._writer.rollback()
            raise

    # -- categories -------------------------------------------------------
    async def list_categories(self) -> list[dict]:
        conn = await self.get_conn()
        async with conn.execute(
            """
            SELECT c.id, c.name,
                   (SELECT COUNT(*) FROM sources s WHERE s.category_id = c.id) AS source_count,
                   (SELECT COUNT(*) FROM items i JOIN sources s2 ON s2.id = i.source_id
                     WHERE s2.category_id = c.id AND i.dismissed = 0 AND s2.hidden = 0) AS item_count
            FROM categories c ORDER BY c.name COLLATE NOCASE
            """
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def create_category(self, name: str) -> dict:
        name = name.strip()
        if not name:
            raise ValueError("category name is required")
        async with self.write() as conn:
            async with conn.execute(
                "INSERT INTO categories (name, created_at) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET name=excluded.name RETURNING id, name",
                (name, time.time()),
            ) as cur:
                row = await cur.fetchone()
                return dict(row)

    async def update_category(self, category_id: int, name: str) -> bool:
        async with self.write() as conn:
            async with conn.execute(
                "UPDATE categories SET name=? WHERE id=? RETURNING id", (name.strip(), category_id)
            ) as cur:
                row = await cur.fetchone()
                return row is not None

    async def delete_category(self, category_id: int) -> bool:
        async with self.write() as conn:
            await conn.execute("UPDATE sources SET category_id=NULL WHERE category_id=?", (category_id,))
            async with conn.execute("DELETE FROM categories WHERE id=?", (category_id,)) as cur:
                return cur.rowcount > 0

    # -- sources ----------------------------------------------------------
    async def list_sources(self) -> list[dict]:
        conn = await self.get_conn()
        async with conn.execute(
            """
            SELECT s.*, c.name AS category_name,
                   (SELECT COUNT(*) FROM items i
                     WHERE i.source_id = s.id AND i.dismissed = 0) AS item_count
            FROM sources s LEFT JOIN categories c ON c.id = s.category_id
            ORDER BY c.name COLLATE NOCASE, s.name COLLATE NOCASE, s.url
            """
        ) as cur:
            rows = await cur.fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["config"] = json.loads(d["config"] or "{}")
                d["enabled"] = bool(d["enabled"])
                d["hidden"] = bool(d["hidden"])
                out.append(d)
            return out

    async def get_source(self, source_id: int) -> dict | None:
        conn = await self.get_conn()
        async with conn.execute(
            "SELECT s.*, c.name AS category_name, "
            "(SELECT COUNT(*) FROM items i WHERE i.source_id = s.id AND i.dismissed = 0) "
            "AS item_count FROM sources s "
            "LEFT JOIN categories c ON c.id = s.category_id WHERE s.id=?",
            (source_id,),
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
            d = dict(row)
            d["config"] = json.loads(d["config"] or "{}")
            d["enabled"] = bool(d["enabled"])
            d["hidden"] = bool(d["hidden"])
            return d

    async def create_source(self, url: str, name: str = "", plugin: str = "",
                      category_id: int | None = None, config: dict | None = None) -> int:
        async with self.write() as conn:
            async with conn.execute(
                "INSERT INTO sources (url, name, plugin, category_id, config, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(url) DO UPDATE SET name=excluded.name, plugin=excluded.plugin, "
                "category_id=excluded.category_id, config=excluded.config RETURNING id",
                (url, name, plugin, category_id, json.dumps(config or {}), time.time()),
            ) as cur:
                row = await cur.fetchone()
                return row["id"]

    async def update_source(self, source_id: int, **fields) -> bool:
        allowed = {"name", "url", "plugin", "category_id", "enabled", "hidden", "config",
                   "etag", "last_modified", "last_refreshed_at", "last_status", "last_error"}
        sets, args = [], []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "config":
                value = json.dumps(value or {})
            elif key in ("enabled", "hidden"):
                value = 1 if value else 0
            sets.append(f"{key}=?")
            args.append(value)
        if not sets:
            return False
        args.append(source_id)
        async with self.write() as conn:
            async with conn.execute(f"UPDATE sources SET {', '.join(sets)} WHERE id=?", args) as cur:
                return cur.rowcount > 0

    async def delete_source(self, source_id: int) -> bool:
        async with self.write() as conn:
            async with conn.execute("DELETE FROM sources WHERE id=?", (source_id,)) as cur:
                return cur.rowcount > 0

    # -- items -------------------------------------------------------------
    async def known_dates(self, source_id: int) -> dict[str, float | None]:
        """guid -> stored published_at (None when the item has no date yet);
        plugins use it to backfill only what is still missing."""
        conn = await self.get_conn()
        async with conn.execute(
            "SELECT guid, published_at FROM items WHERE source_id=?", (source_id,)
        ) as cur:
            rows = await cur.fetchall()
            return {r["guid"]: r["published_at"] for r in rows}

    async def upsert_items(self, source_id: int, items: list[dict]) -> tuple[int, int]:
        """Insert/update parsed items. Returns (inserted, updated)."""
        if not items:
            return (0, 0)
        existing = await self.known_dates(source_id)
        now = time.time()
        max_date = now + _FUTURE_DATE_TOLERANCE
        rows = []
        upd_rows = []
        seen = set()
        for it in items:
            guid = str(it.get("guid") or "").strip()
            if not guid or guid in seen:
                continue
            seen.add(guid)
            published_at = it.get("published_at")
            if published_at is not None and published_at > max_date:
                published_at = None  # future date: CMS timezone bug or scheduled post
            rows.append((
                source_id, guid, it.get("url") or "", it.get("title") or "",
                it.get("summary") or "", it.get("content") or "", it.get("author") or "",
                published_at, it.get("image_url") or "",
                json.dumps(it.get("extra") or {}, ensure_ascii=False), now, now,
            ))
            if guid in existing:
                upd_rows.append(guid)
        inserted = updated = 0
        if rows:
            async with self.write() as conn:
                await conn.executemany(
                    """
                    INSERT INTO items (source_id, guid, url, title, summary, content, author,
                                       published_at, image_url, extra, fetched_at, first_seen_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(source_id, guid) DO UPDATE SET
                        url=excluded.url, title=excluded.title,
                        summary=CASE WHEN length(excluded.summary) > length(items.summary)
                                     THEN excluded.summary ELSE items.summary END,
                        content=CASE WHEN length(excluded.content) > length(items.content)
                                     THEN excluded.content ELSE items.content END,
                        author=CASE WHEN excluded.author != '' THEN excluded.author
                                    ELSE items.author END,
                        published_at=COALESCE(excluded.published_at, items.published_at),
                        image_url=CASE WHEN excluded.image_url != '' THEN excluded.image_url
                                       ELSE items.image_url END,
                        extra=excluded.extra, fetched_at=excluded.fetched_at
                    """,
                    rows,
                )
        inserted = len(rows) - len(upd_rows)
        updated = len(upd_rows)
        async with self.write() as conn:
            id_by_guid = {}
            guids = [r[1] for r in rows]
            for i in range(0, len(guids), 500):
                chunk = guids[i:i + 500]
                qmarks = ",".join("?" * len(chunk))
                async with conn.execute(
                    f"SELECT id, guid FROM items WHERE source_id=? AND guid IN ({qmarks})",
                    [source_id, *chunk],
                ) as cur:
                    for r in await cur.fetchall():
                        id_by_guid[r["guid"]] = r["id"]
            tag_rows = []
            for it in items:
                item_id = id_by_guid.get(str(it.get("guid") or ""))
                if item_id is None:
                    continue
                for tag in it.get("tags") or []:
                    tag = (tag or "").strip()
                    if tag:
                        tag_rows.append((item_id, tag))
            if tag_rows:
                await conn.executemany(
                    "INSERT INTO tags (name) VALUES (?) "
                    "ON CONFLICT(name) DO UPDATE SET name=excluded.name", [(t,) for _, t in tag_rows],
                )
                await conn.executemany(
                    """
                    INSERT OR IGNORE INTO item_tags (item_id, tag_id)
                    SELECT ?, id FROM tags WHERE name = ? COLLATE NOCASE
                    """,
                    tag_rows,
                )
        return (inserted, updated)

    # full row (detail view); the list query omits extra/content to stay slim
    _SELECT = f"SELECT {_ITEM_COLS}, i.extra, i.content {_ITEM_FROM}"
    _SELECT_LIST = f"SELECT {_ITEM_COLS} {_ITEM_FROM}"

    async def list_items(self, q: str | None = None, source_id: int | None = None,
                   category_id: int | None = None, tag: str | None = None,
                   starred: bool = False, sort: str = "new",
                   limit: int = 50, offset: int = 0,
                   include_content: bool = False) -> tuple[list[dict], int]:
        where, args = ["i.dismissed = 0"], []
        join = ""
        match = None
        if q:
            match = fts_query(q)
            if match:
                join = ("JOIN (SELECT rowid AS frowid, rank AS frank FROM items_fts "
                        "WHERE items_fts MATCH ?) f ON f.frowid = i.id")
                args.append(match)
            else:
                q = None
        if starred:
            where.append("i.starred = 1")
        if source_id is not None:
            # an explicit source view shows its items even when the source is hidden
            where.append("i.source_id = ?")
            args.append(source_id)
        else:
            where.append("s.hidden = 0")
        if category_id is not None:
            where.append("s.category_id = ?")
            args.append(category_id)
        if tag:
            where.append("EXISTS (SELECT 1 FROM item_tags jt JOIN tags t ON t.id = jt.tag_id "
                         "WHERE jt.item_id = i.id AND t.name = ? COLLATE NOCASE)")
            args.append(tag)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        if sort == "rank" and match:
            order = "ORDER BY f.frank"
        elif sort == "old":
            # dated items oldest-first; undated tail by (ascending) added date
            order = ("ORDER BY (i.published_at IS NULL) ASC, i.published_at ASC, "
                     "i.first_seen_at ASC, i.id ASC")
        else:
            # dated items newest-first; undated tail by (descending) added date
            order = ("ORDER BY (i.published_at IS NULL) ASC, i.published_at DESC, "
                     "i.first_seen_at DESC, i.id DESC")
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        select = self._SELECT if include_content else self._SELECT_LIST
        conn = await self.get_conn()
        async with conn.execute(
            f"{select} {join} {clause} {order} LIMIT ? OFFSET ?",
            [*args, limit, offset],
        ) as cur:
            rows = await cur.fetchall()
            
        async with conn.execute(
            f"SELECT COUNT(*) FROM items i JOIN sources s ON s.id = i.source_id {join} {clause}",
            args,
        ) as cur:
            total_row = await cur.fetchone()
            total = total_row[0]
            
        items = []
        for r in rows:
            d = dict(r)
            d["tags"] = json.loads(d["tags"] or "[]")
            d["starred"] = bool(d["starred"])
            if include_content:
                d["extra"] = json.loads(d["extra"] or "{}")
            items.append(d)
        return items, total

    async def get_item(self, item_id: int) -> dict | None:
        conn = await self.get_conn()
        async with conn.execute(f"{self._SELECT} WHERE i.id = ?", (item_id,)) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
            d = dict(row)
            d["tags"] = json.loads(d["tags"] or "[]")
            d["extra"] = json.loads(d["extra"] or "{}")
            d["starred"] = bool(d["starred"])
            return d

    async def set_starred(self, item_id: int, starred: bool) -> bool:
        async with self.write() as conn:
            async with conn.execute("UPDATE items SET starred=? WHERE id=?",
                               (1 if starred else 0, item_id)) as cur:
                return cur.rowcount > 0

    async def set_dismissed(self, item_id: int, dismissed: bool) -> bool:
        async with self.write() as conn:
            async with conn.execute("UPDATE items SET dismissed=? WHERE id=?",
                               (1 if dismissed else 0, item_id)) as cur:
                return cur.rowcount > 0

    # -- tags ----------------------------------------------------------------
    async def list_tags(self) -> list[dict]:
        conn = await self.get_conn()
        async with conn.execute(
            """
            SELECT t.id, t.name,
                   (SELECT COUNT(*) FROM item_tags jt JOIN items i ON i.id = jt.item_id
                     JOIN sources s ON s.id = i.source_id
                     WHERE jt.tag_id = t.id AND i.dismissed = 0 AND s.hidden = 0) AS item_count
            FROM tags t
            ORDER BY item_count DESC, t.name COLLATE NOCASE
            """
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def _tag_ids(self, conn: aiosqlite.Connection, names: Iterable[str]) -> list[int]:
        ids = []
        for name in names:
            name = (name or "").strip()
            if not name:
                continue
            async with conn.execute(
                "INSERT INTO tags (name) VALUES (?) ON CONFLICT(name) DO UPDATE SET name=excluded.name "
                "RETURNING id", (name,)
            ) as cur:
                row = await cur.fetchone()
                ids.append(row["id"])
        return ids

    async def set_item_tags(self, item_id: int, names: list[str]) -> list[str]:
        async with self.write() as conn:
            ids = await self._tag_ids(conn, names)
            await conn.execute("DELETE FROM item_tags WHERE item_id=?", (item_id,))
            await conn.executemany(
                "INSERT OR IGNORE INTO item_tags (item_id, tag_id) VALUES (?, ?)",
                [(item_id, tid) for tid in ids],
            )
        item = await self.get_item(item_id)
        return item["tags"]

    async def add_item_tags(self, item_id: int, names: list[str]) -> list[str]:
        async with self.write() as conn:
            ids = await self._tag_ids(conn, names)
            await conn.executemany(
                "INSERT OR IGNORE INTO item_tags (item_id, tag_id) VALUES (?, ?)",
                [(item_id, tid) for tid in ids],
            )
        item = await self.get_item(item_id)
        return item["tags"]

    async def remove_item_tag(self, item_id: int, name: str) -> list[str]:
        async with self.write() as conn:
            await conn.execute(
                "DELETE FROM item_tags WHERE item_id=? AND tag_id=(SELECT id FROM tags WHERE name=? COLLATE NOCASE)",
                (item_id, name),
            )
        item = await self.get_item(item_id)
        return item["tags"]

    # -- stats ----------------------------------------------------------------
    async def counts(self) -> dict:
        conn = await self.get_conn()
        
        async def fetch_count(q: str):
            async with conn.execute(q) as cur:
                return (await cur.fetchone())[0]

        return {
            "sources": await fetch_count("SELECT COUNT(*) FROM sources"),
            "enabled_sources": await fetch_count("SELECT COUNT(*) FROM sources WHERE enabled=1"),
            "items": await fetch_count("SELECT COUNT(*) FROM items WHERE dismissed=0"),
            "starred_items": await fetch_count(
                "SELECT COUNT(*) FROM items i JOIN sources s ON s.id = i.source_id "
                "WHERE i.starred=1 AND i.dismissed=0 AND s.hidden=0"),
            "dismissed": await fetch_count("SELECT COUNT(*) FROM items WHERE dismissed=1"),
            "tags": await fetch_count("SELECT COUNT(*) FROM tags"),
            "categories": await fetch_count("SELECT COUNT(*) FROM categories"),
        }
