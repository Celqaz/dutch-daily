"""Configuration loading from environment variables and a local .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output"

# One feed per language; override individually with NOS_FEED_URL / JA_FEED_URL.
# The Japanese feed is nhkeasier.com, a mirror of NHK News Web Easy: its feed
# carries the COMPLETE easy-Japanese article with furigana for every kanji plus
# a slow-reading mp3, so no scraping is needed.
DEFAULT_FEED_URLS = {
    "nl": "https://feeds.nos.nl/nosnieuwsalgemeen",
    "ja": "https://nhkeasier.com/feed/",
}


def load_dotenv(path: Path = PROJECT_ROOT / ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines, # comments, quotes stripped).

    Real environment variables always win over values from the file.
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _split_emails(raw: str) -> list[str]:
    """Parse a comma-separated list of email addresses."""
    return [part.strip() for part in raw.split(",") if part.strip()]


@dataclass
class Config:
    # --- Languages & sources ---
    languages: list[str] = field(default_factory=lambda: ["nl"])
    feed_urls: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_FEED_URLS))
    # --- LLM (lesson generation) ---
    # Works with the Anthropic API out of the box. To use DeepSeek instead,
    # point ANTHROPIC_BASE_URL at https://api.deepseek.com/anthropic and put a
    # DeepSeek key in ANTHROPIC_API_KEY.
    anthropic_api_key: str = ""
    anthropic_base_url: str = ""
    claude_model: str = ""
    # --- Gmail -> Kindle ---
    gmail_user: str = ""
    gmail_app_password: str = ""
    # One or more Kindle destinations (KINDLE_EMAIL may be comma-separated).
    kindle_emails: list[str] = field(default_factory=lambda: ["yourname@kindle.com"])
    # --- Scheduling ---
    timezone: str = "Europe/Amsterdam"
    delivery_time: str = "06:00"
    # --- Behaviour / tuning ---
    data_dir: Path = DEFAULT_DATA_DIR
    output_dir: Path = DEFAULT_OUTPUT_DIR
    send_email: bool = True
    # When a feed has no unseen item left, resend the newest one instead of
    # skipping that language for the day.
    allow_repeats: bool = False
    # Dutch (and default for any other language)
    max_article_chars: int = 1700
    max_vocab: int = 12
    # Japanese needs a shorter source text: characters carry much more meaning
    # and the kana/romaji readings make the generated lesson longer.
    max_article_chars_ja: int = 900
    max_vocab_ja: int = 10
    http_timeout: int = 30

    @property
    def missing_secrets(self) -> list[str]:
        missing: list[str] = []
        if not self.anthropic_api_key:
            missing.append("ANTHROPIC_API_KEY")
        if not self.gmail_user:
            missing.append("GMAIL_USER")
        if not self.gmail_app_password:
            missing.append("GMAIL_APP_PASSWORD")
        return missing

    def limits_for(self, code: str) -> tuple[int, int]:
        """(max article characters, max vocabulary items) for a language."""
        if code == "ja":
            return self.max_article_chars_ja, self.max_vocab_ja
        return self.max_article_chars, self.max_vocab

    def feed_url_for(self, code: str, default: str = "") -> str:
        return self.feed_urls.get(code) or default


def _resolve_model() -> str:
    """Model name: LLM_MODEL/CLAUDE_MODEL env var, else a per-provider default."""
    env_model = os.environ.get("LLM_MODEL") or os.environ.get("CLAUDE_MODEL")
    if env_model:
        return env_model
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "").lower()
    if "deepseek" in base_url:
        return "deepseek-v4-pro"
    return "claude-sonnet-4-5"


def get_config() -> Config:
    load_dotenv()
    return Config(
        languages=_parse_languages_env(os.environ.get("LANGUAGES", "nl")),
        feed_urls={
            code: os.environ.get(env) or url
            for code, env, url in (
                ("nl", "NOS_FEED_URL", DEFAULT_FEED_URLS["nl"]),
                ("ja", "JA_FEED_URL", DEFAULT_FEED_URLS["ja"]),
            )
        },
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        anthropic_base_url=os.environ.get("ANTHROPIC_BASE_URL", ""),
        claude_model=_resolve_model(),
        gmail_user=os.environ.get("GMAIL_USER", "").strip(),
        # Gmail app passwords are often pasted as "xxxx xxxx xxxx xxxx"; Gmail
        # expects the 16 characters without the spaces.
        gmail_app_password="".join(
            os.environ.get("GMAIL_APP_PASSWORD", "").split()
        ),
        kindle_emails=_split_emails(
            os.environ.get("KINDLE_EMAIL", "yourname@kindle.com")
        ),
        timezone=os.environ.get("TIMEZONE", "Europe/Amsterdam"),
        delivery_time=os.environ.get("DELIVERY_TIME", "06:00"),
        data_dir=Path(os.environ.get("DATA_DIR", DEFAULT_DATA_DIR)),
        output_dir=Path(os.environ.get("OUTPUT_DIR", DEFAULT_OUTPUT_DIR)),
        send_email=_env_bool("SEND_EMAIL", True),
        allow_repeats=_env_bool("ALLOW_REPEATS", False),
        max_article_chars=int(os.environ.get("MAX_ARTICLE_CHARS", "1700")),
        max_vocab=int(os.environ.get("MAX_VOCAB", "12")),
        max_article_chars_ja=int(os.environ.get("MAX_ARTICLE_CHARS_JA", "900")),
        max_vocab_ja=int(os.environ.get("MAX_VOCAB_JA", "10")),
        http_timeout=int(os.environ.get("HTTP_TIMEOUT", "30")),
    )


def _parse_languages_env(raw: str) -> list[str]:
    """Split LANGUAGES; validation against the profiles happens in main()."""
    codes = [part.strip().lower() for part in raw.split(",") if part.strip()]
    return codes or ["nl"]
