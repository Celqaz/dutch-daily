"""Generate a beginner lesson from an article, for any supported language.

The prompt and the JSON field names come from a :class:`LanguageProfile`; the
model's answer is normalised into one canonical shape so the renderer never has
to know which language it is looking at.
"""
from __future__ import annotations

import json
import re
from typing import Any

from .config import Config
from .languages import LanguageProfile
from .web import Article


class LessonError(RuntimeError):
    pass


def _first_json_block(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LessonError("Model did not return a JSON object.")
    return text[start : end + 1]


def _numbered_paragraphs(
    paragraphs: list[str], max_chars: int, readings: list[str] | None = None
) -> str:
    """Turn real article paragraphs into [PARAGRAPH n] blocks, bounded to max_chars.

    When the source publishes furigana, the kana reading is added as a
    ``[READING]`` line so the model reuses it instead of guessing kanji readings.
    """
    result: list[str] = []
    used = 0
    for i, raw in enumerate(paragraphs, 1):
        para = re.sub(r"\s+", " ", raw).strip()
        if not para:
            continue
        truncated = False
        if used + len(para) > max_chars:
            remaining = max_chars - used
            if remaining < 60:
                break
            para = para[:remaining].rsplit(" ", 1)[0].rstrip() + " …"
            truncated = True
        used += len(para)
        block = f"[PARAGRAPH {i}]\n{para}"
        reading = ""
        if readings and i - 1 < len(readings):
            reading = readings[i - 1]
        if reading and not truncated and reading != para:
            block += f"\n[READING] {reading}"
        result.append(block)
        if used >= max_chars:
            break
    return "\n\n".join(result)


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


def build_lesson(
    article: Article, cfg: Config, profile: LanguageProfile
) -> dict[str, Any]:
    """Ask the LLM for a structured lesson about this article, normalised for rendering."""
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

    max_chars, _ = cfg.limits_for(profile.code)
    paragraphs = article.paragraphs or [article.text]
    readings = (
        article.paragraph_readings
        if len(article.paragraph_readings) == len(paragraphs)
        else []
    )
    article_block = _numbered_paragraphs(paragraphs, max_chars, readings)
    reading_note = (
        "[READING] lines are the official kana readings from the source - use "
        "them verbatim for that paragraph.\n"
        if any(readings)
        else ""
    )
    language = profile.name.upper()
    user_content = (
        f"Please write the lesson for this {profile.source_name} article.\n\n"
        f"PUBLISHED: {article.published or 'unknown'}\n"
        f"{language} HEADLINE: {article.title}\n"
        f"SOURCE URL: {article.url}\n\n"
        f"{language} ARTICLE TEXT - paragraphs are marked [PARAGRAPH n]; copy each "
        f"paragraph VERBATIM into article_paragraphs[].{profile.para_text}, in order:\n"
        f"{article_block}\n\n"
        f"{reading_note}"
        "Return exactly this JSON structure:\n"
        f"{profile.output_schema}"
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
            "system": profile.system_prompt,
            "messages": [{"role": "user", "content": user_content}],
        }
        if "deepseek" in cfg.anthropic_base_url.lower():
            request_kwargs["thinking"] = {"type": "disabled"}
        response = client.messages.create(**request_kwargs)
    except Exception as exc:  # noqa: BLE001
        raise LessonError(
            f"LLM API call failed for {profile.name} ({cfg.claude_model}): {exc}"
        ) from exc

    raw_text = _extract_text(response.content).strip()
    if not raw_text:
        raise LessonError(f"The model returned no text content for {profile.name}.")

    try:
        lesson = json.loads(_first_json_block(raw_text))
    except json.JSONDecodeError as exc:
        raise LessonError(f"Could not parse lesson JSON from model: {exc}") from exc

    if not isinstance(lesson, dict):
        raise LessonError("Expected a JSON object lesson, got something else.")

    normalized = _normalize(lesson, profile, cfg)
    _apply_source_readings(normalized, article)
    return normalized


def _apply_source_readings(lesson: dict[str, Any], article: Article) -> None:
    """Prefer the source's own furigana over the model's reading, when aligned."""
    readings = article.paragraph_readings
    pairs = lesson.get("article_paragraphs") or []
    if not any(readings) or len(readings) != len(pairs):
        return
    for pair, reading in zip(pairs, readings):
        if reading:
            pair["reading"] = reading


# --------------------------------------------------------------------------- #
# Normalisation: model JSON -> one canonical shape for the renderer           #
# --------------------------------------------------------------------------- #


def _text(obj: Any, *keys: str) -> str:
    """First non-empty string among ``keys`` (missing keys are fine)."""
    if not isinstance(obj, dict):
        return ""
    for key in keys:
        if not key:
            continue
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)):
            return str(value)
    return ""


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _normalize_paragraphs(lesson: dict, profile: LanguageProfile) -> list[dict]:
    pairs: list[dict] = []
    for raw in _as_list(lesson.get("article_paragraphs")):
        if not isinstance(raw, dict):
            continue
        text = _text(raw, profile.para_text, "text", "source")
        if not text:
            continue
        pairs.append(
            {
                "text": text,
                "reading": _text(raw, profile.para_reading),
                "english": _text(raw, "english", "en"),
            }
        )
    return pairs


