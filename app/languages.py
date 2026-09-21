"""Language profiles: everything that differs between Dutch and Japanese.

A profile bundles the feed defaults, the LLM prompt/schema, the JSON field names
the model is asked to use, the section headings, and the size limits. Adding a
new language means adding one profile here plus one source builder in
``app/sources.py``.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import prompts


@dataclass(frozen=True)
class LanguageProfile:
    code: str  # short id, also the HTML anchor prefix ("nl", "ja")
    name: str  # English name, used in prompts and the TOC
    native_name: str  # shown in the document heading
    source_name: str  # where the article comes from ("NOS.nl", "NHK")
    default_feed_url: str
    system_prompt: str
    output_schema: str
    # --- JSON field names the model must use (canonicalised in lesson.py) ---
    para_text: str = "text"
    para_reading: str = ""
    vocab_term: str = "term"
    grammar_example: str = "example_text"
    grammar_example_reading: str = ""
    word_order_text: str = "text"
    more_example: str = "text"
    more_example_reading: str = ""
    word_building_reading: str = ""
    # --- Headings ---
    article_heading: str = "The article, paragraph by paragraph"
    vocab_heading: str = "Key Vocabulary"
    grammar_heading: str = "Key Grammar Points"
    word_building_heading: str = "Word building"
    # --- Limits / thresholds ---
    min_article_chars: int = 200  # retry the next feed item below this
    max_grammar_points: int = 5
    max_word_building: int = 5


DUTCH = LanguageProfile(
    code="nl",
    name="Dutch",
    native_name="Nederlands",
    source_name="NOS.nl",
    default_feed_url="https://feeds.nos.nl/nosnieuwsalgemeen",
    system_prompt=prompts.DUTCH_SYSTEM_PROMPT,
    output_schema=prompts.DUTCH_OUTPUT_SCHEMA,
    para_text="dutch",
    vocab_term="dutch",
    grammar_example="example_nl",
    word_order_text="dutch",
    more_example="nl",
    article_heading="The article - Dutch and English, paragraph by paragraph",
    word_building_heading="Word building - words from today's article",
    min_article_chars=200,
    max_grammar_points=5,
    max_word_building=5,
)

JAPANESE = LanguageProfile(
    code="ja",
    name="Japanese",
    native_name="\u65e5\u672c\u8a9e",
    source_name="NHK News Web Easy",
    default_feed_url="https://nhkeasier.com/feed/",
    system_prompt=prompts.JAPANESE_SYSTEM_PROMPT,
    output_schema=prompts.JAPANESE_OUTPUT_SCHEMA,
    para_text="japanese",
    para_reading="reading",
    vocab_term="japanese",
    grammar_example="example_ja",
    grammar_example_reading="example_reading",
    word_order_text="japanese",
    more_example="ja",
    more_example_reading="reading",
    word_building_reading="reading",
    article_heading="The article - Japanese and English, paragraph by paragraph",
    word_building_heading="Word building - kanji in today's article",
    min_article_chars=80,  # Japanese packs more meaning per character
    max_grammar_points=4,
    max_word_building=4,
)

PROFILES: dict[str, LanguageProfile] = {p.code: p for p in (DUTCH, JAPANESE)}

AVAILABLE = ", ".join(PROFILES)
DEFAULT_LANGUAGE = DUTCH.code


class UnknownLanguage(ValueError):
    """Raised when a configured language code has no profile."""


def get_profile(code: str) -> LanguageProfile:
    try:
        return PROFILES[code]
    except KeyError:
        raise UnknownLanguage(
            f"Unknown language '{code}'. Available languages: {AVAILABLE}."
        ) from None


def parse_languages(raw: str | list[str] | None) -> list[str]:
    """Parse 'nl,ja' (or a list) into validated, de-duplicated language codes."""
    if raw is None:
        return [DEFAULT_LANGUAGE]
    parts = raw if isinstance(raw, list) else str(raw).split(",")
    codes: list[str] = []
    for part in parts:
        code = str(part).strip().lower()
        if not code:
            continue
        get_profile(code)  # raises UnknownLanguage with a friendly message
        if code not in codes:
            codes.append(code)
    if not codes:
        raise UnknownLanguage(f"No languages configured. Available: {AVAILABLE}.")
    return codes
