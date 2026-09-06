"""Dutch Daily entry point.

Modes:
    python -m app.main                 -> run as a daily scheduler (container default)
    python -m app.main --once          -> build + email one lesson now, then exit
    python -m app.main --once --no-email  -> build one lesson, save preview only
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import kindle, lesson, nos, render
from .config import Config, get_config
from .lesson import LessonError
from .nos import Article
from .state import State
from .scheduler import run_daily

log = logging.getLogger("dutchdaily")


def _stamp(cfg: Config) -> str:
    return datetime.now(ZoneInfo(cfg.timezone)).strftime("%Y-%m-%d")


def _pick_article(cfg: Config, items: list, state: State) -> Article:
    """Prefer the newest article we have not sent yet; fall back to the newest."""
    unseen = [i for i in items if not state.is_seen(i.guid)]
    candidates = unseen or items
    for item in candidates[:5]:
        article = nos.build_article(cfg, item)
        if len(article.text) >= 200:
            return article
    # Last resort: whatever the newest item gives us.
    return nos.build_article(cfg, candidates[0])


def run_job(cfg: Config, state: State, *, email: bool) -> tuple[Path, bool]:
    """One full lesson: feed -> article -> lesson -> html -> (email)."""
    items = nos.fetch_feed(cfg)
    if not items:
        raise RuntimeError("No items found in the NOS feed.")

    article = _pick_article(cfg, items, state)
    log.info("Picked article: %s (%s)", article.title, article.url)

    # 1) Generate the lesson with Claude.
    lesson_data = lesson.build_lesson(article, cfg)
    log.info("Lesson generated (%d vocab items).", len(lesson_data.get("key_vocabulary", [])))

    # 2) Render and always save a preview copy locally.
    html = render.render_document(article, lesson_data, cfg)
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"dutch-daily-{_stamp(cfg)}.html"
    out_path = cfg.output_dir / filename
    out_path.write_text(html, encoding="utf-8")
    log.info("Preview saved: %s", out_path)

    # 3) Send to the Kindle.
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
            subject = f"Dutch Daily {_stamp(cfg)}: {article.title}"[:80]
            msg = kindle.build_message(cfg, html, subject, filename)
            kindle.send_kindle(cfg, msg)
            sent = True
            log.info("Sent to %s", ", ".join(cfg.kindle_emails))
    else:
        log.info("Email disabled (SEND_EMAIL=false or --no-email).")

    state.mark_seen(article.guid)
    state.set_last(
        {
            "date": _stamp(cfg),
            "guid": article.guid,
            "title": article.title,
            "url": article.url,
            "email_sent": sent,
            "file": filename,
        }
    )
    state.save()
    return out_path, sent


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
    parser = argparse.ArgumentParser(prog="dutch-daily", description=__doc__)
    parser.add_argument(
        "--once",
        action="store_true",
        help="run one lesson now and exit (instead of waiting for the daily time)",
    )
    parser.add_argument(
        "--no-email",
        action="store_true",
        help="do not email; only save the HTML preview (implies dry-run)",
    )
    parser.add_argument("--output", type=Path, default=None, help="override output dir")
    args = parser.parse_args(argv)

    cfg = get_config()
    if args.output is not None:
        cfg.output_dir = args.output

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    state = State(cfg.data_dir / "state.json")

    if args.once:
        _require_anthropic(cfg)
        try:
            path, sent = run_job(cfg, state, email=not args.no_email)
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
            "GMAIL_USER / GMAIL_APP_PASSWORD missing — lessons will be saved to "
            "%s but NOT emailed until you add them to .env.",
            cfg.output_dir,
        )
    log.info(
        "Dutch Daily scheduler started (timezone=%s, delivery=%s).",
        cfg.timezone,
        cfg.delivery_time,
    )
    run_daily(lambda: run_job(cfg, state, email=True), cfg)


if __name__ == "__main__":
    main()
