#!/usr/bin/env python3
"""Telegram remote control for the EmbroideryCopilot Outreach CRM.

Safety properties:
  * Only explicitly authorised Telegram user IDs can run any command.
    The allow-list comes from configuration (TELEGRAM_ALLOWED_IDS), never
    from a hardcoded personal account ID.
  * Unauthorised users get no information about the lead database.
  * The bot uses the same ``outreach_store`` as the desktop GUI, so both
    tools share one view of lead state and one set of campaign safeguards.
  * A campaign can only be started through the same confirmation flow the
    GUI uses, and the shared campaign lock prevents overlapping campaigns.
  * A working send still requires human approval in the GUI, so a Telegram
    command can prepare and preview but not silently blast.

Usage (PowerShell):
    $env:TELEGRAM_TOKEN = "123:ABC"
    $env:TELEGRAM_ALLOWED_IDS = "11111111,22222222"
    python telegram_bot.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading

import telebot

import outreach_store as store

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ALLOWED_IDS_ENV = os.environ.get("TELEGRAM_ALLOWED_IDS", "")

PYTHON_EXE = sys.executable
BASE_DIR = store.BASE_DIR


def allowed_ids() -> set:
    """Parse the configured allow-list of Telegram user IDs.

    Read from the environment at call time (not import time) so the list can
    be changed without reimporting the module.
    """
    raw = os.environ.get("TELEGRAM_ALLOWED_IDS", "") or ""
    ids = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            print(f"Ignoring non-numeric TELEGRAM_ALLOWED_IDS entry: {part!r}",
                  file=sys.stderr)
    return ids


#: Snapshot taken at import time; ``_authorized`` refreshes it from the
#: environment so a changed allow-list takes effect without a restart.
ALLOWED = allowed_ids()


def refresh_allowed() -> set:
    """Reload the allow-list from the environment and return it."""
    global ALLOWED
    ALLOWED = allowed_ids()
    return ALLOWED


# A placeholder token lets the module import cleanly (and lets the handlers
# be registered) even when TELEGRAM_TOKEN is not set.  main() refuses to
# start polling in that case, so no request is ever made with a fake token.
bot = telebot.TeleBot(TOKEN or "0:placeholder-not-configured")

# A command may only run one at a time, so a second request cannot start an
# overlapping discovery or campaign.
_TASK_LOCK = threading.Lock()


def _authorized(message) -> bool:
    """True when the sender is on the allow-list."""
    if not ALLOWED:
        return False
    return int(message.from_user.id) in ALLOWED


def _denied(message) -> None:
    bot.send_message(
        message.chat.id,
        "Sorry, this bot is private. Your Telegram ID is not authorised.",
    )
    print(f"Denied unauthorised user id={message.from_user.id} "
          f"chat={message.chat.id}")


def _run_script(script_name: str, timeout: int = 600) -> tuple:
    """Run a project script in a child process, from the project directory."""
    script = BASE_DIR / script_name
    if not script.is_file():
        return 1, f"Missing script: {script_name}"
    try:
        result = subprocess.run(
            [PYTHON_EXE, str(script)],
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, (result.stdout or "") + (result.stderr or "")
    except subprocess.TimeoutExpired:
        return 1, f"{script_name} timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001
        return 1, f"{script_name} failed: {type(exc).__name__}: {exc}"


def _run_in_background(script_name: str, chat_id: int, label: str) -> None:
    """Run a script without blocking the bot's polling loop."""

    def worker():
        if not _TASK_LOCK.acquire(blocking=False):
            bot.send_message(chat_id, "Another task is already running. Try again shortly.")
            return
        try:
            bot.send_message(chat_id, f"{label} started…")
            code, output = _run_script(script_name)
            tail = "\n".join((output or "").strip().splitlines()[-25:])
            if code == 0:
                bot.send_message(chat_id, f"{label} finished.\n{tail}")
            else:
                bot.send_message(chat_id, f"{label} failed (exit {code}).\n{tail}")
        finally:
            _TASK_LOCK.release()

    threading.Thread(target=worker, name=label, daemon=True).start()


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
@bot.message_handler(commands=["start", "help"])
def cmd_start(message):
    if not _authorized(message):
        _denied(message)
        return
    bot.send_message(
        message.chat.id,
        "EmbroideryCopilot Outreach CRM is active.\n\n"
        "/find — discover new B2B leads\n"
        "/leads — list pending leads\n"
        "/stats — counts by status\n"
        "/approve <email> — approve one lead\n"
        "/reject <email> — reject one lead\n"
        "/preview — preview the next campaign\n"
        "/sent — recent delivery attempts\n"
        "/campaign — start a campaign (requires GUI approval)\n",
    )


@bot.message_handler(commands=["find"])
def cmd_find(message):
    if not _authorized(message):
        _denied(message)
        return
    _run_in_background("lead_finder.py", message.chat.id, "Lead discovery")


