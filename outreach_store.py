#!/usr/bin/env python3
"""Shared persistence + campaign-safety layer for the EmbroideryCopilot Outreach CRM.

Every component (app_dashboard.py, email_agent_zoho.py, lead_finder.py,
telegram_bot.py) reads and writes lead state through this module so the
desktop GUI and the Telegram bot can never disagree about a lead's status and
can never write the same CSV concurrently.

Design rules:
  * Existing user data and historical records are never truncated or silently
    dropped.  Writes are atomic (temp file + os.replace) so a crash mid-write
    cannot leave a half-written CSV behind.
  * Reads are tolerant: a missing file, an empty file, a header-less file or a
    row with the wrong number of columns must not raise.
  * All paths are anchored to the project root, so the tools work regardless of
    the current working directory.
"""
from __future__ import annotations

import csv
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------------
# Paths -- always anchored to the project root, never to the CWD.
# --------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent

LEADS_FILE = BASE_DIR / "leads.csv"
SENT_LOG_FILE = BASE_DIR / "sent_log.csv"
REJECTED_FILE = BASE_DIR / "rejected_leads.csv"
SUPPRESSED_FILE = BASE_DIR / "suppressed.csv"
CAMPAIGN_LOCK = BASE_DIR / "campaign.lock"

# Lead statuses used across the whole app.
ST_PENDING = "pending"
ST_APPROVED = "approved"
ST_SENT = "sent"
ST_FAILED = "failed"
ST_REJECTED = "rejected"
ST_SUPPRESSED = "suppressed"

ALL_STATUSES = (ST_PENDING, ST_APPROVED, ST_SENT, ST_FAILED, ST_REJECTED, ST_SUPPRESSED)

# The original five columns keep their original names AND order so existing
# rows and older tooling stay compatible.  New columns are appended right.
LEAD_FIELDS = [
    "name", "email", "subject", "company", "niche",
    "source_url", "discovered_at", "status", "verified", "notes",
]
ORIGINAL_LEAD_FIELDS = ["name", "email", "subject", "company", "niche"]

SENT_LOG_FIELDS = [
    "timestamp", "email", "name", "company", "status", "subject", "message_id", "error",
]
REJECTED_FIELDS = ["timestamp", "email", "name", "company", "reason"]
SUPPRESSED_FIELDS = ["email", "timestamp", "reason"]

# In-process guard so two threads inside one process cannot interleave a
# read-modify-write of the same file.  Always acquired OUTSIDE the file lock.
_THREAD_LOCKS: dict[str, threading.RLock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


def _thread_lock(name: str) -> threading.RLock:
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(name)
        if lock is None:
            lock = threading.RLock()
            _THREAD_LOCKS[name] = lock
        return lock


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")

# Hosts whose mailboxes are throwaway / no-reply.  Kept in code so the app
# works offline; extend the file next to the project if needed.
DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "guerrillamail.net", "guerrillamail.org",
    "10minutemail.com", "10minutemail.net", "tempmail.com", "temp-mail.org",
    "temp-mail.io", "throwawaymail.com", "yopmail.com", "trashmail.com",
    "sharklasers.com", "getnada.com", "dispostable.com", "maildrop.cc",
    "fakeinbox.com", "mailnesia.com", "mytemp.email", "emailondeck.com",
    "spam4.me", "grr.la", "moakt.com", "tmpmail.net", "tempr.email",
}


