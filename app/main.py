"""Daily language lessons (by default Dutch + Japanese) in one Kindle document.

Modes:
    python -m app.main                     -> run as a daily scheduler (container default)
    python -m app.main --once              -> build + email one document now, then exit
    python -m app.main --once --no-email   -> build the document, save the preview only
    python -m app.main --check             -> verify feeds/scraping (no LLM call, no email)
    python -m app.main --lang ja --once    -> restrict a run to one language
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import kindle, lesson, render, sources
from .config import Config, get_config
from .languages import LanguageProfile, UnknownLanguage, get_profile, parse_languages
from .lesson import LessonError
from .scheduler import run_daily
from .state import State

log = logging.getLogger("langdaily")


def _stamp(cfg: Config) -> str:
    return datetime.now(ZoneInfo(cfg.timezone)).strftime("%Y-%m-%d")


def _profiles_for(cfg: Config) -> list[LanguageProfile]:
    return [get_profile(code) for code in cfg.languages]


def build_sections(
    cfg: Config, state: State, profiles: list[LanguageProfile]
) -> list[render.Section]:
    """Build one section per language, isolating failures to a single language."""
    sections: list[render.Section] = []
    for profile in profiles:
        try:
            items = sources.fetch_items(profile, cfg)
            if not items:
                raise RuntimeError(f"No items found in the {profile.source_name} feed.")
            article = sources.pick_article(profile, cfg, items, state)
            if article is None:
                # Every item in the feed has already been delivered.
                sections.append(
                    render.Section(
                        profile=profile,
                        error="no new article today (everything in the feed has "
                        "already been sent)",
                    )
                )
                continue
            log.info(
                "[%s] Picked article: %s (%s)", profile.code, article.title, article.url
            )
            lesson_data = lesson.build_lesson(article, cfg, profile)
            log.info(
                "[%s] Lesson generated (%s).", profile.code, lesson.lesson_summary(lesson_data)
            )
            sections.append(
                render.Section(profile=profile, article=article, lesson=lesson_data)
            )
        except Exception as exc:  # noqa: BLE001 - the other language still ships
            log.exception("[%s] Section failed: %s", profile.code, exc)
            sections.append(render.Section(profile=profile, error=str(exc)))
    return sections


def run_job(
    cfg: Config,
    state: State,
    *,
    email: bool,
    profiles: list[LanguageProfile] | None = None,
) -> tuple[Path, bool]:
    """One daily document: feeds -> articles -> lessons -> single HTML -> email."""
    profiles = profiles or _profiles_for(cfg)
    sections = build_sections(cfg, state, profiles)
    if not any(section.ok for section in sections):
        raise RuntimeError("No lesson could be generated for any configured language.")

    # 1) Render every language into ONE document and always keep a local preview.
    html = render.render_document(sections, cfg)
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"language-daily-{_stamp(cfg)}.html"
    out_path = cfg.output_dir / filename
    out_path.write_text(html, encoding="utf-8")
    log.info("Preview saved: %s", out_path)

    # Record the articles BEFORE emailing so a retry cannot re-send the same stories.
    delivered: dict[str, dict] = {}
    for section in sections:
        if not section.ok or section.article is None:
            continue
        state.mark_seen(section.profile.code, section.article.guid)
        delivered[section.profile.code] = {
            "guid": section.article.guid,
            "title": section.article.title,
            "url": section.article.url,
        }
    first = next(
        (s.article for s in sections if s.ok and s.article is not None), None
    )
    state.set_last(
        {
            "date": _stamp(cfg),
            "guid": first.guid if first else "",
            "title": first.title if first else "",
            "url": first.url if first else "",
            "email_sent": False,
            "file": filename,
            "languages": delivered,
        }
    )
    state.save()

    # 2) Send the single document to the Kindle.
    sent = False
    if email and cfg.send_email:
        missing = []
        if not cfg.gmail_user:
            missing.append("GMAIL_USER")
        if not cfg.gmail_app_password:
            missing.append("GMAIL_APP_PASSWORD")
        if missing:
            log.warning(
                "Email skipped: add %s to .env to enable delivery "
                "(preview still saved above).",
                ", ".join(missing),
            )
        else:
            titles = " | ".join(
                section.article.title
                for section in sections
                if section.ok and section.article is not None
            )
            subject = f"{render.document_title(cfg, sections)}: {titles}"[:120]
            msg = kindle.build_message(cfg, html, subject, filename)
            kindle.send_kindle(cfg, msg)
            sent = True
            log.info("Sent to %s", ", ".join(cfg.kindle_emails))
    else:
        log.info("Email disabled (SEND_EMAIL=false or --no-email).")

    if sent and isinstance(state.data.get("last_delivered"), dict):
        state.data["last_delivered"]["email_sent"] = True
        state.save()
    return out_path, sent


def check_sources(cfg: Config, state: State, profiles: list[LanguageProfile]) -> int:
    """Fetch each feed and article and print what the scraper found (no LLM)."""
    ok = True
    for profile in profiles:
        print(f"\n=== {profile.name} ({profile.code}) ===")
        print(f"feed: {cfg.feed_url_for(profile.code, profile.default_feed_url)}")
        try:
            items = sources.fetch_items(profile, cfg)
        except Exception as exc:  # noqa: BLE001
            print(f"  FEED FAILED: {exc}")
            ok = False
            continue
        unseen = [i for i in items if not state.is_seen(profile.code, i.guid)]
        print(f"  items: {len(items)} ({len(unseen)} not sent yet)")
        if not items:
            ok = False
            continue
        item = (unseen or items)[0]
        print(f"  newest: {item.title}")
        print(f"  url: {item.url}")
        try:
            article = sources.build_article(profile, cfg, item)
        except Exception as exc:  # noqa: BLE001
            print(f"  ARTICLE FAILED: {exc}")
            ok = False
            continue
        print(f"  paragraphs: {len(article.paragraphs)}  chars: {len(article.text)}")
        for index, para in enumerate(article.paragraphs[:3], 1):
            print(f"    [{index}] {para[:110]}")
        if len(article.paragraphs) > 3:
            print(f"    [last] {article.paragraphs[-1][:110]}")
        if len(article.text) < profile.min_article_chars:
            print("  WARNING: very little text - the lesson would be thin.")
            ok = False
    print()
    return 0 if ok else 1


def _require_anthropic(cfg: Config) -> None:
    if not cfg.anthropic_api_key:
        print(
            "\nError: ANTHROPIC_API_KEY is not set.\n"
            "Create a .env file from .env.example and add your LLM API key "
            "(Anthropic, or DeepSeek via ANTHROPIC_BASE_URL — see README.md).\n",
            file=sys.stderr,
        )
        raise SystemExit(2)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="language-daily", description=__doc__)
    parser.add_argument(
        "--once",
        action="store_true",
        help="build one document now and exit (instead of waiting for the daily time)",
    )
    parser.add_argument(
        "--no-email",
        action="store_true",
        help="do not email; only save the HTML preview (implies dry-run)",
    )
    parser.add_argument("--output", type=Path, default=None, help="override output dir")
    parser.add_argument(
        "--lang",
        default=None,
        help="comma-separated languages for this run, e.g. nl or nl,ja "
        "(default: LANGUAGES from .env)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fetch the feeds and articles and report what was found, without "
        "calling the LLM or sending email",
    )
    args = parser.parse_args(argv)

    cfg = get_config()
    if args.output is not None:
        cfg.output_dir = args.output

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        if args.lang:
            cfg.languages = parse_languages(args.lang)
        profiles = _profiles_for(cfg)
    except UnknownLanguage as exc:
        print(f"\nError: {exc}\n", file=sys.stderr)
        raise SystemExit(2) from exc

    state = State(cfg.data_dir / "state.json")

    if args.check:
        raise SystemExit(check_sources(cfg, state, profiles))

    if args.once:
        _require_anthropic(cfg)
        try:
            path, sent = run_job(cfg, state, email=not args.no_email, profiles=profiles)
        except LessonError as exc:
            log.error("Lesson generation failed: %s", exc)
            raise SystemExit(1) from exc
        except Exception:  # noqa: BLE001
            log.exception("Run failed.")
            raise SystemExit(1) from None
        print(f"\nPreview: {path}")
        if args.no_email or not cfg.send_email:
            print("Email was disabled - open the preview to check it, then enable sending.")
        elif sent:
            print(f"Sent to {', '.join(cfg.kindle_emails)}")
        return

    # Scheduler mode (normal container operation).
    _require_anthropic(cfg)
    if cfg.send_email and not (cfg.gmail_user and cfg.gmail_app_password):
        log.warning(
            "GMAIL_USER / GMAIL_APP_PASSWORD missing — documents will be saved to "
            "%s but NOT emailed until you add them to .env.",
            cfg.output_dir,
        )
    log.info(
        "Language daily scheduler started (languages=%s, timezone=%s, delivery=%s).",
        ", ".join(profile.code for profile in profiles),
        cfg.timezone,
        cfg.delivery_time,
    )
    run_daily(lambda: run_job(cfg, state, email=True, profiles=profiles), cfg)


if __name__ == "__main__":
    main()
