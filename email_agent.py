#!/usr/bin/env python3
"""Compatibility shim for the old ``email_agent.py`` entry point.

The original script was a minimal Gmail sender with no safeguards: it read
``SMTP_PASS`` (not the configured ``ZOHO_SMTP_PASS``), defaulted to
``smtp.gmail.com``, had no duplicate protection, no suppression list, no
dry-run and no attachments.

Rather than keeping two senders, this module delegates to
``email_agent_zoho.py``, which is the single supported sender.  The public
function names are kept so existing callers keep working.

Run it the same way as the new sender:
    python email_agent.py --preview
    python email_agent.py --send --confirm
"""
from __future__ import annotations

import sys

import email_agent_zoho as _zoho
from email_agent_zoho import (  # noqa: F401 - re-exported for existing callers
    ConfigError,
    SendError,
    build_context,
    build_message,
    render,
    run_campaign,
    select_recipients,
)

#: Legacy aliases kept so older code and scripts still import cleanly.
send_email = _zoho.send_one
bulk_send = _zoho.run_campaign
TEMPLATES = _zoho.TEMPLATES
DAILY_LIMIT = _zoho.DAILY_LIMIT
SENT_LOG = str(_zoho.store.SENT_LOG_FILE)


def main(argv=None) -> int:
    """Forward to the Zoho agent's CLI."""
    return _zoho.main(argv)


if __name__ == "__main__":
    sys.exit(main())
