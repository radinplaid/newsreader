"""arxiv.org sources: search result pages and single abstract pages.

Search URLs (https://arxiv.org/search/?query=...&searchtype=all&...) are
parsed straight from the result listing (li.arxiv-result), following the
pagination up to max_results with arxiv's requested 3s delay between
requests. Single /abs/ pages use the citation meta tags.
"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from crawler import register
from crawler.base import Plugin, FetchContext, FetchResult, ParsedItem
from crawler.pool import run_parse
from crawler.textutil import clean, find_date_in_text, parse_date, soup_from, text_of

_ID_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/([0-9v./-]+)", re.I)
_SUBMITTED_RE = re.compile(r"Submitted\s+\d{1,2}\s+\w+,\s*\d{4}|\d{1,2}\s+\w+\s+\d{4}")


def _paper_id(href: str) -> str:
    m = _ID_RE.search(href or "")
    if not m:
        return ""
    return m.group(1).rstrip("/")


def parse_search_page(html: str, page_url: str) -> list[dict]:
    soup = soup_from(html)
    out = []
    for li in soup.select("li.arxiv-result"):
        abs_link = li.select_one('a[href*="/abs/"]')
        if abs_link is None:
            continue
        paper_id = _paper_id(abs_link.get("href", "")) or text_of(abs_link)
        url = f"https://arxiv.org/abs/{paper_id}"
        title_el = li.select_one("p.title")
        title = text_of(title_el) or text_of(abs_link)
        if not title:
            continue
        authors_el = li.select_one("p.authors")
        if authors_el is not None:
            author_names = [text_of(a) for a in authors_el.select("a")]
            author_names = [a for a in author_names if a]
            authors = ", ".join(author_names) if author_names else \
                re.sub(r"^Authors:\s*", "", text_of(authors_el), flags=re.I)
        else:
            authors = ""
        abstract = ""
        full = li.select_one("span.abstract-full")
        short = li.select_one("span.abstract-short")
        for el in (full, short):
            if el is not None:
                abstract = text_of(el)
                abstract = re.sub(r"(▲\s*Less|▼\s*More)\s*$", "", abstract).strip()
                if abstract:
                    break
        submitted = None
        for p in li.select("p"):
            t = text_of(p)
            if t.lower().startswith("submitted"):
                submitted = find_date_in_text(t)
                if submitted:
                    break
        comments_el = li.select_one("p.comments")
        comments = text_of(comments_el)
        comments = re.sub(r"^Comments:\s*", "", comments, flags=re.I)
        tags = [text_of(t) for t in li.select("span.tag") if text_of(t)]
        pdf_link = li.select_one('a[href*="/pdf/"]')
        out.append({
            "guid": paper_id, "url": url, "title": title, "summary": abstract[:500],
            "content": abstract, "author": authors,
            "published_at": submitted, "tags": tags[:5],
            "extra": {"comments": comments, "kind": "arxiv"},
            "_pdf": pdf_link.get("href", "") if pdf_link is not None else "",
        })
    return out


def parse_abs_page(html: str, page_url: str) -> dict | None:
    soup = soup_from(html)
    paper_id = _paper_id(page_url) or ""
    title = ""
    og = soup.find("meta", attrs={"property": "og:title"})
    if og is not None:
        title = clean(og.get("content") or "")
    if not title:
        h1 = soup.find("h1", class_="title")
        title = re.sub(r"^Title:\s*", "", text_of(h1))
    if not title:
        return None
    authors = ""
    for m in soup.find_all("meta", attrs={"name": "citation_author"}):
        name = clean(m.get("content") or "")
        if name:
            authors = (authors + ", " + name).strip(", ")
    abstract = ""
    for sel in ('meta[property="og:description"]', 'meta[name="citation_abstract"]',
                "blockquote.abstract"):
        el = soup.select_one(sel)
        if el is not None:
            abstract = clean(el.get("content") or el.get_text(" ", strip=True))
            if abstract:
                break
    published = None
    el = soup.find("meta", attrs={"name": "citation_date"} ) or \
        soup.find("meta", attrs={"name": "citation_online_date"})
    if el is not None:
        published = parse_date(el.get("content") or "")
    if not published:
        published = find_date_in_text(text_of(soup))
    tags = []
    for a in soup.select("td.tablecell.subjects a, span.primary-subject"):
        t = clean(a.get_text(" ", strip=True))
        if t and t not in tags:
            tags.append(t)
    return {
        "guid": paper_id or page_url, "url": page_url, "title": title,
        "summary": abstract[:500], "content": abstract, "author": authors,
        "published_at": published, "tags": tags[:5], "extra": {"kind": "arxiv"},
    }


@register
class ArxivPlugin(Plugin):
    name = "arxiv"
    label = "arXiv search / paper"
    patterns = (r"https?://(www\.)?arxiv\.org/",)
    priority = 25

    def default_config(self) -> dict:
        return {"max_results": 100, "page_delay": 3.0}

    async def fetch(self, ctx: FetchContext) -> FetchResult:
        url = ctx.source["url"]
        if "/search/" in url:
            items = await self._fetch_search(ctx, url)
            name = self._search_name(url)
        elif "/abs/" in url:
            text = await ctx.get_text(url)
            paper = await run_parse(parse_abs_page, text or "", url)
            items = [paper] if paper else []
            name = "arXiv"
        else:
            from crawler.base import FetchError
            raise FetchError("arxiv plugin supports /search/ and /abs/ URLs")
        result_items = [ParsedItem(**{k: v for k, v in i.items() if k in ParsedItem.__dataclass_fields__})
                        for i in items]
        return FetchResult(items=result_items, source_name=name)

    @staticmethod
    def _search_name(url: str) -> str:
        qs = parse_qs(urlparse(url).query)
        query = (qs.get("query") or [""])[0]
        return f"arXiv: {query}" if query else "arXiv search"

    async def _fetch_search(self, ctx: FetchContext, url: str) -> list[dict]:
        max_results = int(ctx.config.get("max_results", 100))
        delay = float(ctx.config.get("page_delay", 3.0))
        parts = urlparse(url)
        qs = parse_qs(parts.query)
        size = int((qs.get("size") or ["50"])[0])
        items: list[dict] = []
        seen: set[str] = set()
        start = 0
        while start < max_results:
            page_qs = dict(qs)
            page_qs["start"] = str(start)
            page_url = urlunparse((parts.scheme, parts.netloc, parts.path, "",
                                   urlencode(page_qs, doseq=True), ""))
            text = await ctx.get_text(page_url, conditional=(start == 0))
            if text is None:
                break
            page_items = await run_parse(parse_search_page, text, page_url)
            new = [i for i in page_items if i["guid"] not in seen]
            if not new:
                break
            for i in new:
                seen.add(i["guid"])
            items.extend(new)
            if len(page_items) < size:
                break
            start += size
            if start < max_results:
                await asyncio.sleep(delay)  # arxiv robots: 3s between requests
        return items[:max_results]
