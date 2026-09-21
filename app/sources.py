"""Feed + full-text extraction, per language.

Two sources are supported:

* ``nl`` - NOS.nl. The RSS ``description`` already carries the full article as
  real ``<p>`` paragraphs (verified 100% coverage), so that is the primary
  source; the JSON-LD ``articleBody`` on the article page is the fallback.
* ``ja`` - nhkeasier.com, a mirror of NHK News Web Easy. Its RSS description
  already carries the complete easy-Japanese article (with furigana), so the
  article page is only fetched when the description turns out to be a teaser; in
  that case the body is recovered from the page's paragraph DOM, falling back to
  JSON-LD and finally to ``og:description``.

Both ultimately produce the same :class:`app.web.Article`.
"""
from __future__ import annotations

import logging
from typing import Optional

from . import web
from .config import Config
from .languages import LanguageProfile
from .state import State

log = logging.getLogger("langdaily.sources")

# "See the rest of this post" links that some feeds append to the description.
_TEASER_TRAILERS = ("続きをみる", "続きを読む", "Read more", "Lees verder")

# A description shorter than this is treated as a teaser, not the article.
_MIN_COMPLETE_DESCRIPTION_CHARS = 150


def _strip_trailers(text: str) -> str:
    for marker in _TEASER_TRAILERS:
        index = text.find(marker)
        if index > 0:
            text = text[:index]
    return text.strip()


def fetch_items(profile: LanguageProfile, cfg: Config) -> list[web.FeedItem]:
    """Fetch and parse the RSS feed for this language."""
    url = cfg.feed_url_for(profile.code, profile.default_feed_url)
    items = web.fetch_feed(url, cfg.http_timeout)
    log.info("[%s] %d items in %s", profile.code, len(items), url)
    return items


def pick_article(
    profile: LanguageProfile, cfg: Config, items: list[web.FeedItem], state: State
) -> Optional[web.Article]:
    """Pick the newest article we have not sent yet.

    Returns ``None`` when every item has already been delivered and repeats are
    not allowed, so a slow feed yields a "nothing new today" note instead of
    resending yesterday's text.
    """
    unseen = [i for i in items if not state.is_seen(profile.code, i.guid)]
    if not unseen and not cfg.allow_repeats:
        log.warning(
            "[%s] All %d feed items have already been sent; no new article today.",
            profile.code,
            len(items),
        )
        return None
    candidates = unseen or items
    for item in candidates[:6]:
        article = build_article(profile, cfg, item)
        if len(article.text) >= profile.min_article_chars:
            return article
        log.info(
            "[%s] Skipping '%s' (only %d chars of text).",
            profile.code,
            item.title,
            len(article.text),
        )
    # Last resort: whatever the newest item gives us.
    return build_article(profile, cfg, candidates[0])


def build_article(
    profile: LanguageProfile, cfg: Config, item: web.FeedItem
) -> web.Article:
    """Build a full Article for a feed item, using the source for this language."""
    if profile.code == "ja":
        return _build_ja_article(cfg, item)
    return _build_nos_article(cfg, item)


# --------------------------------------------------------------------------- #
# NOS.nl (Dutch)                                                               #
# --------------------------------------------------------------------------- #


def _build_nos_article(cfg: Config, item: web.FeedItem) -> web.Article:
    """Build an Article, keeping real <p> paragraphs from the feed description.

    NOS feed descriptions carry the full article as <p> paragraphs, so we use
    them for paragraph-by-paragraph translations. The JSON-LD body is still
    fetched as an authoritative fallback.
    """
    body = ""
    try:
        page = web.get(item.url, cfg.http_timeout)
        body = web.jsonld_article_body(page)
    except Exception:  # noqa: BLE001 - network hiccups should not kill the run
        body = ""
    paragraphs = web.paragraphs_from_html(item.summary_html)
    if not paragraphs:
        fallback = body or web.html_to_text(item.summary_html)
        paragraphs = [ln for ln in fallback.splitlines() if ln.strip()]
        if not paragraphs and fallback:
            paragraphs = [fallback]
    return web.Article(
        guid=item.guid,
        title=item.title,
        url=item.url,
        published=item.published,
        summary=web.html_to_text(item.summary_html),
        body=body,
        paragraphs=paragraphs,
    )


