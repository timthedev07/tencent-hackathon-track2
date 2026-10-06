"""Provider-payload parsing helpers kept at the connector boundary."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def strip_html(value: str) -> str:
    """Convert provider HTML to bounded plain text without retaining markup."""
    parser = _TextExtractor()
    parser.feed(value)
    parser.close()
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", " ".join(parser.parts))).strip()


def flatten_adf(value: Any) -> str:
    """Flatten Jira/Atlassian Document Format into readable text."""
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        if isinstance(value, list):
            return " ".join(filter(None, (flatten_adf(item) for item in value)))
        return ""
    node_type = value.get("type")
    text = str(value.get("text") or "")
    children = flatten_adf(value.get("content", []))
    combined = " ".join(part for part in (text, children) if part)
    if node_type in {"paragraph", "heading", "listItem", "blockquote", "codeBlock"}:
        return f"{combined}\n"
    return combined


def next_cursor(payload: dict[str, Any]) -> str | None:
    """Read common provider pagination cursor shapes."""
    for key in ("nextPageToken", "next_page_token", "cursor", "nextCursor"):
        value = payload.get(key)
        if value:
            return str(value)
    links = payload.get("_links")
    if isinstance(links, dict) and links.get("next"):
        return str(links["next"])
    return None
