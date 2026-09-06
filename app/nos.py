"""Fetch NOS news via RSS and extract the full article text (JSON-LD)."""
from __future__ import annotations

import gzip
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Optional
from urllib.request import Request, urlopen

from .config import Config

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 DutchDaily/1.0"
)


class _TextExtractor(HTMLParser):
    """Pulls visible text out of an HTML fragment, keeping paragraph breaks."""

    _BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "blockquote"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html or "")
    except Exception:
        return ""
    text = "".join(parser.parts)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class _ParagraphExtractor(HTMLParser):
    """Collects the visible text of each <p> element, in document order."""

    def __init__(self) -> None:
        super().__init__()
        self._depth = 0
        self._buf: list[str] = []
        self.paragraphs: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() == "p":
            self._depth += 1
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "p" and self._depth:
            self._depth -= 1
            txt = "".join(self._buf).strip()
            if txt:
                self.paragraphs.append(txt)

    def handle_data(self, data: str) -> None:
        if self._depth:
            self._buf.append(data)


def paragraphs_from_html(html: str) -> list[str]:
    """Extract the <p> paragraphs from an HTML fragment, whitespace-normalised.

    NOS feed descriptions contain the full article split into <p> paragraphs
    (verified: 100% coverage of the article body), giving us real paragraph
    boundaries for the paragraph-by-paragraph translation.
    """
    parser = _ParagraphExtractor()
    try:
        parser.feed(html or "")
    except Exception:
        return []
    return [re.sub(r"\s+", " ", p).strip() for p in parser.paragraphs if p.strip()]


def _get(url: str, timeout: int = 30) -> str:
    """Fetch a URL as text, transparently handling gzip and redirects."""
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=timeout) as resp:  # noqa: S310 (NOS is https/trusted)
        raw = resp.read()
        if resp.headers.get("Content-Encoding", "").lower() == "gzip":
            raw = gzip.decompress(raw)
        charset = resp.headers.get_content_charset() or "utf-8"
        return raw.decode(charset, errors="replace")


@dataclass
class FeedItem:
    guid: str
    title: str
    url: str
    published: Optional[datetime]
    summary_html: str


@dataclass
class Article:
    guid: str
    title: str
    url: str
    published: Optional[datetime]
    summary: str
    body: str
    paragraphs: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        """Full article text; paragraphs preserved when available."""
        if self.paragraphs:
            return "\n\n".join(self.paragraphs)
        return self.body or self.summary


def parse_feed(xml_text: str) -> list[FeedItem]:
    """Parse RSS 2.0 XML into FeedItems (elements have no namespace in NOS feeds)."""
    root = ET.fromstring(xml_text)
    items: list[FeedItem] = []
    for item in root.iter("item"):

        def _child(tag: str) -> str:
            el = item.find(tag)
            return (el.text or "").strip() if el is not None else ""

        title = _child("title")
        link = _child("link")
        guid = _child("guid") or link
        if not title or not link:
            continue
        published: Optional[datetime] = None
        pub_raw = _child("pubDate")
        if pub_raw:
            try:
                published = parsedate_to_datetime(pub_raw)
            except (TypeError, ValueError):
                published = None
        items.append(
            FeedItem(
                guid=guid,
                title=title,
                url=link,
                published=published,
                summary_html=_child("description"),
            )
        )
    return items


def fetch_feed(cfg: Config) -> list[FeedItem]:
    xml_text = _get(cfg.nos_feed_url, cfg.http_timeout)
    try:
        return parse_feed(xml_text)
    except ET.ParseError as exc:  # pragma: no cover - defensive
        raise RuntimeError(f"Could not parse NOS feed: {exc}") from exc


# --------------------------------------------------------------------------- #
# Full-article extraction from the JSON-LD embedded in nos.nl article pages    #
# --------------------------------------------------------------------------- #


def _jsonld_blocks(html_text: str) -> list[dict]:
    blocks: list[dict] = []
    pattern = re.compile(
        r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(html_text):
        raw = match.group(1).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            try:  # tolerate leading garbage by slicing to the first JSON value
                data = json.loads(raw[raw.index("{"):])
            except (json.JSONDecodeError, ValueError):
                continue
        blocks.append(data)
    return blocks


def _find_news_article(o) -> Optional[dict]:
    """Recursively find a dict describing a NewsArticle that has an articleBody."""
    if isinstance(o, dict):
        kind = o.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if "NewsArticle" in kinds and o.get("articleBody"):
            return o
        for value in o.values():
            found = _find_news_article(value)
            if found is not None:
                return found
    elif isinstance(o, list):
        for value in o:
            found = _find_news_article(value)
            if found is not None:
                return found
    return None


def _first_news_article(blocks: list[dict]) -> Optional[dict]:
    for block in blocks:
        found = _find_news_article(block)
        if found is not None:
            return found
    return None


def fetch_article_body(url: str, timeout: int = 30) -> str:
    """Return the plain-text body of a NOS article, or '' if it cannot be found."""
    html_text = _get(url, timeout)
    article = _first_news_article(_jsonld_blocks(html_text))
    if article is None:
        return ""
    return html_to_text(article.get("articleBody") or "")


def build_article(cfg: Config, item: FeedItem) -> Article:
    """Build an Article, keeping real <p> paragraphs from the feed description.

    NOS feed descriptions carry the full article as <p> paragraphs, so we use
    them for paragraph-by-paragraph translations. The JSON-LD body is still
    fetched as an authoritative fallback.
    """
    body = ""
    try:
        body = fetch_article_body(item.url, cfg.http_timeout)
    except Exception:  # noqa: BLE001 - network hiccups should not kill the run
        body = ""
    paragraphs = paragraphs_from_html(item.summary_html)
    if not paragraphs:
        fallback = body or html_to_text(item.summary_html)
        paragraphs = [ln for ln in fallback.splitlines() if ln.strip()]
        if not paragraphs and fallback:
            paragraphs = [fallback]
    return Article(
        guid=item.guid,
        title=item.title,
        url=item.url,
        published=item.published,
        summary=html_to_text(item.summary_html),
        body=body,
        paragraphs=paragraphs,
    )
