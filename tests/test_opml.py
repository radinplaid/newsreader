"""Unit tests for OPML serialization (newsreader.opml)."""
import pytest

from newsreader.opml import export_opml, parse_opml


def _row(url, name, plugin="rss", category_name=None, enabled=True,
         hidden=False, config=None):
    return {"url": url, "name": name, "plugin": plugin,
            "category_name": category_name, "enabled": enabled,
            "hidden": hidden, "config": config or {}}


def test_round_trip_preserves_fields():
    rows = [
        _row("https://example.org/feed.xml", "Example", plugin="rss",
             category_name="News", enabled=False, hidden=True,
             config={"max_items": 5}),
        _row("https://www.youtube.com/playlist?list=PLabc", "Videos",
             plugin="youtube"),
        _row("https://example.com/page", "Scrape", plugin="web", config={}),
    ]
    parsed = parse_opml(export_opml(rows))
    assert len(parsed) == 3
    by_url = {s.url: s for s in parsed}

    feed = by_url["https://example.org/feed.xml"]
    assert feed.name == "Example" and feed.plugin == "rss"
    assert feed.category == "News"
    assert feed.enabled is False and feed.hidden is True
    assert feed.config == {"max_items": 5}

    videos = by_url["https://www.youtube.com/playlist?list=PLabc"]
    assert videos.plugin == "youtube" and videos.category is None
    assert videos.enabled is True and videos.hidden is False

    scrape = by_url["https://example.com/page"]
    assert scrape.plugin == "web" and scrape.config == {}


def test_export_groups_by_category():
    rows = [
        _row("https://a.example/1.xml", "One", category_name="Media"),
        _row("https://a.example/2.xml", "Two", category_name="AI"),
        _row("https://a.example/3.xml", "Three"),
    ]
    xml = export_opml(rows)
    assert '<outline text="AI">' in xml
    assert '<outline text="Media">' in xml
    assert xml.index('text="AI"') < xml.index('text="Media"')
    assert xml.count('type="rss"') == 3


def test_parse_nested_folders_join_path():
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0"><body>
  <outline text="Outer">
    <outline text="Inner">
      <outline text="Deep feed" xmlUrl="https://a.example/deep.xml"/>
    </outline>
    <outline text="Shallow feed" xmlUrl="https://a.example/shallow.xml"/>
  </outline>
</body></opml>"""
    parsed = parse_opml(xml)
    by_url = {s.url: s for s in parsed}
    assert by_url["https://a.example/deep.xml"].category == "Outer / Inner"
    assert by_url["https://a.example/shallow.xml"].category == "Outer"


def test_parse_foreign_opml_defaults():
    xml = """<opml version="2.0"><body>
  <outline text="From elsewhere" xmlUrl="https://news.example/rss"/>
  <outline title="Titled" xmlUrl="https://news.example/two"/>
  <outline xmlUrl="https://news.example/unnamed"/>
