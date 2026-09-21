"""Shared HTTP, HTML and RSS plumbing used by every language source.

Nothing in here is language-specific: fetching a feed, turning HTML into plain
text, finding real paragraph boundaries on a news page, and reading article
bodies out of JSON-LD. Per-language quirks live in ``app/sources.py``.
"""
from __future__ import annotations

import gzip
import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Iterator, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 LanguageDaily/1.0"
)

# Kana, kanji, CJK punctuation and full-width forms: used to detect text that
# was wrapped mid-sentence by the site, where joining must not add a space.
CJK = r"\u3001-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef"

# Elements whose text must never reach a lesson (furigana readings included).
SKIP_TAGS = frozenset({"script", "style", "noscript", "rt", "rp"})

# Elements that imply a line break when flattening HTML into text.
BLOCK_TAGS = frozenset(
    {
        "p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6",
        "blockquote", "section", "article", "tr", "figcaption", "dd", "dt",
    }
)

# Elements that can wrap the article body (used to group <p> elements).
CONTAINER_TAGS = frozenset({"div", "section", "article", "main", "body", "td"})

# Containers whose text is never the article body.
_BOILERPLATE = (
    "nav", "footer", "header", "aside", "side", "related", "recommend",
    "ranking", "popular", "advert", "banner", "share", "comment", "menu",
    "sns", "breadcrumb", "pickup", "toolbar", "promo",
)


# --------------------------------------------------------------------------- #
# HTTP                                                                         #
# --------------------------------------------------------------------------- #


def get(url: str, timeout: int = 30, retries: int = 3) -> str:
    """Fetch a URL as text, handling gzip/redirects, retrying transient failures."""
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = Request(url, headers={"User-Agent": UA})
            with urlopen(req, timeout=timeout) as resp:  # noqa: S310 (https/trusted)
                raw = resp.read()
                if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                charset = resp.headers.get_content_charset() or "utf-8"
                return raw.decode(charset, errors="replace")
        except (URLError, OSError) as exc:
            # Covers DNS failures (gaierror), timeouts, connection resets.
            last_error = exc
            if attempt < retries:
                time.sleep(2 ** attempt)  # 2s, then 4s
    raise last_error if last_error else RuntimeError("request failed")


# --------------------------------------------------------------------------- #
# Text / HTML                                                                  #
# --------------------------------------------------------------------------- #


