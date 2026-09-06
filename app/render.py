"""Render a lesson into a Kindle-friendly standalone HTML document."""
from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from .config import Config
from .nos import Article

_CSS = """
  body { font-family: Georgia, 'Times New Roman', serif; line-height: 1.55;
         color: #1a1a1a; margin: 0 4px; }
  h1 { font-size: 1.5em; }
  h1.original { color: #0b3d91; }
  h2 { font-size: 1.2em; margin-top: 1.1em; }
  h2.original { color: #4b5563; font-weight: normal; }
  h3 { font-size: 1.05em; margin-bottom: 0.15em; }
  .meta { color: #6b7280; font-size: 0.85em; }
  .section-head { border-bottom: 2px solid #e5e7eb; padding-bottom: 2px; }
  .vocab-word { font-weight: bold; color: #0b3d91; }
  .example { color: #374151; margin: 0.15em 0 0.5em 1em; }
  .example .nl { font-style: italic; }
  .note { color: #4b5563; }
  .kw { font-weight: bold; color: #0b3d91; }
  .pair { margin: 0.5em 0 0.9em 0; }
  .dutch { margin: 0 0 0.15em 0; }
  .english { color: #4b5563; margin: 0; }
  .slots { color: #374151; }
  blockquote { margin: 0.4em 0 0.6em 0.4em; padding-left: 0.8em;
               border-left: 3px solid #e5e7eb; }
  .nl-example { font-style: italic; color: #0b3d91; margin: 0 0 0.15em 0; }
  .tip { color: #0b7285; }
  ul { margin-top: 0.2em; }
  li { margin-bottom: 0.35em; }
  .footer { margin-top: 1.5em; color: #9ca3af; font-size: 0.75em;
            border-top: 1px solid #e5e7eb; padding-top: 0.5em; }
  .source { word-break: break-all; }
"""


def _esc(value: Any) -> str:
    return escape("" if value is None else str(value))


def _h2(text: str) -> str:
    return f'<h2 class="section-head">{_esc(text)}</h2>'


def _section_paragraphs(pairs: list[dict]) -> str:
    if not pairs:
        return ""
    parts = [_h2("The article - Dutch and English, paragraph by paragraph")]
    for i, pair in enumerate(pairs, 1):
        parts.append('<div class="pair">')
        parts.append(
            f'<p class="dutch"><b>{i}.</b> {_esc(pair.get("dutch") or "")}</p>'
        )
        parts.append(f'<p class="english">{_esc(pair.get("english") or "")}</p>')
        parts.append("</div>")
    return "\n".join(parts)


def _section_vocabulary(vocab: list[dict]) -> str:
    if not vocab:
        return ""
    parts = [_h2("Key Vocabulary")]
    parts.append("<ul>")
    for v in vocab:
        dutch = _esc(v.get("dutch") or "")
        english = _esc(v.get("english") or "")
        notes = _esc(v.get("notes") or "")
        parts.append("<li>")
        parts.append(f'<span class="kw">{dutch}</span> &mdash; {english}')
        if notes:
            parts.append(f' <span class="note">({notes})</span>')
        parts.append("</li>")
    parts.append("</ul>")
    return "\n".join(parts)


def _section_grammar(points: list[dict]) -> str:
    if not points:
        return ""
    parts = [_h2("Key Grammar Points")]
    for i, g in enumerate(points, 1):
        parts.append(f'<h3>{i}. {_esc(g.get("title") or "")}</h3>')
        nl = _esc(g.get("example_nl") or "")
        en = _esc(g.get("example_en") or "")
        if nl or en:
            parts.append(
                f"<blockquote><p class='nl-example'>{nl}</p>"
                f"<p class='note'>{en}</p></blockquote>"
            )
        exp = _esc(g.get("explanation_en") or "")
        if exp:
            parts.append(f"<p>{exp}</p>")
        wo = g.get("word_order") or []
        if wo:
            slots = " &nbsp;&middot;&nbsp; ".join(
                f"{_esc(s.get('slot') or '')}: {_esc(s.get('dutch') or '')}"
                for s in wo
            )
            parts.append(f'<p class="slots"><b>Word order:</b> {slots}</p>')
        more = g.get("more_examples") or []
        if more:
            parts.append("<ul>")
            for m in more:
                parts.append(
                    f"<li><i>{_esc(m.get('nl') or '')}</i>"
                    f" &mdash; {_esc(m.get('en') or '')}</li>"
                )
            parts.append("</ul>")
        tip = _esc(g.get("tip_en") or "")
        if tip:
            parts.append(f'<p class="tip"><b>Tip:</b> {tip}</p>')
    return "\n".join(parts)


def _section_word_building(words: list[dict]) -> str:
    if not words:
        return ""
    parts = [_h2("Word building - words from today's article")]
    parts.append("<ul>")
    for w in words:
        parts.append(
            f'<li><span class="kw">{_esc(w.get("word") or "")}</span> = '
            f'<i>{_esc(w.get("parts") or "")}</i>'
            f' &mdash; {_esc(w.get("english") or "")}</li>'
        )
    parts.append("</ul>")
    return "\n".join(parts)


def render_document(article: Article, lesson: dict[str, Any], cfg: Config) -> str:
    stamp = datetime.now(ZoneInfo(cfg.timezone)).strftime("%A %d %B %Y")

    body = f"""\
<html>
<head>
<meta charset="utf-8">
<title>Dutch Daily — {_esc(article.title)}</title>
<style>{_CSS}</style>
</head>
<body>
  <h1 class="original">{_esc(article.title)}</h1>
  <p class="meta">Dutch Daily · {_esc(stamp)} · beginner level (A1)</p>

  {_section_vocabulary(lesson.get("key_vocabulary") or [])}
  {_section_grammar(lesson.get("grammar_points") or [])}
  {_section_word_building(lesson.get("word_building") or [])}
  {_section_paragraphs(lesson.get("article_paragraphs") or [])}

  <div class="footer">
    <p><b>Read the original:</b> <a href="{_esc(article.url)}">{_esc(article.url)}</a></p>
    <p class="source">{_esc(article.url)}</p>
    <p>Generated automatically for your daily Dutch practice. Vocab and grammar
    breakdown by AI — check important details before relying on them.</p>
  </div>
</body>
</html>
"""
    return body