</body></opml>"""
    parsed = parse_opml(xml)
    assert len(parsed) == 3
    by_url = {s.url: s for s in parsed}
    first = by_url["https://news.example/rss"]
    assert first.name == "From elsewhere" and first.plugin == ""
    assert first.enabled is True and first.hidden is False and first.config == {}
    assert first.category is None
    assert by_url["https://news.example/two"].name == "Titled"
    assert by_url["https://news.example/unnamed"].name == "https://news.example/unnamed"


def test_parse_url_attr_fallback():
    xml = '<opml version="2.0"><body><outline text="X" url="https://a.example/x"/></body></opml>'
    parsed = parse_opml(xml)
    assert parsed[0].url == "https://a.example/x"


def test_parse_bool_attr_values():
    xml = ('<opml version="2.0"><body>'
           '<outline text="A" xmlUrl="https://a.example/a" enabled="true" hidden="yes"/>'
           '<outline text="B" xmlUrl="https://a.example/b" enabled="0" hidden="false"/>'
           '<outline text="C" xmlUrl="https://a.example/c" enabled="garbage"/>'
           '</body></opml>')
    parsed = parse_opml(xml)
    by_url = {s.url: s for s in parsed}
    assert by_url["https://a.example/a"].enabled is True
    assert by_url["https://a.example/a"].hidden is True
    assert by_url["https://a.example/b"].enabled is False
    assert by_url["https://a.example/b"].hidden is False
    assert by_url["https://a.example/c"].enabled is True


def test_parse_config_attr_garbage():
    xml = ('<opml version="2.0"><body>'
           '<outline text="A" xmlUrl="https://a.example/a" config="not-json"/>'
           '<outline text="B" xmlUrl="https://a.example/b" config="[1,2]"/>'
           '<outline text="C" xmlUrl="https://a.example/c" config="{&quot;k&quot;: 1}"/>'
           '</body></opml>')
    parsed = parse_opml(xml)
    by_url = {s.url: s for s in parsed}
    assert by_url["https://a.example/a"].config == {}
    assert by_url["https://a.example/b"].config == {}
    assert by_url["https://a.example/c"].config == {"k": 1}


def test_parse_ignores_empty_folders_and_leaves_without_url():
    xml = ('<opml version="2.0"><body>'
           '<outline text="Empty folder"/>'
           '<outline text="Bare leaf"/>'
           '<outline text="Kept" xmlUrl="https://a.example/keep.xml"/>'
           '</body></opml>')
    parsed = parse_opml(xml)
    assert [s.url for s in parsed] == ["https://a.example/keep.xml"]


def test_parse_keeps_in_file_duplicates():
    xml = ('<opml version="2.0"><body>'
           '<outline text="First" xmlUrl="https://a.example/dup.xml"/>'
           '<outline text="Second" xmlUrl="https://a.example/dup.xml"/>'
           '</body></opml>')
    parsed = parse_opml(xml)
    assert len(parsed) == 2


def test_parse_accepts_bytes_and_str():
    xml = '<opml version="2.0"><body><outline text="A" xmlUrl="https://a.example/a"/></body></opml>'
    assert parse_opml(xml)[0].url == "https://a.example/a"
    assert parse_opml(xml.encode("utf-8"))[0].url == "https://a.example/a"


def test_parse_rejects_malformed_xml():
    with pytest.raises(ValueError):
        parse_opml("this is not xml")
    with pytest.raises(ValueError):
        parse_opml("<opml version='2.0'><body><outline></body></opml>")
    with pytest.raises(ValueError):
        parse_opml("<opml version='2.0'></opml>")
    with pytest.raises(ValueError):
        parse_opml("<other><body/></other>")


@pytest.mark.parametrize("plugin", ["rss", "youtube", "reddit", "arxiv", "web"])
def test_all_plugins_explicit_metadata_round_trip(plugin):
    row = _row("https://example.org/?a=1&b=2", "", plugin,
               category_name='Research & "News"', enabled=False, hidden=True,
               config={"nested": {"items": [1, False, '<&"', None]}})
    source = parse_opml(export_opml([row]))[0]
    assert source.name == ""
    assert source.plugin == plugin
    assert source.category == row["category_name"]
    assert source.url == row["url"]
    assert source.config == row["config"]
    assert source.enabled is False and source.hidden is True
    assert source.supplied_fields == {
        "name", "plugin", "category", "enabled", "hidden", "config"}
    source = parse_opml(export_opml([_row(row["url"], "", plugin)]))[0]
    assert source.category is None and "category" in source.supplied_fields
    assert source.config == {} and "config" in source.supplied_fields


def test_optional_field_presence_and_category_precedence():
    sources = parse_opml('''<opml><body><outline text="Folder">
      <outline xmlUrl="https://a.example/1" category="" text=""/>
      <outline xmlUrl="https://a.example/2" category="Override"/>
      <outline xmlUrl="https://a.example/3"/>
    </outline><outline xmlUrl="https://a.example/4"/></body></opml>''')
    assert sources[0].category is None and sources[0].name == ""
    assert sources[0].supplied_fields == {"category", "name"}
    assert sources[1].category == "Override"
    assert sources[2].category == "Folder"
    assert sources[2].supplied_fields == {"category"}
    assert sources[3].supplied_fields == set()


@pytest.mark.parametrize("config", ["not-json", "[1,2]", "null", "", "42"])
def test_invalid_config_is_not_supplied(config):
    source = parse_opml(f'<opml><body><outline url="https://a.example/" '
                        f'config="{config}"/></body></opml>')[0]
    assert source.config == {}
    assert "config" not in source.supplied_fields


def test_declared_xml_encoding():
    xml = '<?xml version="1.0" encoding="ISO-8859-1"?><opml><body>' \
          '<outline text="Caf\u00e9" url="https://a.example/"/></body></opml>'
    assert parse_opml(xml.encode("iso-8859-1"))[0].name == "Caf\u00e9"