# --------------------------------------------------------------------------- #
# Japanese (NHK news by default; any RSS with a full-text page works)           #
# --------------------------------------------------------------------------- #


def _build_ja_article(cfg: Config, item: web.FeedItem) -> web.Article:
    """Build the Japanese article, preferring the feed's own text.

    nhkeasier.com already ships the whole article in ``<description>`` - with
    furigana for every kanji and an audio file - so when the description looks
    complete there is no need to fetch (or scrape) the page at all.

    Otherwise the story page is fetched and the body is recovered from its
    paragraph DOM, falling back to JSON-LD and finally to ``og:description``.
    """
    paragraphs, readings = web.paragraphs_with_readings(item.summary_html)
    if _description_is_complete(item, paragraphs):
        log.info(
            "[ja] Using %d paragraph(s) from the feed description (%d with furigana).",
            len(paragraphs),
            sum(1 for r in readings if r),
        )
        return _article_from(item, paragraphs, readings)

    page = ""
    try:
        page = web.get(item.url, cfg.http_timeout)
    except Exception as exc:  # noqa: BLE001 - degrade to the feed description
        log.warning("[ja] Could not fetch %s (%s).", item.url, exc)

    if page:
        paragraphs = web.longest_paragraph_block(page, hint=web.meta_description(page))
        if paragraphs:
            log.info("[ja] Using %d paragraph(s) from the page DOM.", len(paragraphs))
        else:
            body = web.jsonld_article_body(page)
            if body:
                paragraphs = list(web.iter_paragraph_lines(body))
                log.info("[ja] Using JSON-LD articleBody (%d paragraph(s)).", len(paragraphs))

    if not paragraphs:
        paragraphs = _description_paragraphs(item)
        if paragraphs:
            log.info("[ja] Falling back to the feed description.")
    if not paragraphs and page:
        description = web.meta_description(page)
        if description:
            paragraphs = [description]
            log.info("[ja] Falling back to the page's meta description.")

    if not paragraphs:
        log.warning("[ja] No article text found for %s.", item.url)

    return _article_from(item, paragraphs)


def _article_from(
    item: web.FeedItem, paragraphs: list[str], readings: list[str] | None = None
) -> web.Article:
    return web.Article(
        guid=item.guid,
        title=item.title,
        url=item.url,
        published=item.published,
        summary=_strip_trailers(web.html_to_text(item.summary_html)),
        body="\n\n".join(paragraphs),
        paragraphs=paragraphs,
        paragraph_readings=readings or [],
        audio_url=web.first_audio_url(item.summary_html),
    )


def _description_is_complete(item: web.FeedItem, paragraphs: list[str]) -> bool:
    """True when the feed description looks like the whole article.

    nhkeasier.com ships the complete text in the description, but a feed that
    only carries a teaser either appends a "read more" link or stops
    mid-sentence, and those need the story page to be fetched.
    """
    if len(paragraphs) == 0:
        return False
    total = sum(len(p) for p in paragraphs)
    if total < _MIN_COMPLETE_DESCRIPTION_CHARS:
        return False
    if any(marker in item.summary_html for marker in _TEASER_TRAILERS):
        return False
    return paragraphs[-1].rstrip().endswith(("。", "！", "？", ".", "!", "?"))


def _description_paragraphs(item: web.FeedItem) -> list[str]:
    """Paragraphs from the feed description, with any 'read more' link removed."""
    cleaned = _strip_trailers(web.html_to_text(item.summary_html))
    if not cleaned:
        return []
    return [line for line in cleaned.splitlines() if line.strip()]
