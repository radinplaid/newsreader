"""Tests for text/date helpers."""
import calendar
import time
from datetime import datetime, timezone

from crawler.textutil import (absolutize, clean, find_date_in_text, first_image_src,
                              parse_date, strip_tags, timeago)


def ts(y, m, d, hh=12, mm=0, ss=0):
    return calendar.timegm((y, m, d, hh, mm, ss, 0, 0, 0))


def test_parse_date_iso():
    assert parse_date("2026-09-10") == ts(2026, 9, 10)
    assert parse_date("2026-09-10T08:30:00Z") == ts(2026, 9, 10, 8, 30)
    assert parse_date("2026-09-10T08:30:00+02:00") == ts(2026, 9, 10, 6, 30)


def test_parse_date_rfc2822():
    expected = datetime(2026, 9, 15, 17, 54, 48,
                        tzinfo=timezone.utc).timestamp()
    assert parse_date("Tue, 15 Sep 2026 17:54:48 GMT") == expected


def test_parse_date_human_formats():
    assert parse_date("Sep 10, 2026") == ts(2026, 9, 10)
    assert parse_date("September 10, 2026") == ts(2026, 9, 10)
    assert parse_date("10 Sep 2026") == ts(2026, 9, 10)
    assert parse_date("10th September 2026") == ts(2026, 9, 10)
    assert parse_date("") is None
    assert parse_date("not a date") is None
    assert parse_date(None) is None


def test_find_date_in_text():
    text = "Published Apr 24, 2026 · 5 min read"
    assert find_date_in_text(text) == ts(2026, 4, 24)
    assert find_date_in_text("Submitted 13 September, 2026; announced later") == ts(2026, 9, 13)
    assert find_date_in_text("September 2026") == ts(2026, 9, 1)
    assert find_date_in_text("2026-09-10 changelog") == ts(2026, 9, 10)
    assert find_date_in_text("no dates here") is None


def test_clean_and_absolutize():
    assert clean("  a \n\t b  ") == "a b"
    assert absolutize("https://ex.com/blog/", "post-1") == "https://ex.com/blog/post-1"
    assert absolutize("https://ex.com/blog/", "/x") == "https://ex.com/x"
    assert absolutize("https://ex.com/blog/", "") == "https://ex.com/blog/"


def test_first_image_src():
    from bs4 import BeautifulSoup

    def img(html):
        return first_image_src(BeautifulSoup(html, "lxml"))

    assert img('<img src="a.jpg">') == "a.jpg"
    assert img('<img data-src="b.jpg">') == "b.jpg"
    assert img('<img srcset="c1.jpg 1x, c2.jpg 2x" >') == "c1.jpg"
    assert img('<img srcset="c1.jpg 1x, c2.jpg 2x" src="c0.jpg">') == "c0.jpg"
    assert img("<div>no image</div>") == ""


def test_first_image_src_absolutizes():
    from bs4 import BeautifulSoup

    base = "https://mistral.ai/news/"
    assert first_image_src(
        BeautifulSoup('<img src="/_astro/x.webp">', "lxml"), base
    ) == "https://mistral.ai/_astro/x.webp"
    assert first_image_src(
        BeautifulSoup('<img srcset="/a.webp 400w, /b.webp 800w">', "lxml"), base
    ) == "https://mistral.ai/a.webp"
    # already-absolute URLs pass through untouched
    assert first_image_src(
        BeautifulSoup('<img src="https://cdn.ex/i.png">', "lxml"), base
    ) == "https://cdn.ex/i.png"
    # no base URL: raw value, unchanged (backwards compatible)
    assert first_image_src(BeautifulSoup('<img src="/i.png">', "lxml")) == "/i.png"


def test_strip_tags_and_timeago():
    assert strip_tags("<p>Hello <b>world</b></p>") == "Hello world"
    assert timeago(None) == ""
    assert timeago(time.time() - 10) == "just now"
    assert timeago(time.time() - 3600 * 3) == "3h ago"