def _normalize_vocabulary(lesson: dict, profile: LanguageProfile) -> list[dict]:
    items: list[dict] = []
    for raw in _as_list(lesson.get("key_vocabulary")):
        if not isinstance(raw, dict):
            continue
        term = _text(raw, profile.vocab_term, "term", "source")
        if not term:
            continue
        items.append(
            {
                "term": term,
                "reading": _text(raw, "reading"),
                "romaji": _text(raw, "romaji"),
                "english": _text(raw, "english", "en"),
                "notes": _text(raw, "notes"),
            }
        )
    return items


def _normalize_grammar(lesson: dict, profile: LanguageProfile) -> list[dict]:
    points: list[dict] = []
    for raw in _as_list(lesson.get("grammar_points")):
        if not isinstance(raw, dict):
            continue
        word_order = [
            {"slot": _text(step, "slot", "position"), "text": _text(step, profile.word_order_text, "text", "source")}
            for step in _as_list(raw.get("word_order"))
            if isinstance(step, dict)
        ]
        more_examples = [
            {
                "text": _text(ex, profile.more_example, "text", "source"),
                "reading": _text(ex, profile.more_example_reading, "reading"),
                "romaji": _text(ex, "romaji"),
                "en": _text(ex, "en", "english"),
            }
            for ex in _as_list(raw.get("more_examples"))
            if isinstance(ex, dict)
        ]
        points.append(
            {
                "title": _text(raw, "title"),
                "example_text": _text(raw, profile.grammar_example, "example_text"),
                "example_reading": _text(raw, profile.grammar_example_reading, "example_reading"),
                "example_romaji": _text(raw, "example_romaji"),
                "example_en": _text(raw, "example_en", "english"),
                "explanation_en": _text(raw, "explanation_en", "explanation"),
                "word_order": [s for s in word_order if s["text"]],
                "more_examples": [e for e in more_examples if e["text"]],
                "tip_en": _text(raw, "tip_en", "tip"),
            }
        )
    return points


def _normalize_word_building(lesson: dict, profile: LanguageProfile) -> list[dict]:
    words: list[dict] = []
    for raw in _as_list(lesson.get("word_building")):
        if not isinstance(raw, dict):
            continue
        word = _text(raw, "word")
        if not word:
            continue
        words.append(
            {
                "word": word,
                "reading": _text(raw, profile.word_building_reading, "reading"),
                "romaji": _text(raw, "romaji"),
                "parts": _text(raw, "parts"),
                "english": _text(raw, "english", "en"),
            }
        )
    return words


def _normalize(
    lesson: dict[str, Any], profile: LanguageProfile, cfg: Config
) -> dict[str, Any]:
    """Canonical lesson shape shared by every language."""
    _, max_vocab = cfg.limits_for(profile.code)
    return {
        "title_translation": _text(lesson, "title_translation"),
        "article_paragraphs": _normalize_paragraphs(lesson, profile),
        "key_vocabulary": _normalize_vocabulary(lesson, profile)[:max_vocab],
        "grammar_points": _normalize_grammar(lesson, profile)[: profile.max_grammar_points],
        "word_building": _normalize_word_building(lesson, profile)[
            : profile.max_word_building
        ],
    }


def lesson_summary(lesson: dict[str, Any]) -> str:
    """One-line description used in logs."""
    return (
        f"{len(lesson.get('key_vocabulary') or [])} vocab, "
        f"{len(lesson.get('grammar_points') or [])} grammar, "
        f"{len(lesson.get('article_paragraphs') or [])} paragraphs"
    )
