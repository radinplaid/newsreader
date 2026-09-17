"""Seed the database with the project's required sources.

Usage: python scripts/seed_sources.py [--db PATH] [--fresh]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import crawler  # noqa: E402
from models.database import Database  # noqa: E402

SEED = {
    "AI Safety & Policy": [
        ("https://www.aisi.gov.uk/blog", "AISI Blog"),
        ("https://www.aisi.gov.uk/research", "AISI Research"),
    ],
    "AI Research": [
        ("https://metr.org/research/", "METR Research"),
        ("https://arxiv.org/search/?query=%22machine+translation%22&searchtype=all"
         "&abstracts=show&order=-announced_date_first&size=50", "arXiv: machine translation"),
    ],
    "Industry": [
        ("https://cohere.com/blog", "Cohere Blog"),
    ],
    "Media": [
        ("https://www.theguardian.com/technology/artificialintelligenceai/rss",
         "Guardian AI"),
    ],
    "Video": [
        ("https://www.youtube.com/playlist?list=PL9HYL-VRX0oQOXEh8rBFtLNsJxwC_Ta7A",
         "YouTube playlist"),
    ],
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=None)
    parser.add_argument("--fresh", action="store_true", help="delete the db first")
    args = parser.parse_args()

    db = Database(args.db)
    if args.fresh:
        for src in db.list_sources():
            db.delete_source(src["id"])
        for cat in db.list_categories():
            db.delete_category(cat["id"])

    crawler.discover()
    for category, sources in SEED.items():
        cat = db.create_category(category)
        for url, name in sources:
            plugin = crawler.find_plugin(url)
            if plugin is None:
                print(f"!! no plugin for {url}")
                continue
            sid = db.create_source(url, name=name, plugin=plugin.name, category_id=cat["id"])
            print(f"+ [{category}] {name} -> {plugin.name} (source {sid})")
    print("sources:", len(db.list_sources()), "categories:", len(db.list_categories()))


if __name__ == "__main__":
    main()