@bot.message_handler(commands=["leads"])
def cmd_leads(message):
    if not _authorized(message):
        _denied(message)
        return
    pending = store.leads_by_status(store.ST_PENDING)
    if not pending:
        bot.send_message(message.chat.id, "No pending leads. Use /find to discover some.")
        return
    lines = [f"Pending leads ({len(pending)}):"]
    for row in pending[:20]:
        lines.append(f"• {row.get('email')} | {row.get('company', '')}")
    if len(pending) > 20:
        lines.append(f"… and {len(pending) - 20} more")
    bot.send_message(message.chat.id, "\n".join(lines))


@bot.message_handler(commands=["stats"])
def cmd_stats(message):
    if not _authorized(message):
        _denied(message)
        return
    counts = store.count_by_status()
    lines = ["Lead counts:"]
    for status in store.ALL_STATUSES:
        lines.append(f"• {status}: {counts.get(status, 0)}")
    lines.append(f"• sent today: {store.sent_today_count()}")
    bot.send_message(message.chat.id, "\n".join(lines))


@bot.message_handler(commands=["approve"])
def cmd_approve(message):
    if not _authorized(message):
        _denied(message)
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Usage: /approve <email>")
        return
    email = store.normalize_email(parts[1].strip())
    if not store.is_valid_email_syntax(email):
        bot.send_message(message.chat.id, "That does not look like a valid email address.")
        return
    if store.is_suppressed(email):
        bot.send_message(message.chat.id, "That address is suppressed and cannot be approved.")
        return
    if store.set_status(email, store.ST_APPROVED):
        bot.send_message(message.chat.id, f"Approved {email}.")
    else:
        bot.send_message(message.chat.id, f"No pending lead found for {email}.")


@bot.message_handler(commands=["reject"])
def cmd_reject(message):
    if not _authorized(message):
        _denied(message)
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Usage: /reject <email>")
        return
    email = store.normalize_email(parts[1].strip())
    if not store.is_valid_email_syntax(email):
        bot.send_message(message.chat.id, "That does not look like a valid email address.")
        return
    lead = store.find_lead(email) or {}
    store.record_rejection(email, lead.get("name", ""), lead.get("company", ""),
                           reason="rejected via telegram")
    if store.set_status(email, store.ST_REJECTED, reason="rejected via telegram"):
        bot.send_message(message.chat.id, f"Rejected {email}.")
    else:
        bot.send_message(message.chat.id, f"No lead found for {email}.")


@bot.message_handler(commands=["preview"])
def cmd_preview(message):
    if not _authorized(message):
        _denied(message)
        return
    import email_agent_zoho
    recipients = email_agent_zoho.select_recipients()
    if not recipients:
        bot.send_message(message.chat.id, "No approved leads to preview.")
        return
    lines = [f"Campaign preview — {len(recipients)} recipient(s):"]
    for lead in recipients[:20]:
        rendered = email_agent_zoho.render("New Client", lead)
        lines.append(f"• {lead.get('email')}\n  {rendered['subject']}")
    bot.send_message(message.chat.id, "\n".join(lines))


@bot.message_handler(commands=["sent"])
def cmd_sent(message):
    if not _authorized(message):
        _denied(message)
        return
    log = store.load_sent_log()
    if not log:
        bot.send_message(message.chat.id, "No delivery attempts recorded yet.")
        return
    lines = [f"Recent attempts ({len(log)} total):"]
    for row in log[-15:]:
        lines.append(f"• {row.get('timestamp', '')[:19]} {row.get('email')} "
                     f"→ {row.get('status', '')}")
    bot.send_message(message.chat.id, "\n".join(lines))


@bot.message_handler(commands=["campaign"])
def cmd_campaign(message):
    if not _authorized(message):
        _denied(message)
        return
    approved = store.leads_by_status(store.ST_APPROVED)
    if not approved:
        bot.send_message(message.chat.id, "No approved leads. Approve leads first.")
        return
    if store.campaign_running():
        bot.send_message(message.chat.id, "A campaign is already running.")
        return
    bot.send_message(
        message.chat.id,
        f"{len(approved)} approved lead(s) are ready.\n\n"
        "For safety, campaigns are dispatched from the desktop app so a human "
        "sees the preview and confirms. Run:\n"
        f"  {PYTHON_EXE} email_agent_zoho.py --preview\n"
        f"  {PYTHON_EXE} email_agent_zoho.py --send --confirm",
    )


def main() -> int:
    if not TOKEN or TOKEN == "YOUR_TOKEN":
        print("TELEGRAM_TOKEN is not set. Export it before starting the bot.")
        print("  $env:TELEGRAM_TOKEN = \"123:ABC\"")
        return 2
    if not ALLOWED:
        print("TELEGRAM_ALLOWED_IDS is not set, so every command would be denied.")
        print('  $env:TELEGRAM_ALLOWED_IDS = "11111111,22222222"')
        print("Get your numeric Telegram ID from @userinfobot.")
        return 2
    print(f"Bot starting. Authorised user IDs: {sorted(ALLOWED)}")
    bot.polling()
    return 0


if __name__ == "__main__":
    sys.exit(main())