def now_iso() -> str:
    """Local-time ISO-8601 timestamp with offset (sorts correctly, unambiguous)."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def today_key() -> str:
    """Local calendar day, used for the daily sending cap."""
    return datetime.now().strftime("%Y-%m-%d")


def normalize_email(value) -> str:
    """Lower-case, trim and strip noise from an email address.

    Returns "" when the value is missing or has no usable local/domain part.
    This is *syntax* normalisation only -- it never claims a mailbox exists.
    """
    if not value:
        return ""
    cleaned = str(value).strip().strip("<>").strip()
    cleaned = cleaned.rstrip(".,;:!?)")
    if "@" not in cleaned:
        return ""
    local, _, domain = cleaned.rpartition("@")
    local = local.strip().strip(".").lower()
    domain = domain.strip().strip(".").lower()
    if not local or not domain:
        return ""
    return f"{local}@{domain}"


def email_domain(value) -> str:
    """Domain part of an email address, or ""."""
    normalized = normalize_email(value)
    return normalized.split("@", 1)[1] if normalized else ""


def is_valid_email_syntax(value) -> bool:
    """Pure syntax check -- NOT proof that the mailbox exists."""
    normalized = normalize_email(value)
    return bool(normalized) and bool(_EMAIL_RE.match(normalized))


def is_disposable(value) -> bool:
    """True when the address sits on a known throwaway-mail provider."""
    domain = email_domain(value)
    if not domain:
        return False
    return domain in DISPOSABLE_DOMAINS or domain.endswith(".mailinator.com")


def sanitize_cell(value) -> str:
    """Neutralise spreadsheet formula injection (values starting with = + - @)."""
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@"):
        return "'" + text
    return text


# --------------------------------------------------------------------------
# Locking
# --------------------------------------------------------------------------
_STALE_LOCK_SECONDS = 900  # a lock older than 15 min is assumed to be a crash


@contextmanager
def file_lock(name: str = "outreach.lock", timeout: float = 15.0):
    """Cross-process advisory lock based on an exclusive lock file.

    Stops the desktop GUI and the Telegram bot from interleaving writes to the
    same CSV.  Raises TimeoutError if the lock cannot be taken in time.
    """
    lock_path = BASE_DIR / f"{name}.lock"
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            # Recover from a lock left behind by a crashed process.
            try:
                age = time.time() - lock_path.stat().st_mtime
                if age > _STALE_LOCK_SECONDS:
                    lock_path.unlink()
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Timed out waiting for lock '{lock_path.name}'. "
                    "Another outreach task is probably still running."
                )
            time.sleep(0.05)
    try:
        os.write(fd, str(os.getpid()).encode("ascii", "ignore"))
        os.close(fd)
        fd = None
        yield lock_path
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            lock_path.unlink()
        except OSError:
            pass


# --------------------------------------------------------------------------
# CSV primitives
# --------------------------------------------------------------------------
def _looks_like_header(row: list[str], fieldnames: list[str] | None) -> bool:
    """Heuristic: is this row a header row?

    A row is a header when every cell is a known field name, or when the file
    has no known schema and the row is entirely non-numeric non-empty text.
    """
    if not row:
        return False
    cells = [c.strip().lower() for c in row if c.strip()]
    if not cells:
        return True
    if fieldnames:
        known = {f.lower() for f in fieldnames}
        if all(c in known for c in cells):
            return True
        # A data row would contain an email address in the email column.
        return False
    return False


def read_rows(path, fieldnames: list[str] | None = None) -> list[dict]:
    """Read a CSV into a list of dicts, tolerating real-world damage.

    Missing file -> [].  Empty file -> [].  Header-less file -> rows are
    projected positionally onto ``fieldnames``.  Rows with the wrong column
    count are padded/truncated rather than raising.
    """
    path = Path(path)
    if not path.exists():
        return []
    try:
        if path.stat().st_size == 0:
            return []
        with path.open("r", newline="", encoding="utf-8-sig", errors="replace") as handle:
            raw_rows = [row for row in csv.reader(handle)]
    except (OSError, csv.Error):
        return []

    raw_rows = [r for r in raw_rows if r and any(c.strip() for c in r)]
    if not raw_rows:
        return []

    header: list[str]
    body: list[list[str]]
    if _looks_like_header(raw_rows[0], fieldnames):
        header = [c.strip() for c in raw_rows[0]]
        body = raw_rows[1:]
    else:
        # Header-less file: fall back to the known schema, or to positional
        # generic names when we have no schema for this file.
        header = list(fieldnames) if fieldnames else [f"col{i}" for i in range(len(raw_rows[0]))]
        body = raw_rows

    rows = []
    for raw in body:
        padded = list(raw[: len(header)]) + [""] * max(0, len(header) - len(raw))
        rows.append({header[i]: padded[i] for i in range(len(header))})

    if fieldnames:
        rows = [{f: sanitize_cell(row.get(f, "")).strip() for f in fieldnames} for row in rows]
    else:
        rows = [{k: sanitize_cell(v).strip() for k, v in row.items()} for row in rows]
    return rows


def write_rows(path, fieldnames: list[str], rows: list[dict]) -> None:
    """Atomically write a CSV: temp file in the same directory, then replace.

    The original file is only ever replaced by a complete new file, so a crash
    can never leave a truncated CSV behind.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({f: sanitize_cell(row.get(f, "")) for f in fieldnames})
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(tmp_path), str(path))
    except Exception:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def append_row(path, fieldnames: list[str], row: dict) -> None:
    """Append one row to a CSV, creating it with a header if needed.

    The append happens while holding the file lock so a concurrent writer
    cannot interleave a partial line into the file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _thread_lock(path.name):
        with file_lock(f"{path.name}.write"):
            exists = path.exists() and path.stat().st_size > 0
            with path.open("a", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
                if not exists:
                    writer.writeheader()
                writer.writerow({f: sanitize_cell(row.get(f, "")) for f in fieldnames})


# --------------------------------------------------------------------------
# Lead ledger
# --------------------------------------------------------------------------
def load_leads() -> list[dict]:
    """All known leads, projected onto LEAD_FIELDS, in file order."""
    rows = read_rows(LEADS_FILE, LEAD_FIELDS)
    for row in rows:
        if not row.get("status"):
            row["status"] = ST_PENDING
        if not row.get("verified"):
            row["verified"] = "unknown"
    return rows


def save_leads(rows: list[dict]) -> None:
    """Persist the lead ledger atomically, preserving every field we know."""
    write_rows(LEADS_FILE, LEAD_FIELDS, rows)


def leads_by_status(status: str) -> list[dict]:
    return [row for row in load_leads() if row.get("status") == status]


def count_by_status() -> dict:
    """Counts for every status, used to drive the dashboard counters."""
    counts = {status: 0 for status in ALL_STATUSES}
    for row in load_leads():
        status = row.get("status") or ST_PENDING
        counts[status] = counts.get(status, 0) + 1
    return counts


def find_lead(email: str):
    """First lead row matching the normalised address, or None."""
    target = normalize_email(email)
    if not target:
        return None
    for row in load_leads():
        if normalize_email(row.get("email")) == target:
            return row
    return None


def upsert_lead(lead: dict) -> bool:
    """Insert or update a lead by normalised email.  Returns True if added.

    Existing rows are never overwritten wholesale: only fields that are
    actually provided are updated, so a rediscovery cannot wipe a status or a
    verification result.
    """
    email = normalize_email(lead.get("email"))
    if not email:
        return False
    with _thread_lock(LEADS_FILE.name):
        with file_lock(f"{LEADS_FILE.name}.write"):
            rows = load_leads()
            for row in rows:
                if normalize_email(row.get("email")) == email:
                    for key, value in lead.items():
                        if key in ("email", "status"):
                            continue
                        if value not in (None, ""):
                            row[key] = value
                    row["email"] = email
                    save_leads(rows)
                    return False
            new_row = {f: "" for f in LEAD_FIELDS}
            for key, value in lead.items():
                if key in LEAD_FIELDS:
                    new_row[key] = value
            new_row["email"] = email
            if not new_row.get("status"):
                new_row["status"] = ST_PENDING
            if not new_row.get("verified"):
                new_row["verified"] = "unknown"
            if not new_row.get("discovered_at"):
                new_row["discovered_at"] = now_iso()
            rows.append(new_row)
            save_leads(rows)
            return True


def set_status(email: str, status: str, reason: str = "") -> bool:
    """Move one lead to a new status.  Returns True if something changed."""
    if status not in ALL_STATUSES:
        raise ValueError(f"Unknown status: {status!r}")
    target = normalize_email(email)
    if not target:
        return False
    with _thread_lock(LEADS_FILE.name):
        with file_lock(f"{LEADS_FILE.name}.write"):
            rows = load_leads()
            changed = False
            for row in rows:
                if normalize_email(row.get("email")) == target:
                    row["status"] = status
                    if reason:
                        row["notes"] = reason
                    changed = True
            if changed:
                save_leads(rows)
            return changed


def set_status_bulk(emails, status: str, reason: str = "") -> int:
    """Move several leads to a new status in one atomic write."""
    targets = {normalize_email(e) for e in emails if normalize_email(e)}
    if not targets:
        return 0
    if status not in ALL_STATUSES:
        raise ValueError(f"Unknown status: {status!r}")
    with _thread_lock(LEADS_FILE.name):
        with file_lock(f"{LEADS_FILE.name}.write"):
            rows = load_leads()
            changed = 0
            for row in rows:
                if normalize_email(row.get("email")) in targets:
                    row["status"] = status
                    if reason:
                        row["notes"] = reason
                    changed += 1
            if changed:
                save_leads(rows)
            return changed


# --------------------------------------------------------------------------
# Rejection + suppression
# --------------------------------------------------------------------------
def record_rejection(email: str, name: str = "", company: str = "", reason: str = "") -> None:
    """Append a rejection to rejected_leads.csv."""
    append_row(
        REJECTED_FILE,
        REJECTED_FIELDS,
        {
            "timestamp": now_iso(),
            "email": normalize_email(email),
            "name": name,
            "company": company,
            "reason": reason,
        },
    )


def load_rejections() -> list[dict]:
    return read_rows(REJECTED_FILE, REJECTED_FIELDS)


def load_suppressed() -> dict:
    """Suppressed addresses -> reason.  This list is never automatically cleared."""
    out: dict = {}
    for row in read_rows(SUPPRESSED_FILE, SUPPRESSED_FIELDS):
        email = normalize_email(row.get("email"))
        if email:
            out[email] = row.get("reason", "")
    return out


def suppress(email: str, reason: str = "manual") -> bool:
    """Add an address to the permanent suppression list.

    Suppressed contacts are never re-added to a campaign by any code path.
    """
    target = normalize_email(email)
    if not target:
        return False
    if target in load_suppressed():
        return False
    append_row(
        SUPPRESSED_FILE,
        SUPPRESSED_FIELDS,
        {"email": target, "timestamp": now_iso(), "reason": reason},
    )
    return True


def is_suppressed(email: str) -> bool:
    return normalize_email(email) in load_suppressed()


# --------------------------------------------------------------------------
# Send history
# --------------------------------------------------------------------------
def load_sent_log() -> list[dict]:
    return read_rows(SENT_LOG_FILE, SENT_LOG_FIELDS)


def sent_today_count() -> int:
    """How many messages were sent today (for the daily cap)."""
    today = today_key()
    return sum(1 for row in load_sent_log() if row.get("timestamp", "")[:10] == today)


def previously_sent(email: str) -> bool:
    """True when the address already has a successful send on record."""
    target = normalize_email(email)
    if not target:
        return False
    for row in load_sent_log():
        if normalize_email(row.get("email")) == target and row.get("status") == "sent":
            return True
    return False


def record_send_attempt(
    email: str,
    name: str = "",
    company: str = "",
    status: str = "",
    subject: str = "",
    message_id: str = "",
    error: str = "",
) -> None:
    """Append one delivery attempt (sent or failed) to sent_log.csv."""
    append_row(
        SENT_LOG_FILE,
        SENT_LOG_FIELDS,
        {
            "timestamp": now_iso(),
            "email": normalize_email(email),
            "name": name,
            "company": company,
            "status": status,
            "subject": subject,
            "message_id": message_id,
            "error": error,
        },
    )


# --------------------------------------------------------------------------
# Campaign lock -- stops a second campaign starting while one is running.
# --------------------------------------------------------------------------
@contextmanager
def campaign_guard(timeout: float = 2.0):
    """Exclusive guard around a sending campaign.

    Raises RuntimeError if a campaign is already running.  The lock file is
    stale-tolerant so a crashed campaign cannot block the app forever.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(str(CAMPAIGN_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {now_iso()}".encode("ascii", "ignore"))
            os.close(fd)
            break
        except FileExistsError:
            try:
                age = time.time() - CAMPAIGN_LOCK.stat().st_mtime
                if age > _STALE_LOCK_SECONDS:
                    CAMPAIGN_LOCK.unlink()
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError(
                    "A campaign is already running. Wait for it to finish, or "
                    "delete campaign.lock if you are sure nothing is running."
                )
            time.sleep(0.05)
    try:
        yield CAMPAIGN_LOCK
    finally:
        try:
            CAMPAIGN_LOCK.unlink()
        except OSError:
            pass


def campaign_running() -> bool:
    """True when a campaign lock is currently held (and not stale)."""
    if not CAMPAIGN_LOCK.exists():
        return False
    try:
        return (time.time() - CAMPAIGN_LOCK.stat().st_mtime) <= _STALE_LOCK_SECONDS
    except OSError:
        return False


__all__ = [
    "BASE_DIR", "LEADS_FILE", "SENT_LOG_FILE", "REJECTED_FILE", "SUPPRESSED_FILE",
    "LEAD_FIELDS", "SENT_LOG_FIELDS", "REJECTED_FIELDS", "SUPPRESSED_FIELDS",
    "ST_PENDING", "ST_APPROVED", "ST_SENT", "ST_FAILED", "ST_REJECTED", "ST_SUPPRESSED",
    "ALL_STATUSES", "DISPOSABLE_DOMAINS",
    "now_iso", "today_key", "normalize_email", "email_domain", "is_valid_email_syntax",
    "is_disposable", "sanitize_cell", "file_lock", "read_rows", "write_rows", "append_row",
    "load_leads", "save_leads", "leads_by_status", "count_by_status", "find_lead",
    "upsert_lead", "set_status", "set_status_bulk", "record_rejection", "load_rejections",
    "load_suppressed", "suppress", "is_suppressed", "load_sent_log", "sent_today_count",
    "previously_sent", "record_send_attempt", "campaign_guard", "campaign_running",
]
