"""Outbound email for Umrah Connect.

Uses SMTP over SSL when the OMRA_SMTP_* environment variables are all set:

    OMRA_SMTP_HOST  e.g. smtp.zoho.com
    OMRA_SMTP_PORT  e.g. 465 (SSL)
    OMRA_SMTP_USER  SMTP login
    OMRA_SMTP_PASS  SMTP password
    OMRA_SMTP_FROM  From address, e.g. "Umrah Connect <no-reply@example.com>"

When SMTP is not configured (dev / tests), the message is logged to the
uvicorn logger instead and the function returns False, so callers can keep
their dev-log fallbacks.
"""
import logging
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger("uvicorn.error")


def _smtp_config():
    host = os.environ.get("OMRA_SMTP_HOST", "").strip()
    port = os.environ.get("OMRA_SMTP_PORT", "").strip()
    user = os.environ.get("OMRA_SMTP_USER", "").strip()
    password = os.environ.get("OMRA_SMTP_PASS", "").strip()
    sender = os.environ.get("OMRA_SMTP_FROM", "").strip()
    if not (host and port and user and password and sender):
        return None
    try:
        return {"host": host, "port": int(port), "user": user, "password": password, "from": sender}
    except ValueError:
        logger.error("[mailer] OMRA_SMTP_PORT is not a number: %r", port)
        return None


def send_email(to: str, subject: str, html: str) -> bool:
    """Send an HTML email. Returns True when actually sent via SMTP,
    False when SMTP is unconfigured (message logged) or sending failed."""
    cfg = _smtp_config()
    if cfg is None:
        logger.info("[mailer] SMTP not configured — email NOT sent.\n  To: %s\n  Subject: %s\n  Body: %s",
                    to, subject, html)
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg["from"]
    msg["To"] = to
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(cfg["host"], cfg["port"], context=context, timeout=20) as server:
            server.login(cfg["user"], cfg["password"])
            server.sendmail(cfg["from"], [to], msg.as_string())
        return True
    except Exception:
        logger.exception("[mailer] Failed to send email to %s (subject: %s)", to, subject)
        return False
