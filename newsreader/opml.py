"""OPML 2.0 serialization for source lists.

Beyond standard outline attributes, custom metadata transfers Newsreader
source definitions, not articles or read state. Other readers may strip this
metadata or not support non-feed sources. Foreign OPML files still parse.
"""
from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

ATTR_PLUGIN = "plugin"
ATTR_ENABLED = "enabled"
ATTR_HIDDEN = "hidden"
ATTR_CONFIG = "config"
ATTR_CATEGORY = "category"


@dataclass
class OpmlSource:
    url: str
    name: str = ""
    plugin: str = ""
    category: str | None = None
    enabled: bool = True
    hidden: bool = False
    config: dict = field(default_factory=dict)
    supplied_fields: set[str] = field(default_factory=set)


def export_opml(sources: list[dict], title: str = "newsreader sources") -> str:
    """Serialize source rows (from Database.list_sources) to an OPML 2.0 document."""
    root = ET.Element("opml", {"version": "2.0"})
    head = ET.SubElement(root, "head")
    ET.SubElement(head, "title").text = title
    ET.SubElement(head, "dateCreated").text = time.strftime(
        "%a, %d %b %Y %H:%M:%S GMT", time.gmtime())

    by_category: dict[str, list[dict]] = {}
    for src in sources:
        by_category.setdefault(src.get("category_name") or "", []).append(src)

    body = ET.SubElement(root, "body")
    for category in sorted((c for c in by_category if c), key=str.casefold):
        folder = ET.SubElement(body, "outline", {"text": category})
        for src in by_category[category]:
            folder.append(_source_outline(src))
    for src in by_category.get("", []):
        body.append(_source_outline(src))

    ET.indent(root, space="  ")
    xml_body = ET.tostring(root, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_body + "\n"


def parse_opml(data: bytes | str) -> list[OpmlSource]:
    """Parse an OPML document into source rows. Raises ValueError on bad XML."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    try:
        root = ET.fromstring(data)
    except (ET.ParseError, LookupError, ValueError) as exc:
        raise ValueError(f"invalid OPML: {exc}") from exc

    if root.tag != "opml":
        raise ValueError("invalid OPML: expected <opml> root")
    body = root.find("body")
    if body is None:
        raise ValueError("invalid OPML: no <body> element")

    out: list[OpmlSource] = []
    _walk(body, [], out)
    return out


def _source_outline(src: dict) -> ET.Element:
    name = src.get("name", src["url"])
    attrs = {
        "type": "rss",
        "text": name,
        "title": name,
        "xmlUrl": src["url"],
        ATTR_PLUGIN: src.get("plugin") or "",
        ATTR_CATEGORY: src.get("category_name") or "",
        ATTR_ENABLED: "1" if src.get("enabled", True) else "0",
        ATTR_HIDDEN: "1" if src.get("hidden", False) else "0",
        ATTR_CONFIG: json.dumps(src.get("config") or {},
                                separators=(",", ":"), sort_keys=True),
    }
    return ET.Element("outline", attrs)


def _walk(el: ET.Element, folder_path: list[str], out: list[OpmlSource]) -> None:
    for child in el.findall("outline"):
        url = (child.get("xmlUrl") or child.get("url") or "").strip()
        if url:
            out.append(_source_from_outline(child, url, folder_path))
        else:
            name = _outline_name(child).strip()
            path = folder_path + [name] if name else list(folder_path)
            _walk(child, path, out)


def _source_from_outline(el: ET.Element, url: str,
                         folder_path: list[str]) -> OpmlSource:
    name = _outline_name(el)
    supplied = {key for key in ("plugin", "enabled", "hidden") if key in el.attrib}
    if "text" in el.attrib or "title" in el.attrib:
        supplied.add("name")
    category = " / ".join(folder_path) if folder_path else None
    if ATTR_CATEGORY in el.attrib:
        category = el.get(ATTR_CATEGORY) or None
        supplied.add("category")
    elif folder_path:
        supplied.add("category")
    config = _config_attr(el.get(ATTR_CONFIG))
    if config is not None:
        supplied.add("config")
    return OpmlSource(
        url=url,
        name=name if "name" in supplied else url,
        plugin=(el.get(ATTR_PLUGIN) or "").strip(),
        category=category,
        enabled=_bool_attr(el.get(ATTR_ENABLED), True),
        hidden=_bool_attr(el.get(ATTR_HIDDEN), False),
        config=config if config is not None else {},
        supplied_fields=supplied,
    )


def _outline_name(el: ET.Element) -> str:
    return el.get("text", el.get("title", ""))


def _bool_attr(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    norm = value.strip().lower()
    if norm in ("1", "true", "yes"):
        return True
    if norm in ("0", "false", "no"):
        return False
    return default


def _config_attr(value: str | None) -> dict | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None
