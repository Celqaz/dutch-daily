"""Generate a beginner Dutch lesson from an article using Anthropic Claude."""
from __future__ import annotations

import json
import re
from typing import Any

from .config import Config
from .nos import Article

SYSTEM_PROMPT = """\
You are an expert tutor of Dutch as a foreign language. Your student is a TOTAL
BEGINNER (CEFR A1) whose native language is English. Each day you receive one real,
short Dutch news article from NOS.nl. Turn it into a structured, friendly,
self-study lesson in English with this layout, in order (learn words and
grammar first, read the article last):
1) Key Vocabulary   2) Grammar Points   3) Word building   4) Article paragraphs.

Rules:
- NEVER use emoji or decorative symbols in any field. Plain text only (Dutch
  accented letters are fine). No tables or columns - just text.
- Beginner tone: simple, warm, concrete English. If you use a grammar term,
  explain it in one plain-English phrase.
- Ground EVERYTHING in the article: quote real Dutch from the text in examples;
  never invent example sentences or facts about the story.
- ARTICLE PARAGRAPHS (reading practice, goes LAST in the document): the article
  text below is split into paragraphs, each marked [PARAGRAPH n]. Output EVERY
  paragraph in order: article_paragraphs[].dutch must be the VERBATIM Dutch
  paragraph, and article_paragraphs[].english its natural English translation.
- KEY VOCABULARY = the 8-12 most useful words/phrases from the article. In each
  NOTES value: give the article (de/het/een) for nouns and the infinitive for
  verbs, plus one short hint (a cognate, a word-part, or 'very common').
- GRAMMAR POINTS = exactly 3 to 5 points chosen from grammar that is BOTH visible
  in this article AND appropriate for an A1 learner (e.g. de/het, present tense,
  word order after a time word, separable verbs, plurals, common prepositions,
  inversion after an opener like 'zo' or a time phrase, negation with niet/geen,
  adjectives, the 'om ... te' purpose structure, diminutives). For EACH point:
    * title = a short name of the idea
    * example_nl = ONE real sentence quoted from the article
    * example_en = its English translation
    * explanation_en = a simple beginner explanation of how the pattern works
    * word_order = optional 1-6 slots showing how the example sentence is built
      (e.g. [{"slot":"Position 1","dutch":"Zo"},{"slot":"Position 2","dutch":"tikte"}])
    * more_examples = 0-3 short related examples (nl + en)
    * tip_en = a one-line memory hook (no emoji)
- WORD BUILDING = 2-5 longer words from the article split into parts, each with a
  gloss of the whole (e.g. uitleggen = uit + leggen, "out-lay").
- Keep the story faithful to the article. Do not add facts.
- Output ONLY a single valid JSON object matching the schema below. No markdown
  fences, no commentary, no trailing text.
"""

OUTPUT_SCHEMA = """\
{
  "article_paragraphs": [
    {"dutch": "verbatim Dutch paragraph from the article", "english": "its natural English translation"}
  ],
  "key_vocabulary": [
    {"dutch": "word or phrase as it appears", "english": "English meaning", "notes": "de/het/een + noun / infinitive for verbs / one short hint"}
  ],
  "grammar_points": [
    {
      "title": "short name of the grammar idea",
      "example_nl": "one real Dutch sentence quoted from the article",
      "example_en": "English translation of that sentence",
      "explanation_en": "simple beginner (A1) explanation of the pattern",
      "word_order": [{"slot": "Position 1", "dutch": "..."}, {"slot": "Position 2", "dutch": "..."}],
      "more_examples": [{"nl": "short Dutch example", "en": "English"}],
      "tip_en": "one-line memory hook"
    }
  ],
  "word_building": [
    {"word": "long word", "parts": "part1 + part2", "english": "meaning of the whole word"}
  ]
}"""


class LessonError(RuntimeError):
    pass


