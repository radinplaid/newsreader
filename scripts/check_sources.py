"""Live-check every enabled source (or given URLs) through the plugin chain.

Usage:
  python scripts/check_sources.py            # all sources in the db
  python scripts/check_sources.py --url URL  # just one URL (no db needed)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

import crawler  # noqa: E402
from crawler.base import FetchContext  # noqa: E402
from fetcher import USER_AGENT  # noqa: E402
from models.database import Database  # noqa: E402


def fmt_ts(ts: float | None) -> str:
    if not ts:
        return "-"
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


async def check(url: str, client: httpx.AsyncClient, name: str = "") -> dict:
    plugin = crawler.find_plugin(url)
    if plugin is None:
        return {"url": url, "error": "no plugin matches"}
    config = plugin.default_config()
    ctx = FetchContext(source={"id": 0, "url": url, "name": name, "etag": "",
                               "last_modified": "", "config": {}},
                       client=client, known_guids=set(), config=config,
                       logger=logging.getLogger("check"))
    started = time.time()
    try:
        result = await plugin.fetch(ctx)
    except Exception as exc:  # noqa: BLE001
        return {"url": url, "plugin": plugin.name, "error": f"{type(exc).__name__}: {exc}",
                "seconds": round(time.time() - started, 1)}
    items = result.items
    dated = sum(1 for i in items if i.published_at)
    sample = [f"{i.title[:70]} ({fmt_ts(i.published_at)})" for i in items[:3]]
    return {
        "url": url, "plugin": plugin.name, "items": len(items),
        "dated": dated, "with_content": sum(1 for i in items if i.content),
        "source_name": result.source_name, "requests": ctx.requests,
        "seconds": round(time.time() - started, 1), "sample": sample,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=None)
    parser.add_argument("--url", action="append", default=[])
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()

    logging.disable(logging.WARNING)
    crawler.discover()
    targets = [(u, "") for u in args.url]
    if not targets:
        db = Database(args.db)
        targets = [(s["url"], s["name"] or s["url"]) for s in db.list_sources() if s["enabled"]]
    sem = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(timeout=40, follow_redirects=True,
                                 headers={"User-Agent": USER_AGENT,
                                          "Accept-Encoding": "gzip, deflate"}) as client:

        async def run(u, n):
            async with sem:
                return await check(u, client, n)

        results = await asyncio.gather(*(run(u, n) for u, n in targets))
    ok = 0
    for r in results:
        if r.get("error"):
            print(f"FAIL [{r.get('plugin','?')}] {r['url']}\n      {r['error']}")
            continue
        ok += 1
        print(f"OK   [{r['plugin']:>7}] {r['url']}")
        print(f"       items={r['items']} dated={r['dated']} content={r['with_content']} "
              f"requests={r['requests']} in {r['seconds']}s  (feed: {r.get('source_name')})")
        for s in r["sample"]:
            print(f"       · {s}")
    print(f"\n{ok}/{len(results)} sources parsed successfully")


if __name__ == "__main__":
    asyncio.run(main())