def normalize_text(text: str) -> str:
    """Normalise whitespace without damaging CJK text.

    Japanese pages often wrap a sentence across source lines; joining those
    lines must not insert a space, while Latin text keeps its word spacing.
    """
    if not text:
        return ""
    text = text.replace("\u3000", " ")
    text = re.sub(rf"(?<=[{CJK}])[ \t]*\n[ \t]*(?=[{CJK}])", "", text)
    text = re.sub(rf"(?<=[{CJK}])[ \t]+(?=[{CJK}])", "", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class _TextParser(HTMLParser):
    """Pulls visible text out of an HTML fragment, keeping paragraph breaks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS:
            self._skip += 1
            return
        if not self._skip and tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag.lower() in SKIP_TAGS or self._skip:
            return
        if tag.lower() in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if not self._skip and tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Flatten an HTML fragment into plain text, preserving paragraph breaks."""
    parser = _TextParser()
    try:
        parser.feed(html or "")
    except Exception:  # noqa: BLE001 - malformed markup must not kill a run
        return ""
    return normalize_text("".join(parser.parts))


class _ParagraphParser(HTMLParser):
    """Collects the visible text of each <p> element, in document order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._depth = 0
        self._buf: list[str] = []
        self._skip = 0
        self.paragraphs: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "p":
            self._depth += 1
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "p" and self._depth:
            self._depth -= 1
            text = normalize_text("".join(self._buf))
            if text:
                self.paragraphs.append(text)

    def handle_data(self, data: str) -> None:
        if self._depth and not self._skip:
            self._buf.append(data)


def paragraphs_from_html(html: str) -> list[str]:
    """Extract the <p> paragraphs from an HTML fragment, whitespace-normalised.

    NOS feed descriptions contain the full article split into <p> paragraphs
    (verified: 100% coverage of the article body), giving us real paragraph
    boundaries for the paragraph-by-paragraph translation.
    """
    parser = _ParagraphParser()
    try:
        parser.feed(html or "")
    except Exception:  # noqa: BLE001
        return []
    return parser.paragraphs


class _RubyParagraphParser(HTMLParser):
    """Paragraph text plus a kana-only version built from <ruby><rt> readings.

    nhkeasier.com annotates every kanji with furigana inside the feed itself,
    which gives us authoritative readings for the lesson (e.g. 20日 -> はつか,
    an irregular reading a model would easily get wrong).
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self._depth = 0
        self._base: list[str] = []
        self._kana: list[str] = []
        self._ruby_start: Optional[int] = None
        self._in_rt = False
        self._rt: list[str] = []
        self.paragraphs: list[str] = []
        self.readings: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag == "rt":
            self._in_rt = True
            self._rt = []
            if self._ruby_start is None:
                self._ruby_start = len(self._kana)
            return
        if tag in SKIP_TAGS:  # script, style, noscript, rp
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "p":
            self._depth += 1
            self._base, self._kana = [], []
            self._ruby_start = None
        elif tag == "ruby":
            self._ruby_start = len(self._kana)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "rt":
            if self._in_rt and self._ruby_start is not None:
                # Replace the kanji we collected with its reading.
                del self._kana[self._ruby_start:]
                self._kana.append("".join(self._rt))
            self._in_rt = False
            self._rt = []
            return
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "ruby":
            self._ruby_start = None
        elif tag == "p" and self._depth:
            self._depth -= 1
            base = normalize_text("".join(self._base))
            if base:
                kana = normalize_text("".join(self._kana))
                self.paragraphs.append(base)
                self.readings.append("" if kana == base else kana)

    def handle_data(self, data: str) -> None:
        if self._skip or not self._depth:
            return
        if self._in_rt:
            self._rt.append(data)
            return
        self._base.append(data)
        self._kana.append(data)


def paragraphs_with_readings(html: str) -> tuple[list[str], list[str]]:
    """(<p> paragraphs, kana-only equivalent) from HTML that uses <ruby>/<rt>."""
    parser = _RubyParagraphParser()
    try:
        parser.feed(html or "")
    except Exception:  # noqa: BLE001
        return [], []
    return parser.paragraphs, parser.readings


_AUDIO_RE = re.compile(r"<audio[^>]+src=[\"']([^\"']+)[\"']", re.IGNORECASE)


def first_audio_url(html: str) -> str:
    """First <audio src=...> in a fragment (NHK Easy ships a slow-reading mp3)."""
    match = _AUDIO_RE.search(html or "")
    return match.group(1) if match else ""


def _container_key(tag: str, attrs: Any) -> str:
    attr = {str(k).lower(): (v or "") for k, v in (attrs or [])}
    key = tag
    if attr.get("id"):
        key += "#" + attr["id"]
    classes = attr.get("class", "").split()
    if classes:
        key += "." + ".".join(classes[:3])
    return key


class _BlockParser(HTMLParser):
    """Groups <p> text by the nearest wrapping element.

    News sites wrap the body in one container while sidebars/related links live
    in others, so the group with the most text is a good article-body guess.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[str] = ["body"]
        self._depth = 0
        self._buf: list[str] = []
        self._skip = 0
        self.order: list[str] = []
        self.groups: dict[str, list[str]] = {}

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS:
            self._skip += 1
            return
        if self._skip:
            return
        if tag in CONTAINER_TAGS:
            self._stack.append(_container_key(tag, attrs))
        elif tag == "p":
            self._depth += 1
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "p":
            if self._depth:
                self._depth -= 1
                text = normalize_text("".join(self._buf))
                if text:
                    key = self._stack[-1] if self._stack else "body"
                    if key not in self.groups:
                        self.groups[key] = []
                        self.order.append(key)
                    self.groups[key].append(text)
        elif tag in CONTAINER_TAGS and len(self._stack) > 1:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        if self._depth and not self._skip:
            self._buf.append(data)


def longest_paragraph_block(
    html_text: str, *, hint: str = "", min_total: int = 80, min_avg: float = 12.0
) -> list[str]:
    """Best guess at the article body: the richest group of <p> paragraphs.

    Groups are ranked by size with three corrections that matter in practice:
    link lists of related articles are heavily down-weighted (they are often the
    biggest group on the page), a group that contains ``hint`` (typically the
    page's meta description, i.e. the article lead) is boosted, and mostly-CJK
    text is preferred, which pushes boilerplate such as an English copyright
    line out of the way.

    Returns an empty list when nothing looks like prose, so callers can fall
    back to JSON-LD, the feed description, or ``og:description``.
    """
    parser = _BlockParser()
    try:
        parser.feed(html_text or "")
    except Exception:  # noqa: BLE001
        return []
    hint_prefix = re.sub(r"\s+", "", hint or "")[:40]
    best: list[str] = []
    best_score = 0.0
    for key in parser.order:
        paras = parser.groups.get(key) or []
        if not paras:
            continue
        text = "".join(paras)
        total = len(text)
        matches_hint = bool(hint_prefix) and hint_prefix in re.sub(r"\s+", "", text)
        if not matches_hint and (total < min_total or total / len(paras) < min_avg):
            continue
        score = float(total) * (1.0 + _cjk_ratio(text))
        if matches_hint:
            score *= 3.0
        if any(marker in key.lower() for marker in _BOILERPLATE):
            score *= 0.1
        if _looks_like_link_list(paras):
            score *= 0.15
        if score > best_score:
            best, best_score = paras, score
    return best


_TIMESTAMP_RE = re.compile(r"^\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2}")


def _looks_like_link_list(paragraphs: list[str]) -> bool:
    """True for teaser / related-article lists rather than prose.

    News sites wrap their "related articles" rail in the same kind of container
    as the body, but its paragraphs are short link titles interleaved with
    timestamps. Those are down-weighted so the real body wins.
    """
    if len(paragraphs) < 3:
        return False
    if any(_TIMESTAMP_RE.match(para) for para in paragraphs):
        return True
    short = sum(1 for para in paragraphs if len(para) < 15)
    return short >= max(3, len(paragraphs) * 0.2)


def _cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    cjk = len(re.findall(rf"[{CJK}]", text))
    return cjk / len(text)


_META_PATTERNS = (
    re.compile(
        r'<meta[^>]+(?:property|name)=["\'](?:og:description|description)["\'][^>]*'
        r'content=["\'](.*?)["\']',
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r'<meta[^>]+content=["\'](.*?)["\'][^>]*'
        r'(?:property|name)=["\'](?:og:description|description)["\']',
        re.IGNORECASE | re.DOTALL,
    ),
)


def meta_description(html_text: str) -> str:
    """Return the page's meta/og description as plain text ('' if absent)."""
    for pattern in _META_PATTERNS:
        match = pattern.search(html_text or "")
        if match:
            text = html_to_text(match.group(1))
            if text:
                return text
    return ""


# --------------------------------------------------------------------------- #
# JSON-LD article bodies                                                       #
# --------------------------------------------------------------------------- #


def jsonld_blocks(html_text: str) -> list[Any]:
    blocks: list[Any] = []
    pattern = re.compile(
        r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(html_text or ""):
        raw = match.group(1).strip()
        if not raw:
            continue
        try:
            blocks.append(json.loads(raw))
        except json.JSONDecodeError:
            try:  # tolerate leading garbage by slicing to the first JSON value
                blocks.append(json.loads(raw[raw.index("{"):]))
            except (json.JSONDecodeError, ValueError):
                continue
    return blocks


def _find_article_object(obj: Any) -> Optional[dict]:
    """Recursively find a dict describing an Article that has an articleBody."""
    if isinstance(obj, dict):
        kind = obj.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        has_type = any(isinstance(k, str) and "Article" in k for k in kinds)
        if has_type and obj.get("articleBody"):
            return obj
        for value in obj.values():
            found = _find_article_object(value)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_article_object(value)
            if found is not None:
                return found
    return None


def jsonld_article_body(html_text: str) -> str:
    """Return the plain-text ``articleBody`` from JSON-LD, or '' if not present.

    Note: on nos.nl this body is one continuous string without paragraph marks,
    so real paragraph boundaries have to come from the feed's <p> tags.
    """
    for block in jsonld_blocks(html_text):
        article = _find_article_object(block)
        if article is not None:
            return html_to_text(article.get("articleBody") or "")
    return ""


# --------------------------------------------------------------------------- #
# Feeds                                                                        #
# --------------------------------------------------------------------------- #


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
    # Kana-only version of each paragraph, when the source publishes furigana
    # (nhkeasier.com) - authoritative readings for the lesson.
    paragraph_readings: list[str] = field(default_factory=list)
    # Link to the source audio, when there is one (NHK News Web Easy mp3).
    audio_url: str = ""

    @property
    def text(self) -> str:
        """Full article text; paragraphs preserved when available."""
        if self.paragraphs:
            return "\n\n".join(self.paragraphs)
        return self.body or self.summary


def _child_text(item: ET.Element, tag: str) -> str:
    """Read a child element's text, tolerating namespaced feeds."""
    for path in (tag, f"{{*}}{tag}"):
        el = item.find(path)
        if el is not None and el.text and el.text.strip():
            return el.text.strip()
    return ""


def parse_feed(xml_text: str) -> list[FeedItem]:
    """Parse RSS 2.0 XML into FeedItems (works for NOS and NHK alike)."""
    root = ET.fromstring(xml_text)
    items: list[FeedItem] = []
    for item in root.iter("item"):
        title = _child_text(item, "title")
        link = _child_text(item, "link")
        guid = _child_text(item, "guid") or link
        if not title or not link:
            continue
        published: Optional[datetime] = None
        pub_raw = _child_text(item, "pubDate")
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
                summary_html=_child_text(item, "description"),
            )
        )
    return items


def fetch_feed(url: str, timeout: int = 30) -> list[FeedItem]:
    xml_text = get(url, timeout)
    try:
        return parse_feed(xml_text)
    except ET.ParseError as exc:
        raise RuntimeError(f"Could not parse feed {url}: {exc}") from exc


def iter_paragraph_lines(text: str) -> Iterator[str]:
    """Yield the non-empty lines of an already-flattened text block."""
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            yield stripped
