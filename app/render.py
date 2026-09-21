"""Render one or more language lessons into a single Kindle-friendly document.

The document starts with an explicit table of contents whose links point at
anchors inside the file; Kindle's conversion keeps internal links, so the reader
can jump straight from the TOC to the Dutch or the Japanese half (and back).
Headings are nested (h1 per language, h2 per section) so the same structure also
shows up in the Kindle "Go to" navigation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import Any, Optional
from zoneinfo import ZoneInfo

from .config import Config
from .languages import LanguageProfile
from .web import Article

_CSS = """
  body { font-family: Georgia, 'Times New Roman', serif; line-height: 1.55;
         color: #1a1a1a; margin: 0 4px; }
  h1 { font-size: 1.5em; }
  h1.original { color: #0b3d91; }
  h2 { font-size: 1.2em; margin-top: 1.1em; }
  h2.original { color: #4b5563; font-weight: normal; }
  h3 { font-size: 1.05em; margin-bottom: 0.15em; }
  .doc-title { font-size: 1.55em; font-weight: bold; color: #111827;
               margin: 0.1em 0 0.2em 0; }
  .meta { color: #6b7280; font-size: 0.85em; }
  .section-head { border-bottom: 2px solid #e5e7eb; padding-bottom: 2px; }
  .lang { font-size: 1.45em; color: #0b3d91; margin: 0.2em 0 0.2em 0;
          border-bottom: 3px solid #0b3d91; padding-bottom: 3px; }
  .toc { margin: 0.4em 0 1.4em 0; }
  .toc ul { list-style-type: none; padding-left: 1.1em; margin-top: 0.2em; }
  .toc li { margin-bottom: 0.25em; }
  .vocab-word { font-weight: bold; color: #0b3d91; }
  .kw { font-weight: bold; color: #0b3d91; }
  .reading { color: #6b7280; font-size: 0.88em; }
  .romaji { color: #9ca3af; font-size: 0.85em; font-style: italic; }
  .example { color: #374151; margin: 0.15em 0 0.5em 1em; }
  .note { color: #4b5563; }
  .pair { margin: 0.5em 0 0.9em 0; }
  .target { margin: 0 0 0.15em 0; }
  .english { color: #4b5563; margin: 0; }
  .slots { color: #374151; }
  blockquote { margin: 0.4em 0 0.6em 0.4em; padding-left: 0.8em;
               border-left: 3px solid #e5e7eb; }
  .src-example { font-style: italic; color: #0b3d91; margin: 0 0 0.15em 0; }
  .tip { color: #0b7285; }
  ul { margin-top: 0.2em; }
  li { margin-bottom: 0.35em; }
  .backtotoc { margin-top: 1em; font-size: 0.9em; }
  .unavailable { color: #991b1b; }
  .footer { margin-top: 1.5em; color: #9ca3af; font-size: 0.75em;
            border-top: 1px solid #e5e7eb; padding-top: 0.5em; }
  .source { word-break: break-all; }
  .divider { border: 0; border-top: 1px solid #d1d5db; margin: 1.6em 0; }
"""


@dataclass
class Section:
    """One language's slice of the daily document."""

    profile: LanguageProfile
    article: Optional[Article] = None
    lesson: Optional[dict[str, Any]] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.article is not None and self.lesson is not None

    @property
    def heading(self) -> str:
        return f"{self.profile.name} - {self.profile.native_name}"


def _esc(value: Any) -> str:
    return escape("" if value is None else str(value))


def _anchor(code: str, suffix: str = "") -> str:
    return f"{code}-{suffix}" if suffix else code


def _pair(text: str, reading: str = "") -> str:
    """A target-language line with an optional kana reading underneath."""
    if not reading:
        return text
    return f'{text}<br/><span class="reading">{reading}</span>'


def _romaji_span(romaji: str) -> str:
    return f' <span class="romaji">({_esc(romaji)})</span>' if romaji else ""


# --------------------------------------------------------------------------- #
# Table of contents                                                            #
# --------------------------------------------------------------------------- #


def _toc_entries(section: Section) -> list[tuple[str, str]]:
    """(label, anchor) for the sub-sections that actually have content."""
    lesson = section.lesson or {}
    entries: list[tuple[str, str]] = []
    if lesson.get("key_vocabulary"):
        entries.append((section.profile.vocab_heading, _anchor(section.profile.code, "vocab")))
    if lesson.get("grammar_points"):
        entries.append((section.profile.grammar_heading, _anchor(section.profile.code, "grammar")))
    if lesson.get("word_building"):
        entries.append(
            (section.profile.word_building_heading, _anchor(section.profile.code, "words"))
        )
    if lesson.get("article_paragraphs"):
        entries.append((section.profile.article_heading, _anchor(section.profile.code, "article")))
    return entries


def _toc(sections: list[Section]) -> str:
    parts = [
        '<a id="toc"></a>',
        '<div class="toc">',
        '<h2 class="section-head">Contents</h2>',
        "<ul>",
    ]
    for section in sections:
        code = section.profile.code
        parts.append(f'<li><a href="#{code}"><b>{_esc(section.heading)}</b></a>')
        if not section.ok:
            parts.append(' <span class="note">- not available today</span>')
        entries = _toc_entries(section)
        if entries:
            parts.append("<ul>")
            for label, anchor in entries:
                parts.append(f'<li><a href="#{anchor}">{_esc(label)}</a></li>')
            parts.append("</ul>")
        parts.append("</li>")
    parts.extend(["</ul>", "</div>"])
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# Language sections                                                            #
# --------------------------------------------------------------------------- #


def _section_vocabulary(section: Section) -> str:
    profile = section.profile
    vocab = (section.lesson or {}).get("key_vocabulary") or []
    if not vocab:
        return ""
    parts = [
        f'<h2 class="section-head" id="{_anchor(profile.code, "vocab")}">'
        f"{_esc(profile.vocab_heading)}</h2>",
        "<ul>",
    ]
    for item in vocab:
        term = _esc(item.get("term") or "")
        reading = _esc(item.get("reading") or "")
        english = _esc(item.get("english") or "")
        notes = _esc(item.get("notes") or "")
        romaji = _esc(item.get("romaji") or "")
        rendered = f'<span class="kw">{term}</span>'
        if reading:
            rendered += f' <span class="reading">[{reading}]</span>'
        if romaji:
            rendered += _romaji_span(romaji)
        line = f"{rendered} &mdash; {english}"
        if notes:
            line += f' <span class="note">({notes})</span>'
        parts.append(f"<li>{line}</li>")
    parts.append("</ul>")
    return "\n".join(parts)


def _section_grammar(section: Section) -> str:
    profile = section.profile
    points = (section.lesson or {}).get("grammar_points") or []
    if not points:
        return ""
    parts = [
        f'<h2 class="section-head" id="{_anchor(profile.code, "grammar")}">'
        f"{_esc(profile.grammar_heading)}</h2>"
    ]
    for i, point in enumerate(points, 1):
        parts.append(f'<h3>{i}. {_esc(point.get("title") or "")}</h3>')
        text = _esc(point.get("example_text") or "")
        reading = _esc(point.get("example_reading") or "")
        romaji = _esc(point.get("example_romaji") or "")
        english = _esc(point.get("example_en") or "")
        if text or english:
            parts.append("<blockquote>")
            parts.append(f'<p class="src-example">{_pair(text, reading)}</p>')
            if romaji:
                parts.append(f'<p class="romaji">{romaji}</p>')
            if english:
                parts.append(f'<p class="note">{english}</p>')
            parts.append("</blockquote>")
        explanation = _esc(point.get("explanation_en") or "")
        if explanation:
            parts.append(f"<p>{explanation}</p>")
        slots = point.get("word_order") or []
        if slots:
            rendered = " &nbsp;&middot;&nbsp; ".join(
                f"{_esc(step.get('slot') or '')}: {_esc(step.get('text') or '')}"
                for step in slots
            )
            parts.append(f'<p class="slots"><b>Word order:</b> {rendered}</p>')
        more = point.get("more_examples") or []
        if more:
            parts.append("<ul>")
            for example in more:
                example_text = _esc(example.get("text") or "")
                example_reading = _esc(example.get("reading") or "")
                example_romaji = _esc(example.get("romaji") or "")
                example_en = _esc(example.get("en") or "")
                line = f"<li><i>{_pair(example_text, example_reading)}</i>"
                if example_romaji:
                    line += _romaji_span(example_romaji)
                line += f" &mdash; {example_en}</li>"
                parts.append(line)
            parts.append("</ul>")
        tip = _esc(point.get("tip_en") or "")
        if tip:
            parts.append(f'<p class="tip"><b>Tip:</b> {tip}</p>')
    return "\n".join(parts)


def _section_word_building(section: Section) -> str:
    profile = section.profile
    words = (section.lesson or {}).get("word_building") or []
    if not words:
        return ""
    parts = [
        f'<h2 class="section-head" id="{_anchor(profile.code, "words")}">'
        f"{_esc(profile.word_building_heading)}</h2>",
        "<ul>",
    ]
    for item in words:
        word = _esc(item.get("word") or "")
        reading = _esc(item.get("reading") or "")
        romaji = _esc(item.get("romaji") or "")
        parts_text = _esc(item.get("parts") or "")
        english = _esc(item.get("english") or "")
        head = f'<span class="kw">{word}</span>'
        if reading:
            head += f' <span class="reading">[{reading}]</span>'
        if romaji:
            head += _romaji_span(romaji)
        parts.append(f"<li>{head} = <i>{parts_text}</i> &mdash; {english}</li>")
    parts.append("</ul>")
    return "\n".join(parts)


def _section_paragraphs(section: Section) -> str:
    profile = section.profile
    pairs = (section.lesson or {}).get("article_paragraphs") or []
    if not pairs:
        return ""
    parts = [
        f'<h2 class="section-head" id="{_anchor(profile.code, "article")}">'
        f"{_esc(profile.article_heading)}</h2>"
    ]
    for i, pair in enumerate(pairs, 1):
        text = _esc(pair.get("text") or "")
        reading = _esc(pair.get("reading") or "")
        english = _esc(pair.get("english") or "")
        parts.append('<div class="pair">')
        parts.append(f'<p class="target"><b>{i}.</b> {_pair(text, reading)}</p>')
        parts.append(f'<p class="english">{english}</p>')
        parts.append("</div>")
    return "\n".join(parts)


def _section_source(section: Section) -> str:
    """Source line + link back to the TOC at the end of each language block."""
    parts = []
    if section.article is not None:
        if section.article.audio_url:
            parts.append(
                '<p class="meta"><b>Listen (slow reading):</b> '
                f'<a href="{_esc(section.article.audio_url)}">audio file</a></p>'
            )
        parts.append(
            '<p class="meta"><b>Read the original:</b> '
            f'<a href="{_esc(section.article.url)}">{_esc(section.article.url)}</a></p>'
        )
    parts.append('<p class="backtotoc"><a href="#toc">Back to contents</a></p>')
    return "\n".join(parts)


def _render_section(section: Section) -> str:
    profile = section.profile
    parts = [
        f'<h1 class="lang" id="{profile.code}">{_esc(section.heading)}</h1>',
        f'<p class="meta">Beginner level (A1) &middot; source: {_esc(profile.source_name)}</p>',
    ]
    if not section.ok:
        parts.append(
            '<p class="unavailable">This section could not be generated today'
            f" ({_esc(section.error or 'unknown error')}).</p>"
        )
        parts.append(_section_source(section))
        return "\n".join(parts)

    article = section.article
    assert article is not None  # noqa: S101 - guarded by section.ok
    translation = (section.lesson or {}).get("title_translation") or ""
    parts.append(f'<h2 class="original">{_esc(article.title)}</h2>')
    if translation:
        parts.append(f'<p class="english">{_esc(translation)}</p>')

    parts.extend(
        [
            _section_vocabulary(section),
            _section_grammar(section),
            _section_word_building(section),
            _section_paragraphs(section),
            _section_source(section),
        ]
    )
    return "\n".join(part for part in parts if part)


# --------------------------------------------------------------------------- #
# Document                                                                     #
# --------------------------------------------------------------------------- #


def document_title(cfg: Config, sections: list[Section]) -> str:
    names = " + ".join(section.profile.name for section in sections)
    stamp = datetime.now(ZoneInfo(cfg.timezone)).strftime("%Y-%m-%d")
    return f"{names or 'Language'} Daily {stamp}"


def render_document(sections: list[Section], cfg: Config) -> str:
    """Render every language section into one HTML document with a linked TOC."""
    stamp = datetime.now(ZoneInfo(cfg.timezone)).strftime("%A %d %B %Y")
    title = document_title(cfg, sections)
    blocks = "\n<hr class=\"divider\"/>\n".join(
        _render_section(section) for section in sections
    )
    body = f"""\
<html>
<head>
<meta charset="utf-8">
<title>{_esc(title)}</title>
<style>{_CSS}</style>
</head>
<body>
  <div class="doc-title">{_esc(title)}</div>
  <p class="meta">Daily reading practice &middot; {_esc(stamp)}</p>

  {_toc(sections)}

  {blocks}

  <div class="footer">
    <p>Generated automatically for your daily language practice. Vocab and
    grammar breakdown by AI - check important details before relying on them.</p>
    <p><a href="#toc">Back to contents</a></p>
  </div>
</body>
</html>
"""
    return body
