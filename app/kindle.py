"""Send the generated HTML document to a Kindle address via Gmail SMTP."""
from __future__ import annotations

import smtplib
from email.message import EmailMessage

from .config import Config

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


class SendError(RuntimeError):
    pass


def build_message(cfg: Config, html: str, subject: str, filename: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.gmail_user
    msg["To"] = cfg.kindle_emails
    # Kindle only converts *attachments*; the body itself is not used.
    msg.set_content(
        "Your daily language lesson is attached (one document, English "
        "breakdowns for every configured language).\n"
        "Kindle converts the attached .html file into a document with a "
        "table of contents you can jump around in.\n\n"
        "Veel succes en がんばって！ (Good luck!)"
    )
    msg.add_attachment(
        html.encode("utf-8"),
        maintype="text",
        subtype="html",
        filename=filename,
    )
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
