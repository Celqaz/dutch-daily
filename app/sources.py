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
import random
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

# How many candidates we are willing to build before giving up on the length
# check (each build may fetch the article page for the languages that need it).
_MAX_CANDIDATE_TRIES = 8


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


def _is_excluded(profile: LanguageProfile, item: web.FeedItem) -> bool:
    """True for known non-articles: daily digests and podcast teasers."""
    title = item.title.strip().lower()
    return any(
        title.startswith(prefix.lower()) for prefix in profile.exclude_title_prefixes
    )


def eligible_items(
    profile: LanguageProfile, cfg: Config, items: list[web.FeedItem], state: State
) -> list[web.FeedItem]:
    """Feed items that could be sent: not already delivered, not a digest.

    The whole feed window counts as the candidate pool (NOS: ~20 items spanning
    ~13 hours), so a normal run chooses from ~20 articles instead of only the
    newest one. With ``ALLOW_REPEATS`` the already-sent items are allowed back in.
    """
    unseen = [i for i in items if not state.is_seen(profile.code, i.guid)]
    if not unseen:
        unseen = list(items) if cfg.allow_repeats else []
    return [i for i in unseen if not _is_excluded(profile, i)]


def pick_article(
    profile: LanguageProfile, cfg: Config, items: list[web.FeedItem], state: State
) -> Optional[web.Article]:
    """Pick an article we have not sent yet, out of the whole feed window.

    Digest-like items (NOS's morning "Wekdienst" briefing, "Podcast De Dag") are
    dropped first. A profile with ``pick_random`` then draws one of the remaining
    candidates at random, so consecutive days differ; otherwise the newest
    eligible item is used.

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
    pool = eligible_items(profile, cfg, items, state)
    if not pool:
        # Every candidate looked like a digest - a briefing still beats nothing.
        log.info(
            "[%s] All %d candidate(s) were digests; falling back to the whole feed.",
            profile.code,
            len(unseen),
        )
        pool = list(unseen or items)
    if profile.pick_random:
        random.shuffle(pool)
    log.info(
        "[%s] %d candidate(s) of %d feed item(s); %s.",
        profile.code,
        len(pool),
        len(items),
        "picking one at random" if profile.pick_random else "taking the newest eligible",
    )
    for item in pool[:_MAX_CANDIDATE_TRIES]:
        article = build_article(profile, cfg, item)
        if len(article.text) >= profile.min_article_chars:
            return article
        log.info(
            "[%s] Skipping '%s' (only %d chars of text).",
            profile.code,
            item.title,
            len(article.text),
        )
    # Last resort: whatever the first eligible item gives us.
    return build_article(profile, cfg, pool[0])


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

    NOS feed descriptions carry the full article as <p> paragraphs (verified
    identical to the article page's JSON-LD ``articleBody``), so the page is
    only fetched when the description turns out to be unusable - which keeps
    scanning a whole window of candidates cheap.
    """
    paragraphs = web.paragraphs_from_html(item.summary_html)
    body = ""
    if not paragraphs:
        try:
            page = web.get(item.url, cfg.http_timeout)
            body = web.jsonld_article_body(page)
        except Exception:  # noqa: BLE001 - network hiccups must not kill the run
            body = ""
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
