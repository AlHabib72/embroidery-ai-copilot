#!/usr/bin/env python3
"""Tests for the EmbroideryCopilot Outreach CRM.

Rules for this file:
  * No network access and no real SMTP.  The SMTP path is exercised through
    a fake connection object.
  * Every test uses its own temporary directory for CSV state, so the real
    leads.csv / sent_log.csv are never touched.
  * The module-level paths in outreach_store are redirected per-test.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import email_agent_zoho  # noqa: E402
import outreach_store as store  # noqa: E402


# --------------------------------------------------------------------------
# Fixture: redirect every CSV path to a temp directory
# --------------------------------------------------------------------------
@pytest.fixture()
def tmp_store(tmp_path, monkeypatch):
    """Point outreach_store at an isolated temp directory."""
    monkeypatch.setattr(store, "BASE_DIR", tmp_path)
    monkeypatch.setattr(store, "LEADS_FILE", tmp_path / "leads.csv")
    monkeypatch.setattr(store, "SENT_LOG_FILE", tmp_path / "sent_log.csv")
    monkeypatch.setattr(store, "REJECTED_FILE", tmp_path / "rejected_leads.csv")
    monkeypatch.setattr(store, "SUPPRESSED_FILE", tmp_path / "suppressed.csv")
    monkeypatch.setattr(store, "CAMPAIGN_LOCK", tmp_path / "campaign.lock")
    return tmp_path


# --------------------------------------------------------------------------
# CSV robustness
# --------------------------------------------------------------------------
def test_read_rows_missing_file_returns_empty(tmp_store):
    assert store.read_rows(tmp_store / "nope.csv", store.LEAD_FIELDS) == []


def test_read_rows_empty_file_returns_empty(tmp_store):
    path = tmp_store / "empty.csv"
    path.write_text("", encoding="utf-8")
    assert store.read_rows(path, store.LEAD_FIELDS) == []


def test_read_rows_header_only(tmp_store):
    path = tmp_store / "h.csv"
    path.write_text("name,email,subject,company,niche\n", encoding="utf-8")
    assert store.read_rows(path, store.LEAD_FIELDS) == []


def test_read_rows_short_row_is_padded(tmp_store):
    path = tmp_store / "short.csv"
    path.write_text("name,email,subject,company,niche\nAcme,a@b.com\n", encoding="utf-8")
    rows = store.read_rows(path, store.LEAD_FIELDS)
    assert len(rows) == 1
    assert rows[0]["email"] == "a@b.com"
    assert rows[0]["company"] == ""


def test_read_rows_long_row_is_truncated(tmp_store):
    path = tmp_store / "long.csv"
    path.write_text("name,email,subject,company,niche\nAcme,a@b.com,s,c,n,extra,more\n",
                    encoding="utf-8")
    rows = store.read_rows(path, store.LEAD_FIELDS)
    assert len(rows) == 1
    assert rows[0]["niche"] == "n"


def test_read_rows_headerless_file(tmp_store):
    """A CSV written without a header (the old email_agent.py bug) still loads."""
    path = tmp_store / "nohdr.csv"
    path.write_text("Acme,a@b.com,s,c,n\nBeta,d@e.com,s,c,n\n", encoding="utf-8")
    rows = store.read_rows(path, store.LEAD_FIELDS)
    assert len(rows) == 2
    assert rows[0]["email"] == "a@b.com"
    assert rows[1]["name"] == "Beta"


def test_write_rows_is_atomic_and_round_trips(tmp_store):
    rows = [{"name": "Acme", "email": "a@b.com"}]
    store.write_rows(store.LEADS_FILE, store.LEAD_FIELDS, rows)
    assert store.LEADS_FILE.exists()
    loaded = store.load_leads()
    assert len(loaded) == 1
    assert loaded[0]["email"] == "a@b.com"
    # No stray temp files left behind.
    assert not list(tmp_store.glob("*.tmp"))


def test_write_rows_preserves_existing_data(tmp_store):
    """write_rows replaces the file; upsert_lead is what preserves records."""
    store.upsert_lead({"email": "a@b.com", "name": "Acme"})
    store.upsert_lead({"email": "b@c.com", "name": "Beta"})
    loaded = store.load_leads()
    assert {r["email"] for r in loaded} == {"a@b.com", "b@c.com"}
    assert {r["name"] for r in loaded} == {"Acme", "Beta"}


def test_sanitize_cell_blocks_formula_injection():
    assert store.sanitize_cell("=cmd|'/c calc'!A1").startswith("'")
    assert store.sanitize_cell("+1234").startswith("'")
    assert store.sanitize_cell("normal") == "normal"


# --------------------------------------------------------------------------
# Email normalisation
# --------------------------------------------------------------------------
def test_normalize_email():
    assert store.normalize_email("  Info@Example.COM ") == "info@example.com"
    assert store.normalize_email("<a@b.com>") == "a@b.com"
    assert store.normalize_email("a@b.com.") == "a@b.com"
    assert store.normalize_email("not-an-email") == ""
    assert store.normalize_email(None) == ""


def test_is_valid_email_syntax():
    assert store.is_valid_email_syntax("a@b.com")
    assert not store.is_valid_email_syntax("a@b")
    assert not store.is_valid_email_syntax("a b@c.com")
    assert not store.is_valid_email_syntax("")


def test_is_disposable():
    assert store.is_disposable("x@mailinator.com")
    assert not store.is_disposable("x@realcompany.com")


# --------------------------------------------------------------------------
# Lead ledger
# --------------------------------------------------------------------------
def test_upsert_lead_inserts_and_dedupes(tmp_store):
    assert store.upsert_lead({"email": "A@B.com", "name": "Acme"}) is True
    assert store.upsert_lead({"email": "a@b.com", "name": "Acme Corp"}) is False
    leads = store.load_leads()
    assert len(leads) == 1
    # A rediscovery must not wipe an existing status.
    store.set_status("a@b.com", store.ST_APPROVED)
    store.upsert_lead({"email": "a@b.com", "name": "Acme Corp"})
    assert store.load_leads()[0]["status"] == store.ST_APPROVED


def test_upsert_lead_keeps_existing_status(tmp_store):
    store.upsert_lead({"email": "a@b.com"})
    store.set_status("a@b.com", store.ST_SENT)
    store.upsert_lead({"email": "a@b.com", "company": "New Co"})
    lead = store.find_lead("a@b.com")
    assert lead["status"] == store.ST_SENT
    assert lead["company"] == "New Co"


def test_set_status_bulk(tmp_store):
    for email in ("a@b.com", "c@d.com", "e@f.com"):
        store.upsert_lead({"email": email})
    changed = store.set_status_bulk(["a@b.com", "c@d.com"], store.ST_APPROVED)
    assert changed == 2
    assert len(store.leads_by_status(store.ST_APPROVED)) == 2


def test_set_status_rejects_unknown_status(tmp_store):
    store.upsert_lead({"email": "a@b.com"})
    with pytest.raises(ValueError):
        store.set_status("a@b.com", "nonsense")


def test_count_by_status(tmp_store):
    store.upsert_lead({"email": "a@b.com"})
    store.upsert_lead({"email": "c@d.com"})
    store.set_status("c@d.com", store.ST_APPROVED)
    counts = store.count_by_status()
    assert counts[store.ST_PENDING] == 1
    assert counts[store.ST_APPROVED] == 1


def test_load_leads_defaults_status_and_verified(tmp_store):
    store.write_rows(store.LEADS_FILE, store.LEAD_FIELDS,
                     [{"name": "Acme", "email": "a@b.com"}])
    lead = store.load_leads()[0]
    assert lead["status"] == store.ST_PENDING
    assert lead["verified"] == "unknown"


# --------------------------------------------------------------------------
# Suppression
# --------------------------------------------------------------------------
def test_suppress_is_permanent_and_idempotent(tmp_store):
    assert store.suppress("a@b.com", "opt out") is True
    assert store.suppress("a@b.com", "opt out") is False
    assert store.is_suppressed("A@B.com")
    assert len(store.load_suppressed()) == 1


def test_suppressed_email_is_never_selected(tmp_store):
    store.upsert_lead({"email": "a@b.com"})
    store.set_status("a@b.com", store.ST_APPROVED)
    store.suppress("a@b.com", "opt out")
    emails = [l["email"] for l in email_agent_zoho.select_recipients()]
    assert "a@b.com" not in emails


# --------------------------------------------------------------------------
# Send log / duplicate protection
# --------------------------------------------------------------------------
def test_record_send_attempt_and_previously_sent(tmp_store):
    store.record_send_attempt("a@b.com", status="sent", subject="Hi")
    assert store.previously_sent("a@b.com") is True
    assert store.previously_sent("c@d.com") is False


def test_failed_attempt_does_not_mark_as_sent(tmp_store):
    store.record_send_attempt("a@b.com", status="failed", error="boom")
    assert store.previously_sent("a@b.com") is False


def test_already_sent_lead_is_not_selected_again(tmp_store):
    store.upsert_lead({"email": "a@b.com"})
    store.set_status("a@b.com", store.ST_APPROVED)
    store.record_send_attempt("a@b.com", status="sent")
    assert email_agent_zoho.select_recipients() == []


def test_sent_today_count(tmp_store):
    store.record_send_attempt("a@b.com", status="sent")
    assert store.sent_today_count() == 1


# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------
def test_render_substitutes_variables():
    lead = {"name": "Priya", "company": "Acme Apparel", "email": "p@acme.com"}
    out = email_agent_zoho.render("New Client", lead)
    assert "Acme Apparel" in out["subject"]
    assert "Priya" in out["text"]
    assert "{" not in out["subject"]


def test_render_escapes_recipient_values_in_html():
    lead = {"name": "<script>alert(1)</script>", "company": "A & B Ltd",
            "email": "x@y.com"}
    out = email_agent_zoho.render("New Client", lead)
    assert "<script>" not in out["html"]
    assert "&lt;script&gt;" in out["html"]
    assert "A &amp; B Ltd" in out["html"]


def test_plain_text_is_not_html_escaped():
    """The plain-text alternative must stay readable (no &quot; / &amp;)."""
    out = email_agent_zoho.render("New Client",
                                   {"email": "x@y.com", "company": "A & B Ltd"})
    assert "&quot;" not in out["text"]
    assert "&amp;" not in out["text"]
    assert "A & B Ltd" in out["text"]


def test_render_handles_missing_personalisation():
    lead = {"email": "unknown@acme.com"}
    out = email_agent_zoho.render("New Client", lead)
    assert out["subject"]
    assert "{" not in out["text"]
    # Falls back to the domain rather than inventing a person.
    assert "acme" in out["text"].lower()


def test_render_unknown_template_raises():
    with pytest.raises(email_agent_zoho.ConfigError):
        email_agent_zoho.render("No Such Template", {"email": "a@b.com"})


def test_templates_contain_unsubscribe_instructions():
    for name in email_agent_zoho.TEMPLATES:
        out = email_agent_zoho.render(name, {"email": "a@b.com", "company": "Acme"})
        assert "unsubscribe" in out["text"].lower()
        assert "unsubscribe" in out["html"].lower()


# --------------------------------------------------------------------------
# Message building
# --------------------------------------------------------------------------
def test_build_message_is_multipart_with_text_and_html():
    out = email_agent_zoho.render("New Client", {"email": "a@b.com", "company": "Acme"})
    msg = email_agent_zoho.build_message("a@b.com", out)
    assert msg.is_multipart()
    subtypes = {part.get_content_type() for part in msg.walk()}
    assert "text/plain" in subtypes
    assert "text/html" in subtypes
    assert msg["To"] == "a@b.com"
    assert "Embroidery Copilot" in msg["From"]


def test_build_message_reports_missing_attachment(tmp_store):
    out = email_agent_zoho.render("New Client", {"email": "a@b.com"})
    with pytest.raises(email_agent_zoho.SendError):
        email_agent_zoho.build_message("a@b.com", out, [tmp_store / "missing.pdf"])


def test_build_message_includes_attachment(tmp_store):
    attachment = tmp_store / "cv.pdf"
    attachment.write_bytes(b"%PDF-1.4 fake")
    out = email_agent_zoho.render("New Client", {"email": "a@b.com"})
    msg = email_agent_zoho.build_message("a@b.com", out, [attachment])
    names = [part.get_filename() for part in msg.walk() if part.get_filename()]
    assert "cv.pdf" in names


# --------------------------------------------------------------------------
# Sending -- mocked SMTP, no network
# --------------------------------------------------------------------------
class FakeSMTP:
    """Stand-in for smtplib.SMTP.  Records what would have been sent."""

    def __init__(self, fail_times=0, error=None):
        self.sent = []
        self.fail_times = fail_times
        self.error = error
        self.quit_called = False

    def send_message(self, message):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise self.error or OSError("connection reset")
        self.sent.append(message)

    def quit(self):
        self.quit_called = True


def test_send_one_with_fake_server(tmp_store):
    out = email_agent_zoho.render("New Client", {"email": "a@b.com", "company": "Acme"})
    server = FakeSMTP()
    message_id = email_agent_zoho.send_one("a@b.com", out, server=server)
    assert len(server.sent) == 1
    assert message_id


def test_send_one_retries_transient_then_succeeds(tmp_store, monkeypatch):
    monkeypatch.setattr(email_agent_zoho, "MAX_RETRIES", 3)
    monkeypatch.setattr(email_agent_zoho, "RETRY_BASE_DELAY", 0)
    out = email_agent_zoho.render("New Client", {"email": "a@b.com"})
    server = FakeSMTP(fail_times=1, error=OSError("connection reset"))
    email_agent_zoho.send_one("a@b.com", out, server=server)
    assert len(server.sent) == 1


def test_send_one_gives_up_after_bounded_retries(tmp_store, monkeypatch):
    monkeypatch.setattr(email_agent_zoho, "MAX_RETRIES", 2)
    monkeypatch.setattr(email_agent_zoho, "RETRY_BASE_DELAY", 0)
    out = email_agent_zoho.render("New Client", {"email": "a@b.com"})
    server = FakeSMTP(fail_times=99, error=OSError("connection reset"))
    with pytest.raises(email_agent_zoho.SendError):
        email_agent_zoho.send_one("a@b.com", out, server=server)


def test_send_one_does_not_retry_permanent_failure(tmp_store, monkeypatch):
    monkeypatch.setattr(email_agent_zoho, "MAX_RETRIES", 3)
    monkeypatch.setattr(email_agent_zoho, "RETRY_BASE_DELAY", 0)
    out = email_agent_zoho.render("New Client", {"email": "a@b.com"})
    import smtplib
    error = smtplib.SMTPResponseException(550, b"mailbox unavailable")
    server = FakeSMTP(fail_times=99, error=error)
    with pytest.raises(email_agent_zoho.SendError):
        email_agent_zoho.send_one("a@b.com", out, server=server)


def test_dry_run_never_opens_smtp(tmp_store, monkeypatch):
    store.upsert_lead({"email": "a@b.com", "company": "Acme"})
    store.set_status("a@b.com", store.ST_APPROVED)

    def explode(*_args, **_kwargs):
        raise AssertionError("dry run must not connect to SMTP")

    monkeypatch.setattr(email_agent_zoho, "_connect", explode)
    summary = email_agent_zoho.run_campaign("New Client", dry_run=True)
    assert summary["dry_run"] is True
    assert summary["sent"] == 0
    assert store.load_sent_log() == []


def test_campaign_records_outcome_and_marks_sent(tmp_store, monkeypatch):
    store.upsert_lead({"email": "a@b.com", "company": "Acme"})
    store.set_status("a@b.com", store.ST_APPROVED)
    server = FakeSMTP()
    monkeypatch.setattr(email_agent_zoho, "_connect", lambda: server)
    monkeypatch.setattr(email_agent_zoho, "MIN_INTERVAL_SECONDS", 0)
    summary = email_agent_zoho.run_campaign("New Client")
    assert summary["sent"] == 1
    assert summary["failed"] == 0
    assert store.previously_sent("a@b.com")
    assert store.find_lead("a@b.com")["status"] == store.ST_SENT
    log = store.load_sent_log()
    assert len(log) == 1
    assert log[0]["status"] == "sent"
    assert log[0]["message_id"]


def test_campaign_records_failure_reason(tmp_store, monkeypatch):
    store.upsert_lead({"email": "a@b.com", "company": "Acme"})
    store.set_status("a@b.com", store.ST_APPROVED)
    server = FakeSMTP(fail_times=99, error=OSError("nope"))
    monkeypatch.setattr(email_agent_zoho, "_connect", lambda: server)
    monkeypatch.setattr(email_agent_zoho, "MAX_RETRIES", 1)
    monkeypatch.setattr(email_agent_zoho, "MIN_INTERVAL_SECONDS", 0)
    summary = email_agent_zoho.run_campaign("New Client")
    assert summary["sent"] == 0
    assert summary["failed"] == 1
    log = store.load_sent_log()
    assert log[0]["status"] == "failed"
    assert "nope" in log[0]["error"]
    assert store.find_lead("a@b.com")["status"] == store.ST_FAILED


def test_daily_limit_stops_the_campaign(tmp_store, monkeypatch):
    for index in range(5):
        email = f"lead{index}@acme.com"
        store.upsert_lead({"email": email, "company": "Acme"})
        store.set_status(email, store.ST_APPROVED)
    server = FakeSMTP()
    monkeypatch.setattr(email_agent_zoho, "_connect", lambda: server)
    monkeypatch.setattr(email_agent_zoho, "MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(email_agent_zoho, "DAILY_LIMIT", 2)
    summary = email_agent_zoho.run_campaign("New Client")
    assert summary["sent"] == 2
    assert len(server.sent) == 2


def test_campaign_requires_approved_status(tmp_store, monkeypatch):
    store.upsert_lead({"email": "pending@acme.com"})
    monkeypatch.setattr(email_agent_zoho, "_connect",
                        lambda: (_ for _ in ()).throw(AssertionError("must not connect")))
    summary = email_agent_zoho.run_campaign("New Client")
    assert summary["sent"] == 0
    assert summary["recipients"] == []


# --------------------------------------------------------------------------
# Campaign lock
# --------------------------------------------------------------------------
def test_campaign_guard_blocks_overlap(tmp_store):
    with store.campaign_guard():
        with pytest.raises(RuntimeError):
            with store.campaign_guard():
                pass
    # Released again afterwards.
    with store.campaign_guard():
        pass


def test_campaign_lock_is_released_on_error(tmp_store):
    try:
        with store.campaign_guard():
            raise ValueError("boom")
    except ValueError:
        pass
    assert not store.campaign_running()


# --------------------------------------------------------------------------
# File lock
# --------------------------------------------------------------------------
def test_file_lock_is_exclusive(tmp_store):
    with store.file_lock("test.lock"):
        with pytest.raises(TimeoutError):
            with store.file_lock("test.lock", timeout=0.2):
                pass


def test_file_lock_released(tmp_store):
    with store.file_lock("test.lock"):
        pass
    with store.file_lock("test.lock", timeout=1):
        pass


# --------------------------------------------------------------------------
# Dashboard widget initialisation
# --------------------------------------------------------------------------
def test_dashboard_constructs_without_crashing(tmp_store, monkeypatch):
    """The v1 dashboard crashed at startup (self.save was never defined)."""
    import app_dashboard
    app = app_dashboard.App()
    try:
        # All widgets the refresh path touches must exist.
        for attribute in ("queue", "approved", "rejects", "log", "progress",
                          "stat_labels", "search_entry", "filter_menu"):
            assert hasattr(app, attribute), f"missing widget: {attribute}"
        # The counters must have been populated by refresh_all().
        assert app.stat_labels["pending"] is not None
        # Selecting a template must not raise.
        app.pick_tmpl("New Client")
    finally:
        app.destroy()


def test_dashboard_load_queue_handles_missing_csv(tmp_store):
    import app_dashboard
    app = app_dashboard.App()
    try:
        assert app._leads == []
        app.refresh_all()
        assert app.stat_labels["pending"].cget("text") == "0"
    finally:
        app.destroy()


def test_dashboard_load_queue_handles_empty_csv(tmp_store):
    import app_dashboard
    store.LEADS_FILE.write_text("", encoding="utf-8")
    app = app_dashboard.App()
    try:
        assert app._leads == []
    finally:
        app.destroy()


def test_dashboard_load_queue_handles_malformed_csv(tmp_store):
    import app_dashboard
    store.LEADS_FILE.write_text("name,email\nAcme,not-an-email\n", encoding="utf-8")
    app = app_dashboard.App()
    try:
        app.refresh_all()
        assert app._leads  # loaded without raising
    finally:
        app.destroy()


def test_dashboard_approve_moves_lead_to_approved(tmp_store):
    import app_dashboard
    store.upsert_lead({"email": "a@b.com", "company": "Acme"})
    app = app_dashboard.App()
    try:
        app._selected.add("a@b.com")
        app.on_approve()
        assert store.find_lead("a@b.com")["status"] == store.ST_APPROVED
        assert app.stat_labels["approved"].cget("text") == "1"
    finally:
        app.destroy()


def test_dashboard_filter_narrows_the_queue(tmp_store):
    import app_dashboard
    store.upsert_lead({"email": "a@b.com"})
    store.upsert_lead({"email": "c@d.com"})
    store.set_status("c@d.com", store.ST_APPROVED)
    app = app_dashboard.App()
    try:
        app.on_filter_change("Approved")
        assert len(app._visible_leads()) == 1
        app.on_filter_change("All")
        assert len(app._visible_leads()) == 2
    finally:
        app.destroy()


def test_dashboard_search_filters_leads(tmp_store):
    import app_dashboard
    store.upsert_lead({"email": "a@acme.com", "company": "Acme Apparel"})
    store.upsert_lead({"email": "b@other.com", "company": "Other Co"})
    app = app_dashboard.App()
    try:
        app.search_entry.insert(0, "acme")
        app.on_search_change()
        assert len(app._visible_leads()) == 1
        assert app._visible_leads()[0]["email"] == "a@acme.com"
    finally:
        app.destroy()


# --------------------------------------------------------------------------
# Telegram authorisation
# --------------------------------------------------------------------------
def test_telegram_denies_unauthorised_user():
    import telegram_bot

    class FakeUser:
        id = 999999

    class FakeMessage:
        from_user = FakeUser()
        chat = type("C", (), {"id": 1})()
        text = "/leads"

    sent = []
    telegram_bot.bot.send_message = lambda chat_id, text, **kw: sent.append(text)
    telegram_bot.ALLOWED = set()
    telegram_bot.cmd_leads(FakeMessage())
    assert sent and "not authorised" in sent[0].lower()
    assert not any("acme" in s.lower() for s in sent)


def test_telegram_allows_configured_user():
    import telegram_bot

    class FakeUser:
        id = 42

    class FakeMessage:
        from_user = FakeUser()
        chat = type("C", (), {"id": 1})()
        text = "/stats"

    sent = []
    telegram_bot.bot.send_message = lambda chat_id, text, **kw: sent.append(text)
    telegram_bot.ALLOWED = {42}
    telegram_bot.cmd_stats(FakeMessage())
    assert sent
    assert "Lead counts" in sent[0]


def test_telegram_allowed_ids_parsing():
    import telegram_bot
    os.environ["TELEGRAM_ALLOWED_IDS"] = "111, 222;333"
    try:
        assert telegram_bot.allowed_ids() == {111, 222, 333}
    finally:
        os.environ.pop("TELEGRAM_ALLOWED_IDS")


def test_telegram_allowed_ids_ignores_garbage():
    import telegram_bot
    os.environ["TELEGRAM_ALLOWED_IDS"] = "111,not-a-number"
    try:
        assert telegram_bot.allowed_ids() == {111}
    finally:
        os.environ.pop("TELEGRAM_ALLOWED_IDS")
