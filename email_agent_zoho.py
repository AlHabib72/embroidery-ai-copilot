#!/usr/bin/env python3
"""Zoho Mail delivery agent for the EmbroideryCopilot Outreach CRM.

Sends approved B2B outreach over Zoho SMTP (smtppro.zoho.in:587, STARTTLS).

Safety properties (all enforced in code, not just documented):
  * Credentials come from the environment / a local .env -- never from source.
  * A daily sending cap and a minimum interval between messages.
  * Duplicate-send protection across restarts (sent_log.csv is the record).
  * A dry-run mode that never opens an SMTP connection.
  * A preview mode that shows exactly what would be sent.
  * Suppressed / rejected / hard-bounced contacts are never contacted.
  * A human approval step is required before anything is sent.
  * Every attempt -- success or failure -- is recorded with its error reason.

Usage (PowerShell):
    python email_agent_zoho.py --preview
    python email_agent_zoho.py --dry-run
    python email_agent_zoho.py --send --confirm
"""
from __future__ import annotations

import argparse
import email.utils
import html
import os
import random
import re
import smtplib
import ssl
import socket
import sys
import time
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import outreach_store as store

# --------------------------------------------------------------------------
# Configuration -- every value overridable by environment variable.
# --------------------------------------------------------------------------
SMTP_HOST = os.environ.get("ZOHO_SMTP_HOST", "smtppro.zoho.in")
SMTP_PORT = int(os.environ.get("ZOHO_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("ZOHO_SMTP_USER", "founder@embroiderycopilot.com")
SMTP_PASS = os.environ.get("ZOHO_SMTP_PASS", "") or os.environ.get("SMTP_PASS", "")

SENDER_NAME = os.environ.get("OUTREACH_SENDER_NAME", "Embroidery Copilot")
SENDER_EMAIL = SMTP_USER
REPLY_TO = os.environ.get("OUTREACH_REPLY_TO", SMTP_USER)

DAILY_LIMIT = int(os.environ.get("OUTREACH_DAILY_LIMIT", "50"))
MIN_INTERVAL_SECONDS = float(os.environ.get("OUTREACH_MIN_INTERVAL", "45"))
MAX_RETRIES = int(os.environ.get("OUTREACH_MAX_RETRIES", "3"))
RETRY_BASE_DELAY = float(os.environ.get("OUTREACH_RETRY_BASE_DELAY", "60"))

ATTACHMENT_DIR = store.BASE_DIR
DEFAULT_ATTACHMENTS = [
    name for name in ("cv.pdf", "portfolio.pdf")
    if (store.BASE_DIR / name).is_file()
]

# Include the sender's real identity and a way to opt out.  Truthful and
# complete per CAN-SPAM-style expectations and common B2B norms.
UNSUBSCRIBE_LINE = (
    "If you would rather not receive further emails from us, reply with "
    "\"unsubscribe\" and we will remove your address from our list."
)

SERVICE_NAME = os.environ.get("OUTREACH_SERVICE_NAME", "custom embroidery digitizing and production")

# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------
TEMPLATES = {
    "New Client": {
        "subject": "Custom embroidery for {company_name} — free sample offer",
        "html": """<div style="font-family:Segoe UI,Arial,sans-serif;font-size:15px;line-height:1.55;color:#222">
<p>Hi {contact_person},</p>
<p>I work with embroidery digitizing and production for {company_name} — digitizing,
embroidery and screen printing, all under one roof.</p>
<p>If you are working on {service_name} for {company_name}, we can digitize your
logos and send a stitched sample so you can judge the quality before committing
to a run.</p>
<p>Happy to share our portfolio and rates.</p>
<p>Best regards,<br/>{sender_name}</p>
<hr style="border:none;border-top:1px solid #e3e3e3;margin:18px 0">
<p style="font-size:12px;color:#666">{sender_name} · {sender_email}<br/>{unsubscribe_line}</p>
</div>""",
        "text": """Hi {contact_person},

I work with embroidery digitizing and production for {company_name} — digitizing,
embroidery and screen printing, all under one roof.

If you are working on {service_name} for {company_name}, we can digitize your
logos and send a stitched sample so you can judge the quality before committing
to a run.

Happy to share our portfolio and rates.

Best regards,
{sender_name}

---
{sender_name} · {sender_email}
{unsubscribe_line}""",
    },
    "Sales Pitch": {
        "subject": "Bulk embroidery production for {company_name}",
        "html": """<div style="font-family:Segoe UI,Arial,sans-serif;font-size:15px;line-height:1.55;color:#222">
<p>Hi {contact_person},</p>
<p>We are a production house offering {service_name} to brands, uniforms and
promotional product companies.</p>
<p>Where we typically help {company_name}:</p>
<ul>
  <li>Logo digitizing from any artwork, 24&ndash;72 hour turnaround</li>
  <li>Stitch samples before bulk production</li>
  <li>Consistent quality across repeat runs</li>
</ul>
<p>Would a short call to compare notes on your current supplier be useful?</p>
<p>Best regards,<br/>{sender_name}</p>
<hr style="border:none;border-top:1px solid #e3e3e3;margin:18px 0">
<p style="font-size:12px;color:#666">{sender_name} · {sender_email}<br/>{unsubscribe_line}</p>
</div>""",
        "text": """Hi {contact_person},

We are a production house offering {service_name} to brands, uniforms and
promotional product companies.

Where we typically help {company_name}:
  - Logo digitizing from any artwork, 24-72 hour turnaround
  - Stitch samples before bulk production
  - Consistent quality across repeat runs

Would a short call to compare notes on your current supplier be useful?

Best regards,
{sender_name}

---
{sender_name} · {sender_email}
{unsubscribe_line}""",
    },
    "Follow-up": {
        "subject": "Following up — Embroidery Copilot",
        "html": """<div style="font-family:Segoe UI,Arial,sans-serif;font-size:15px;line-height:1.55;color:#222">
<p>Hi {contact_person},</p>
<p>Just following up on my earlier note about {service_name} for {company_name}.</p>
<p>Our portfolio covers recent work for apparel and uniform programs — happy to
send it over, along with a stitched sample of your own logo if that is useful.</p>
<p>Best regards,<br/>{sender_name}</p>
<hr style="border:none;border-top:1px solid #e3e3e3;margin:18px 0">
<p style="font-size:12px;color:#666">{sender_name} · {sender_email}<br/>{unsubscribe_line}</p>
</div>""",
        "text": """Hi {contact_person},

Just following up on my earlier note about {service_name} for {company_name}.

Our portfolio covers recent work for apparel and uniform programs — happy to
send it over, along with a stitched sample of your own logo if that is useful.

Best regards,
{sender_name}

---
{sender_name} · {sender_email}
{unsubscribe_line}""",
    },
}

_TEMPLATE_VARS = {
    "company_name", "contact_person", "service_name", "sender_name",
    "sender_email", "unsubscribe_line",
}


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


class SendError(RuntimeError):
    """Raised when a message could not be delivered."""

    def __init__(self, message, permanent=False, recipients=None):
        super().__init__(message)
        self.permanent = permanent
        self.recipients = recipients or []


# --------------------------------------------------------------------------
# Template rendering
# --------------------------------------------------------------------------
def _friendly_name(lead: dict) -> str:
    """Best available name for a lead, without inventing a person."""
    for field in ("name", "contact_person", "contact"):
        value = (lead.get(field) or "").strip()
        if value:
            return value
    company = (lead.get("company") or "").strip()
    if company:
        return f"the {company} team"
    local = store.email_domain(lead.get("email")).split(".")[0]
    return local or "there"


def build_context(lead: dict, service_name: str = SERVICE_NAME) -> dict:
    """Build the template variable context for one lead.

    Missing personalisation degrades gracefully instead of raising, and
    recipient-provided values are HTML-escaped by the renderer.
    """
    company = (lead.get("company") or "").strip()
    if not company:
        company = store.email_domain(lead.get("email")) or "your team"
    return {
        "company_name": company,
        "contact_person": _friendly_name(lead),
        "service_name": service_name,
        "sender_name": SENDER_NAME,
        "sender_email": SENDER_EMAIL,
        "unsubscribe_line": UNSUBSCRIBE_LINE,
    }


def render(template_name: str, lead: dict, service_name: str = SERVICE_NAME) -> dict:
    """Render a template for a lead -> {"subject", "html", "text"}.

    Unknown template names raise.  Missing variables are replaced with a safe
    placeholder rather than leaving a literal ``{brace}`` in the output.
    """
    template = TEMPLATES.get(template_name)
    if template is None:
        raise ConfigError(
            f"Unknown template {template_name!r}. Available: {', '.join(TEMPLATES)}"
        )
    context = build_context(lead, service_name)

    def fill(text: str, escape: bool) -> str:
        def repl(match):
            key = match.group(1).strip()
            if key in _TEMPLATE_VARS:
                value = str(context.get(key, ""))
                # Recipient-provided values are escaped for the HTML part
                # only; the plain-text part must stay readable.
                return html.escape(value, quote=True) if escape else value
            return ""
        return re.sub(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", repl, text)

    return {
        "subject": fill(template["subject"], escape=False),
        "html": fill(template["html"], escape=True),
        "text": fill(template["text"], escape=False),
    }


# --------------------------------------------------------------------------
# Message building
# --------------------------------------------------------------------------
def build_message(to: str, rendered: dict, attachments=None) -> MIMEMultipart:
    """Build a multipart MIME message: HTML body + plain-text alternative.

    Attachments are validated up front so a missing file is reported clearly
    instead of failing halfway through the SMTP transaction.
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = rendered["subject"]
    msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    msg["To"] = to
    msg["Reply-To"] = REPLY_TO
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Message-ID"] = email.utils.make_msgid(domain=store.email_domain(SENDER_EMAIL) or None)

    msg.attach(MIMEText(rendered["text"], "plain", "utf-8"))
    msg.attach(MIMEText(rendered["html"], "html", "utf-8"))

    for path in attachments or []:
        attachment_path = Path(path)
        if not attachment_path.is_file():
            raise SendError(f"Attachment not found: {attachment_path}", permanent=True)
        try:
            payload = attachment_path.read_bytes()
        except OSError as exc:
            raise SendError(f"Cannot read attachment {attachment_path}: {exc}", permanent=True)
        part = MIMEApplication(payload, _subtype="application/pdf")
        part.add_header("Content-Disposition", "attachment", filename=attachment_path.name)
        msg.attach(part)

    return msg


# --------------------------------------------------------------------------
# SMTP
# --------------------------------------------------------------------------
def _is_transient(exc: Exception) -> bool:
    """True for errors that may succeed on a later attempt."""
    if isinstance(exc, smtplib.SMTPResponseException):
        code = exc.smtp_code
        # 4xx = transient, 5xx = permanent.
        return 400 <= code < 500
    if isinstance(exc, (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError,
                        ConnectionError, TimeoutError, socket.timeout, OSError)):
        # OSError covers "connection reset" / "broken pipe" style failures.
        return True
    if isinstance(exc, smtplib.SMTPException):
        text = str(exc).lower()
        return any(w in text for w in ("timeout", "temporar", "try again", "connection"))
    return False


def _connect():
    """Open an authenticated STARTTLS SMTP session with Zoho."""
    if not SMTP_USER or not SMTP_PASS:
        raise ConfigError(
            "SMTP credentials are not configured. Set ZOHO_SMTP_USER and "
            "ZOHO_SMTP_PASS in your environment or in a local .env file. "
            "(Do not commit real credentials.)"
        )
    context = ssl.create_default_context()
    server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30)
    try:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        server.login(SMTP_USER, SMTP_PASS)
    except Exception:
        try:
            server.quit()
        except Exception:
            pass
        raise
    return server


def send_one(to: str, rendered: dict, attachments=None, server=None) -> str:
    """Send a single message over ``server``.  Returns the Message-ID.

    Transient failures are retried a bounded number of times with backoff.
    Permanent failures are never retried.  The caller owns the connection --
    this function never closes it.
    """
    message = build_message(to, rendered, attachments)
    message_id = message["Message-ID"]
    attempts = 0
    last_error = None

    while attempts < max(1, MAX_RETRIES):
        attempts += 1
        try:
            if server is None:
                server = _connect()
            server.send_message(message)
            return message_id
        except Exception as exc:  # noqa: BLE001 - classified below
            last_error = exc
            permanent = isinstance(exc, ConfigError) or (isinstance(exc, SendError) and exc.permanent)
            if not permanent and not _is_transient(exc):
                permanent = True
            if permanent:
                raise SendError(
                    f"{type(exc).__name__}: {exc}", permanent=True, recipients=[to]
                ) from exc
            if attempts >= max(1, MAX_RETRIES):
                break
            # A dropped connection may have been accepted by the server; the
            # log records the attempt either way, so the operator can check.
            delay = RETRY_BASE_DELAY * (2 ** (attempts - 1)) + random.uniform(0, 5)
            print(f"  transient error ({exc}); retrying in {delay:.0f}s "
                  f"[attempt {attempts}/{MAX_RETRIES}]")
            time.sleep(delay)
    raise SendError(
        f"giving up after {attempts} attempts: {type(last_error).__name__}: {last_error}",
        permanent=False, recipients=[to],
    )


# --------------------------------------------------------------------------
# Campaign selection
# --------------------------------------------------------------------------
def select_recipients(include_statuses=None) -> list:
    """Pick the leads a campaign may target.

    Only approved leads are eligible by default.  Suppressed, rejected and
    already-sent contacts are always excluded.
    """
    statuses = include_statuses or [store.ST_APPROVED]
    suppressed = store.load_suppressed()
    selected = []
    for lead in store.load_leads():
        email = store.normalize_email(lead.get("email"))
        if not email or not store.is_valid_email_syntax(email):
            continue
        if lead.get("status") not in statuses:
            continue
        if email in suppressed:
            continue
        if store.previously_sent(email):
            continue
        selected.append(lead)
    return selected


def resolve_attachments(names) -> list:
    """Resolve attachment names to paths, raising a clear error if missing."""
    paths = []
    for name in names or []:
        path = Path(name)
        if not path.is_absolute():
            path = ATTACHMENT_DIR / path
        if not path.is_file():
            raise ConfigError(f"Attachment not found: {path}")
        paths.append(path)
    return paths


# --------------------------------------------------------------------------
# Campaign
# --------------------------------------------------------------------------
def run_campaign(
    template_name: str,
    dry_run: bool = False,
    attachments=None,
    limit=None,
    on_event=None,
) -> dict:
    """Run one campaign.  Returns a summary dict.

    ``on_event`` is an optional callable used by the GUI to stream progress;
    it is always called from the calling thread, never from a worker.
    """
    def emit(kind, message, **extra):
        if on_event:
            try:
                on_event(kind, message, **extra)
            except Exception:  # noqa: BLE001 - progress must never break a send
                pass

    attachments = resolve_attachments(attachments)
    recipients = select_recipients()

    sent = 0
    failed = 0
    skipped = 0
    errors = []

    sent_today = store.sent_today_count()
    cap = DAILY_LIMIT if limit is None else min(limit, DAILY_LIMIT)
    remaining = max(0, cap - sent_today)

    emit("info", f"Campaign '{template_name}' | cap {DAILY_LIMIT}/day, "
                 f"sent today {sent_today}, eligible {len(recipients)}, remaining {remaining}")

    if not recipients:
        emit("warn", "No eligible recipients. Approve leads first "
                     "(Dashboard → Lead Queue → Approve Selected).")
        return {"sent": 0, "failed": 0, "skipped": 0, "errors": [],
                "recipients": [], "dry_run": dry_run}

    if remaining <= 0:
        emit("warn", f"Daily limit of {DAILY_LIMIT} reached. Nothing sent today.")
        return {"sent": 0, "failed": 0, "skipped": 0, "errors": [],
                "recipients": recipients, "dry_run": dry_run}

    if dry_run:
        for lead in recipients[:remaining]:
            rendered = render(template_name, lead)
            emit("preview", f"[DRY RUN] -> {lead.get('email')} | {rendered['subject']}")
        emit("info", f"Dry run complete. {min(len(recipients), remaining)} message(s) "
                     f"would be sent. Nothing was transmitted.")
        return {"sent": 0, "failed": 0, "skipped": 0, "errors": [],
                "recipients": recipients[:remaining], "dry_run": True}

    server = None
    try:
        for index, lead in enumerate(recipients):
            if index >= remaining:
                emit("warn", f"Daily limit of {DAILY_LIMIT} reached. Stopping cleanly.")
                break
            email = store.normalize_email(lead.get("email"))
            if index > 0:
                time.sleep(MIN_INTERVAL_SECONDS)
            try:
                rendered = render(template_name, lead)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                errors.append(f"{email}: {exc}")
                store.record_send_attempt(email, lead.get("name", ""), lead.get("company", ""),
                                          status="failed", error=f"render: {exc}")
                emit("error", f"render failed for {email}: {exc}")
                continue

            try:
                if server is None:
                    server = _connect()
                message_id = send_one(email, rendered, attachments, server=server)
            except SendError as exc:
                failed += 1
                errors.append(f"{email}: {exc}")
                store.record_send_attempt(email, lead.get("name", ""), lead.get("company", ""),
                                          status="failed", error=str(exc))
                store.set_status(email, store.ST_FAILED, reason=str(exc)[:200])
                emit("error", f"failed {email}: {exc}")
                if server is not None:
                    try:
                        server.quit()
                    except Exception:
                        pass
                    server = None
                continue
            except Exception as exc:  # noqa: BLE001
                failed += 1
                errors.append(f"{email}: {exc}")
                store.record_send_attempt(email, lead.get("name", ""), lead.get("company", ""),
                                          status="failed", error=str(exc))
                store.set_status(email, store.ST_FAILED, reason=str(exc)[:200])
                emit("error", f"failed {email}: {exc}")
                if server is not None:
                    try:
                        server.quit()
                    except Exception:
                        pass
                    server = None
                continue

            sent += 1
            store.record_send_attempt(email, lead.get("name", ""), lead.get("company", ""),
                                      status="sent", subject=rendered["subject"],
                                      message_id=message_id)
            store.set_status(email, store.ST_SENT)
            emit("sent", f"sent -> {email} | {rendered['subject']}")
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                pass

    emit("info", f"Campaign finished: {sent} sent, {failed} failed, {skipped} skipped.")
    return {"sent": sent, "failed": failed, "skipped": skipped, "errors": errors,
            "recipients": recipients, "dry_run": False}


def preview_one(template_name: str, lead: dict) -> dict:
    """Render one message for preview without sending anything."""
    return render(template_name, lead)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _load_dotenv() -> None:
    """Load a local .env file into the environment if python-dotenv is absent.

    Existing environment variables always win, so a real environment setting is
    never overwritten by the file.
    """
    env_path = store.BASE_DIR / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv  # type: ignore
        load_dotenv(env_path, override=False)
        return
    except ImportError:
        pass
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="EmbroideryCopilot Zoho outreach agent")
    parser.add_argument("--template", default="New Client", choices=list(TEMPLATES))
    parser.add_argument("--preview", action="store_true",
                        help="show rendered recipients/messages without sending")
    parser.add_argument("--dry-run", action="store_true",
                        help="render everything but never open an SMTP connection")
    parser.add_argument("--send", action="store_true", help="actually send")
    parser.add_argument("--confirm", action="store_true",
                        help="required with --send (human approval step)")
    parser.add_argument("--limit", type=int, default=None, help="max messages this run")
    parser.add_argument("--attachment", action="append", default=[],
                        help="attachment file name or path (repeatable)")
    args = parser.parse_args(argv)

    _load_dotenv()
    global SMTP_USER, SMTP_PASS, SMTP_HOST, SMTP_PORT
    SMTP_USER = os.environ.get("ZOHO_SMTP_USER", SMTP_USER)
    SMTP_PASS = os.environ.get("ZOHO_SMTP_PASS", "") or os.environ.get("SMTP_PASS", "")
    SMTP_HOST = os.environ.get("ZOHO_SMTP_HOST", SMTP_HOST)
    SMTP_PORT = int(os.environ.get("ZOHO_SMTP_PORT", str(SMTP_PORT)))

    if args.preview:
        recipients = select_recipients()
        print(f"\n=== PREVIEW: {len(recipients)} recipient(s) ===\n")
        for lead in recipients:
            rendered = preview_one(args.template, lead)
            print("-" * 70)
            print("To:      ", lead.get("email"))
            print("Name:    ", lead.get("name", ""))
            print("Company: ", lead.get("company", ""))
            print("Subject: ", rendered["subject"])
            print("Attachments:", ", ".join(args.attachment) or "(none)")
            print("-" * 70)
            print(rendered["text"])
            print()
        return 0

    if not args.send:
        parser.print_help()
        return 0

    if not args.confirm:
        print("Refusing to send without --confirm.")
        print("Run with --preview first, then --send --confirm when you are happy.")
        return 2

    if not SMTP_PASS:
        print("Refusing to send: ZOHO_SMTP_PASS is not set.")
        return 2

    if store.campaign_running():
        print("Refusing to start: another campaign is already running.")
        return 3

    attachments = args.attachment or DEFAULT_ATTACHMENTS

    try:
        with store.campaign_guard():
            summary = run_campaign(args.template, dry_run=args.dry_run,
                                   attachments=attachments, limit=args.limit)
    except RuntimeError as exc:
        print(f"Campaign could not start: {exc}")
        return 3
    except ConfigError as exc:
        print(f"Configuration problem: {exc}")
        return 2

    print(f"\nDone. sent={summary['sent']} failed={summary['failed']}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