def _first_json_block(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LessonError("Model did not return a JSON object.")
    return text[start : end + 1]


def _numbered_paragraphs(paragraphs: list[str], max_chars: int) -> str:
    """Turn real article paragraphs into [PARAGRAPH n] blocks, bounded to max_chars."""
    result: list[str] = []
    used = 0
    for i, raw in enumerate(paragraphs, 1):
        para = re.sub(r"\s+", " ", raw).strip()
        if not para:
            continue
        if used + len(para) > max_chars:
            remaining = max_chars - used
            if remaining < 60:
                break
            para = para[:remaining].rsplit(" ", 1)[0].rstrip() + " …"
        used += len(para)
        result.append(f"[PARAGRAPH {i}]\n{para}")
        if used >= max_chars:
            break
    return "\n\n".join(result)


def _trim_article(article: Article, max_chars: int) -> str:
    text = article.text.strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    # Try not to slice mid-word.
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut + " …"


def _extract_text(content) -> str:
    """Join any 'text' content blocks, tolerating dict- or object-style blocks.

    Newer anthropic SDKs / DeepSeek's shim may represent content blocks either as
    objects (block.type == 'text', block.text) or as plain dicts. Thinking blocks
    store their prose under 'thinking', not 'text', so they are naturally skipped.
    """
    parts: list[str] = []
    for block in content or []:
        if isinstance(block, dict):
            text = block.get("text")
        else:
            text = getattr(block, "text", None)
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n".join(parts)


def build_lesson(article: Article, cfg: Config) -> dict[str, Any]:
    """Ask Claude for a structured lesson about this article and return it as a dict."""
    if not cfg.anthropic_api_key:
        raise LessonError(
            "ANTHROPIC_API_KEY is not set — add it to .env (see README)."
        )

    import anthropic  # lazy import so feed/scrape can run without the SDK

    # DeepSeek (and other providers) expose an Anthropic-compatible API; if
    # ANTHROPIC_BASE_URL is set we simply point the official SDK at it.
    # A bounded timeout stops a stalled provider from hanging the daily job
    # (the SDK default is 10 minutes).
    client_kwargs = {"api_key": cfg.anthropic_api_key, "timeout": 240.0}
    if cfg.anthropic_base_url:
        client_kwargs["base_url"] = cfg.anthropic_base_url
    client = anthropic.Anthropic(**client_kwargs)

    paragraphs = article.paragraphs or [article.text]
    article_block = _numbered_paragraphs(paragraphs, cfg.max_article_chars)
    user_content = (
        "Please write the lesson for this NOS article.\n\n"
        f"PUBLISHED: {article.published or 'unknown'}\n"
        f"DUTCH HEADLINE: {article.title}\n"
        f"SOURCE URL: {article.url}\n\n"
        "DUTCH ARTICLE TEXT - paragraphs are marked [PARAGRAPH n]; copy each "
        "paragraph VERBATIM into article_paragraphs[].dutch, in order:\n"
        f"{article_block}\n\n"
        "Return exactly this JSON structure:\n"
        f"{OUTPUT_SCHEMA}"
    )

    try:
        # Newer anthropic SDKs (1.x) removed the top-level `temperature`
        # parameter from messages.create(), so we rely on the model default.
        #
        # DeepSeek's v4 models are "thinking" models that reason by default on
        # this endpoint. That is slow and can eat the whole token budget,
        # producing NO text block at all. Disabling thinking keeps responses
        # fast (~15-20s) with clean text. Real Anthropic rejects
        # {"type": "disabled"}, so only send it when pointed at DeepSeek.
        request_kwargs: dict = {
            "model": cfg.claude_model,
            "max_tokens": 8192,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_content}],
        }
        if "deepseek" in cfg.anthropic_base_url.lower():
            request_kwargs["thinking"] = {"type": "disabled"}
        response = client.messages.create(**request_kwargs)
    except Exception as exc:  # noqa: BLE001
        raise LessonError(f"Anthropic API call failed: {exc}") from exc

    raw_text = _extract_text(response.content).strip()
    if not raw_text:
        raise LessonError("Anthropic returned no text content.")

    try:
        lesson = json.loads(_first_json_block(raw_text))
    except json.JSONDecodeError as exc:
        raise LessonError(f"Could not parse lesson JSON from model: {exc}") from exc

    if not isinstance(lesson, dict):
        raise LessonError("Expected a JSON object lesson, got something else.")

    lesson.setdefault("article_paragraphs", [])
    lesson.setdefault("key_vocabulary", [])
    lesson.setdefault("grammar_points", [])
    lesson.setdefault("word_building", [])

    # Hard caps to keep the Kindle document digestible.
    if len(lesson["key_vocabulary"]) > cfg.max_vocab:
        lesson["key_vocabulary"] = lesson["key_vocabulary"][: cfg.max_vocab]
    if len(lesson["grammar_points"]) > 5:
        lesson["grammar_points"] = lesson["grammar_points"][:5]
    if len(lesson["word_building"]) > 5:
        lesson["word_building"] = lesson["word_building"][:5]

    return lesson
