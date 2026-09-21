"""Send the generated documents to a Kindle address via Gmail SMTP."""
from __future__ import annotations

import smtplib
from email.message import EmailMessage
from pathlib import Path

from .config import Config

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465

_MIME_TYPES = {
    ".epub": ("application", "epub+zip"),
    ".html": ("text", "html"),
    ".pdf": ("application", "pdf"),
}


class SendError(RuntimeError):
    pass


def build_message(cfg: Config, files: list[tuple[str, bytes]], subject: str) -> EmailMessage:
    """Build the email with one or more attachments.

    ``files`` is a list of ``(filename, content)``; the MIME type is derived from
    the extension (``.epub`` for KOReader, ``.html`` for Kindle's converter).
    """
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.gmail_user
    msg["To"] = cfg.kindle_emails
    kinds = ", ".join(Path(name).suffix.lstrip(".") for name, _ in files)
    # Kindle only converts *attachments*; the body itself is not used.
    msg.set_content(
        f"Your daily language practice is attached ({kinds}) - one document with\n"
        "a table of contents covering every language.\n"
        "EPUB: read it in KOReader (or pull it from the OPDS catalog).\n"
        "HTML: opens in any browser and is what Send-to-Kindle converts well.\n\n"
        "Veel succes en がんばって！ (Good luck!)\n"
    )
    for name, payload in files:
        maintype, subtype = _MIME_TYPES.get(
            Path(name).suffix.lower(), ("application", "octet-stream")
        )
        msg.add_attachment(payload, maintype=maintype, subtype=subtype, filename=name)
    return msg


def _is_placeholder(pw: str) -> bool:
    compact = "".join(pw.split()).lower()
    return compact == "xxxxxxxxxxxxxxxx" or (len(compact) == 16 and len(set(compact)) == 1)


def send_kindle(cfg: Config, msg: EmailMessage) -> None:
    """Send via Gmail's SSL SMTP endpoint using an App Password."""
    if not (cfg.gmail_user and cfg.gmail_app_password):
        raise SendError("GMAIL_USER / GMAIL_APP_PASSWORD are not configured.")
    if _is_placeholder(cfg.gmail_app_password):
        raise SendError(
            "GMAIL_APP_PASSWORD in .env is still the example placeholder "
            "(xxxx xxxx xxxx xxxx). Replace it with a REAL Gmail App Password:\n"
            "  Google Account > Security > 2-Step Verification > App passwords\n"
            "  (2-Step Verification must be ON to create app passwords)."
        )
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=cfg.http_timeout) as server:
            server.login(cfg.gmail_user, cfg.gmail_app_password)
            server.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise SendError(
            "Gmail rejected the login. Use an App Password (not your normal "
            "password) and make sure 2-Step Verification is on."
        ) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise SendError(f"Could not send email: {exc}") from exc
