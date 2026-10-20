#!/usr/bin/env python3
"""EmbroideryCopilot Outreach CRM — six-tab desktop dashboard.

Structure (unchanged from v1 so muscle memory still works):
    Dashboard · Lead Queue · Approved/Ready · Reject History · Templates · Settings

Rules this file follows:
  * UI construction, data loading and refresh are separate steps.
  * Every widget exists before any method touches it.
  * Network / SMTP work never runs on the Tk main thread.  Workers put
    progress on a queue which the main thread drains with ``after()``.
  * CSV state is shared with the Telegram bot through ``outreach_store``.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import customtkinter as ctk

import outreach_store as store

BASE_DIR = store.BASE_DIR
PYTHON_EXE = sys.executable

TEMPLATE_NAMES = ["New Client", "Sales Pitch", "Follow-up"]

STATUS_FILTERS = [
    ("All", None),
    ("Pending", store.ST_PENDING),
    ("Approved", store.ST_APPROVED),
    ("Sent", store.ST_SENT),
    ("Failed", store.ST_FAILED),
    ("Rejected", store.ST_REJECTED),
    ("Suppressed", store.ST_SUPPRESSED),
]

FILTER_ALL = "All"


class App(ctk.CTk):
    """Main window."""

    def __init__(self):
        super().__init__()
        self.title("Embroidery Copilot — Outreach CRM v2")
        self.geometry("1120x720")
        self.minsize(900, 600)

        # Appearance mode is applied BEFORE any widget is created.
        self._appearance = "dark"
        ctk.set_appearance_mode(self._appearance)

        # --- state that must exist before any refresh runs ---------------
        self._leads: list[dict] = []
        self._selected: set[str] = set()
        self._filter_status = None  # None means "All"
        self._search = ""
        self._events: queue.Queue = queue.Queue()
        self._busy = False
        self._cancel = threading.Event()
        self._template_choice = TEMPLATE_NAMES[0]
        self._templates = {}

        self._build_ui()
        self._load_templates()
        self.refresh_all()
        try:
            self.after(120, self._drain_events)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        """Create every widget.  Nothing here reads data or refreshes views."""
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.tabs = ctk.CTkTabview(self)
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)

        self._build_dashboard_tab(self.tabs.add("Dashboard"))
        self._build_queue_tab(self.tabs.add("Lead Queue"))
        self._build_approved_tab(self.tabs.add("Approved / Ready"))
        self._build_reject_tab(self.tabs.add("Reject History"))
        self._build_templates_tab(self.tabs.add("Templates"))
        self._build_settings_tab(self.tabs.add("Settings"))

    # -- Tab 1: Dashboard ------------------------------------------------
    def _build_dashboard_tab(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(tab, text="Queue / Ready / Rejected", font=ctk.CTkFont(size=18, weight="bold")
                     ).grid(row=0, column=0, pady=(6, 10), sticky="w")

        self.stat_labels = {}
        stat_names = [("pending", "Pending"), ("approved", "Approved / Ready"),
                      ("sent", "Sent"), ("failed", "Failed"),
                      ("rejected", "Rejected"), ("suppressed", "Suppressed")]
        for index, (key, label) in enumerate(stat_names):
            card = ctk.CTkFrame(tab)
            card.grid(row=1, column=0, pady=4, sticky="ew")
            card.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(card, text=label, width=140, anchor="w"
                         ).grid(row=0, column=0, padx=(12, 6), pady=6)
            value_label = ctk.CTkLabel(card, text="0", font=ctk.CTkFont(size=18, weight="bold"))
            value_label.grid(row=0, column=1, padx=6, pady=6, sticky="w")
            self.stat_labels[key] = value_label

        actions = ctk.CTkFrame(tab, fg_color="transparent")
        actions.grid(row=2, column=0, pady=10, sticky="ew")
        self.btn_find = ctk.CTkButton(actions, text="Find Leads", command=self.on_find_leads, width=150)
        self.btn_find.pack(side="left", padx=4)
        self.btn_preview = ctk.CTkButton(actions, text="Preview Campaign", command=self.on_preview,
                                         width=150)
        self.btn_preview.pack(side="left", padx=4)
        self.btn_send = ctk.CTkButton(actions, text="Send Approved", command=self.on_send,
                                      width=150)
        self.btn_send.pack(side="left", padx=4)
        self.btn_cancel = ctk.CTkButton(actions, text="Cancel", command=self.on_cancel,
                                        width=110, state="disabled")
        self.btn_cancel.pack(side="left", padx=4)

        self.progress = ctk.CTkProgressBar(tab)
        self.progress.set(0)
        self.progress.grid(row=3, column=0, pady=(4, 8), sticky="ew")

        self.log = ctk.CTkTextbox(tab, height=200)
        self.log.grid(row=4, column=0, pady=6, sticky="nsew")
        tab.grid_rowconfigure(4, weight=1)

    # -- Tab 2: Lead Queue ----------------------------------------------
    def _build_queue_tab(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        toolbar = ctk.CTkFrame(tab, fg_color="transparent")
        toolbar.grid(row=0, column=0, pady=(6, 4), sticky="ew")
        toolbar.grid_columnconfigure(2, weight=1)

        self.filter_menu = ctk.CTkOptionMenu(
            toolbar, values=[name for name, _ in STATUS_FILTERS], command=self.on_filter_change)
        self.filter_menu.grid(row=0, column=0, padx=4)
        self.filter_menu.set(FILTER_ALL)

        self.search_entry = ctk.CTkEntry(toolbar, placeholder_text="Search name, email, company…")
        self.search_entry.grid(row=0, column=1, padx=8, sticky="ew")
        self.search_entry.bind("<KeyRelease>", self.on_search_change)

        self.btn_refresh = ctk.CTkButton(toolbar, text="Refresh", command=self.refresh_all, width=100)
        self.btn_refresh.grid(row=0, column=2, padx=4, sticky="e")

        self.queue = ctk.CTkTextbox(tab, height=320, wrap="word")
        self.queue.grid(row=1, column=0, padx=6, pady=6, sticky="nsew")
        self.queue.bind("<Button-1>", self._on_queue_click)

        buttons = ctk.CTkFrame(tab, fg_color="transparent")
        buttons.grid(row=2, column=0, pady=(0, 8), sticky="ew")
        self.btn_approve = ctk.CTkButton(buttons, text="Approve Selected → Ready",
                                         command=self.on_approve, width=200)
        self.btn_approve.pack(side="left", padx=4)
        self.btn_reject = ctk.CTkButton(buttons, text="Reject Selected → Log",
                                        command=self.on_reject, width=200)
        self.btn_reject.pack(side="left", padx=4)
        self.btn_suppress = ctk.CTkButton(buttons, text="Suppress Selected",
                                           command=self.on_suppress, width=170)
        self.btn_suppress.pack(side="left", padx=4)
        self.btn_select_all = ctk.CTkButton(buttons, text="Select All Visible",
                                            command=self.on_select_all, width=160)
        self.btn_select_all.pack(side="left", padx=4)

        self.queue_status = ctk.CTkLabel(tab, text="", anchor="w")
        self.queue_status.grid(row=3, column=0, padx=8, pady=(0, 6), sticky="ew")

    # -- Tab 3: Approved / Ready ----------------------------------------
    def _build_approved_tab(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(tab, text="Approved leads, ready for outreach. "
                     "Only these are contacted by a campaign.",
                     anchor="w").grid(row=0, column=0, padx=8, pady=(6, 2), sticky="ew")
        self.approved = ctk.CTkTextbox(tab, height=340, wrap="word")
        self.approved.grid(row=1, column=0, padx=6, pady=6, sticky="nsew")
        self.approved_count = ctk.CTkLabel(tab, text="", anchor="w")
        self.approved_count.grid(row=2, column=0, padx=8, pady=(0, 6), sticky="ew")

    # -- Tab 4: Reject History ------------------------------------------
    def _build_reject_tab(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(tab, text="Rejected and suppressed contacts. "
                     "These are never contacted again.",
                     anchor="w").grid(row=0, column=0, padx=8, pady=(6, 2), sticky="ew")
        self.rejects = ctk.CTkTextbox(tab, height=340, wrap="word")
        self.rejects.grid(row=1, column=0, padx=6, pady=6, sticky="nsew")
        self.rejects_count = ctk.CTkLabel(tab, text="", anchor="w")
        self.rejects_count.grid(row=2, column=0, padx=8, pady=(0, 6), sticky="ew")

    # -- Tab 5: Templates ------------------------------------------------
    def _build_templates_tab(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)
        ctk.CTkLabel(tab, text="Outreach templates. Placeholders: "
                     "{company_name}, {contact_person}, {service_name}.",
                     anchor="w").grid(row=0, column=0, padx=8, pady=(6, 2), sticky="ew")
        self.sel = ctk.CTkComboBox(tab, values=TEMPLATE_NAMES, command=self.pick_tmpl)
        self.sel.grid(row=1, column=0, padx=8, pady=6, sticky="ew")
        self.tmpl_text = ctk.CTkTextbox(tab, height=240, wrap="word")
        self.tmpl_text.grid(row=2, column=0, padx=6, pady=6, sticky="nsew")
        self.tmpl_html = ctk.CTkTextbox(tab, height=140, wrap="word")
        self.tmpl_html.grid(row=3, column=0, padx=6, pady=6, sticky="nsew")

    # -- Tab 6: Settings -------------------------------------------------
    def _build_settings_tab(self, tab) -> None:
        tab.grid_columnconfigure(1, weight=1)
        rows = [
            ("SMTP host", "ZOHO_SMTP_HOST", os.environ.get("ZOHO_SMTP_HOST", "smtppro.zoho.in")),
            ("SMTP port", "ZOHO_SMTP_PORT", os.environ.get("ZOHO_SMTP_PORT", "587")),
            ("SMTP user", "ZOHO_SMTP_USER",
             os.environ.get("ZOHO_SMTP_USER", "founder@embroiderycopilot.com")),
            ("Daily limit", "OUTREACH_DAILY_LIMIT",
             os.environ.get("OUTREACH_DAILY_LIMIT", "50")),
            ("Min interval (s)", "OUTREACH_MIN_INTERVAL",
             os.environ.get("OUTREACH_MIN_INTERVAL", "45")),
        ]
        for index, (label, key, value) in enumerate(rows):
            ctk.CTkLabel(tab, text=label, width=150, anchor="w").grid(
                row=index, column=0, padx=(10, 6), pady=5, sticky="w")
            entry = ctk.CTkEntry(tab)
            entry.insert(0, f"{value}   [{key}]")
            entry.configure(state="readonly")
            entry.grid(row=index, column=1, padx=6, pady=5, sticky="ew")

        password_row = len(rows)
        ctk.CTkLabel(tab, text="SMTP password", width=150, anchor="w").grid(
            row=password_row, column=0, padx=(10, 6), pady=5, sticky="w")
        self.pw = ctk.CTkEntry(tab, show="*", placeholder_text="(read from ZOHO_SMTP_PASS)")
        self.pw.grid(row=password_row, column=1, padx=6, pady=5, sticky="ew")
        ctk.CTkLabel(tab, text="", text_color="gray").grid(
            row=password_row + 1, column=0, columnspan=2, padx=10, pady=2, sticky="w")
        self.pw_status = ctk.CTkLabel(tab, text="", anchor="w")
        self.pw_status.grid(row=password_row + 1, column=0, columnspan=2,
                            padx=10, pady=(2, 6), sticky="ew")

        ctk.CTkButton(tab, text="Check configuration", command=self.on_check_config
                      ).grid(row=password_row + 2, column=0, columnspan=2, padx=10, pady=6)

        theme_row = password_row + 3
        ctk.CTkLabel(tab, text="Theme", width=150, anchor="w").grid(
            row=theme_row, column=0, padx=(10, 6), pady=5, sticky="w")
        self.theme_menu = ctk.CTkOptionMenu(tab, values=["dark", "light"], command=self.on_theme_change)
        self.theme_menu.grid(row=theme_row, column=1, padx=6, pady=5, sticky="ew")
        self.theme_menu.set(self._appearance)

        ctk.CTkLabel(tab, text="Data files live next to this script:\n"
                     + "\n".join([
                         f"• {store.LEADS_FILE.name} — lead ledger",
                         f"• {store.SENT_LOG_FILE.name} — delivery attempts",
                         f"• {store.REJECTED_FILE.name} — rejections",
                         f"• {store.SUPPRESSED_FILE.name} — opt-out list",
                     ]),
                     justify="left", anchor="w").grid(
            row=theme_row + 1, column=0, columnspan=2, padx=10, pady=10, sticky="w")

    # ------------------------------------------------------------------
    # Templates
    # ------------------------------------------------------------------
    def _load_templates(self) -> None:
        """Load the email templates from the sender module (one source of truth)."""
        try:
            import email_agent_zoho
            self._templates = {
                name: {"subject": tpl["subject"], "html": tpl["html"], "text": tpl["text"]}
                for name, tpl in email_agent_zoho.TEMPLATES.items()
            }
        except Exception as exc:  # noqa: BLE001 - a template problem must not kill the GUI
            self._templates = {}
            self.log_line(f"Could not load templates: {exc}")

    def pick_tmpl(self, choice) -> None:
        """Show the chosen template's text and HTML."""
        self._template_choice = choice or TEMPLATE_NAMES[0]
        template = self._templates.get(self._template_choice)
        self.tmpl_text.delete("1.0", "end")
        self.tmpl_html.delete("1.0", "end")
        if not template:
            self.tmpl_text.insert("1.0", f"Template {self._template_choice!r} is not available.")
            return
        self.tmpl_text.insert("1.0", template["text"])
        self.tmpl_html.insert("1.0", template["html"])

    # ------------------------------------------------------------------
    # Data loading + refresh (never called during UI construction)
    # ------------------------------------------------------------------
    def refresh_all(self) -> None:
        """Reload every view from disk.  Safe to call at any time."""
        try:
            self._leads = store.load_leads()
        except Exception as exc:  # noqa: BLE001
            self._leads = []
            self.log_line(f"Could not read leads: {exc}")

        self._refresh_stats()
        self._refresh_queue()
        self._refresh_approved()
        self._refresh_rejects()

    def _refresh_stats(self) -> None:
        counts = store.count_by_status()
        for key, label in self.stat_labels.items():
            label.configure(text=str(counts.get(key, 0)))

    def _visible_leads(self) -> list[dict]:
        """Leads matching the current status filter and search text."""
        rows = self._leads
        # ``_filter_status`` is None for "All" and a status string otherwise.
        if self._filter_status is not None:
            rows = [r for r in rows if r.get("status") == self._filter_status]
        needle = self._search.strip().lower()
        if needle:
            rows = [r for r in rows if needle in " ".join(
                str(r.get(f, "")) for f in ("name", "email", "company", "niche")
            ).lower()]
        return rows

    def _refresh_queue(self) -> None:
        rows = self._visible_leads()
        self.queue.delete("1.0", "end")
        if not rows:
            self.queue.insert("end", "No leads to show.\n\n"
                                     "Use “Find Leads” on the Dashboard tab, or clear the filter.\n")
            self.queue_status.configure(text="0 lead(s)")
            return
        for index, row in enumerate(rows):
            email = store.normalize_email(row.get("email"))
            marker = "[x]" if email in self._selected else "[ ]"
            self.queue.insert("end", f"{marker} {index + 1}. {row.get('name', '')} | "
                                      f"{email} | {row.get('company', '')} | "
                                      f"{row.get('status', '')}\n")
        self.queue_status.configure(
            text=f"{len(rows)} lead(s) shown · {len(self._selected)} selected")

    def _refresh_approved(self) -> None:
        rows = [r for r in self._leads if r.get("status") == store.ST_APPROVED]
        self.approved.delete("1.0", "end")
        if not rows:
            self.approved.insert("end", "No approved leads yet.\n\n"
                                       "Approve leads in the Lead Queue tab, then send from the Dashboard.\n")
        else:
            for row in rows:
                self.approved.insert("end", f"{row.get('name', '')} | {row.get('email', '')} | "
                                            f"{row.get('company', '')}\n")
        self.approved_count.configure(text=f"{len(rows)} approved lead(s)")

    def _refresh_rejects(self) -> None:
        rejected = [r for r in self._leads if r.get("status") == store.ST_REJECTED]
        suppressed = store.load_suppressed()
        self.rejects.delete("1.0", "end")
        if not rejected and not suppressed:
            self.rejects.insert("end", "No rejected or suppressed contacts.\n")
        else:
            for row in rejected:
                self.rejects.insert("end", f"[rejected] {row.get('email', '')} | "
                                            f"{row.get('name', '')} | "
                                            f"{row.get('notes', '')}\n")
            for email, reason in sorted(suppressed.items()):
                self.rejects.insert("end", f"[suppressed] {email} | {reason}\n")
        self.rejects_count.configure(
            text=f"{len(rejected)} rejected · {len(suppressed)} suppressed")

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------
    def _on_queue_click(self, event) -> None:
        """Toggle the checkbox of the clicked row."""
        index = self.queue.index(f"@{event.x},{event.y}")
        if index is None:
            return
        line = int(index.split(".")[0])
        rows = self._visible_leads()
        if not 1 <= line <= len(rows):
            return
        email = store.normalize_email(rows[line - 1].get("email"))
        if not email:
            return
        if email in self._selected:
            self._selected.discard(email)
        else:
            self._selected.add(email)
        self._refresh_queue()

    def on_select_all(self) -> None:
        rows = self._visible_leads()
        emails = {store.normalize_email(r.get("email")) for r in rows}
        emails.discard("")
        if emails and emails <= self._selected:
            self._selected -= emails
        else:
            self._selected |= emails
        self._refresh_queue()

    def on_filter_change(self, choice) -> None:
        """Filter by status.  The menu shows labels; the store uses values."""
        label = choice or FILTER_ALL
        for name, value in STATUS_FILTERS:
            if name == label:
                # "All" maps to None, every other entry to its status string.
                self._filter_status = value
                break
        else:
            self._filter_status = None
        self._refresh_queue()

    def on_search_change(self, _event=None) -> None:
        self._search = self.search_entry.get() if hasattr(self, "search_entry") else ""
        self._refresh_queue()

    # ------------------------------------------------------------------
    # Background work
    # ------------------------------------------------------------------
    def log_line(self, message: str) -> None:
        """Append a line to the dashboard log (main thread only)."""
        if not hasattr(self, "log"):
            return
        self.log.insert("end", message.rstrip() + "\n")
        self.log.see("end")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for button in (self.btn_find, self.btn_preview, self.btn_send):
            button.configure(state="disabled" if busy else "normal")
        self.btn_cancel.configure(state="normal" if busy else "disabled")
        self.progress.set(0.0 if not busy else 0.5)

    def _drain_events(self) -> None:
        """Move worker messages onto the UI.  Always on the main thread."""
        try:
            while True:
                kind, message, extra = self._events.get_nowait()
                if kind == "log":
                    self.log_line(message)
                elif kind == "progress":
                    self.progress.set(extra.get("value", 0.0))
                elif kind == "done":
                    self._set_busy(False)
                    self.progress.set(1.0)
                    self.log_line(message)
                    self.refresh_all()
                elif kind == "error":
                    self._set_busy(False)
                    self.progress.set(0.0)
                    self.log_line(f"ERROR: {message}")
        except queue.Empty:
            pass
        # The window may have been destroyed while a worker was still running.
        try:
            self.after(120, self._drain_events)
        except Exception:  # noqa: BLE001 - interpreter is shutting down
            pass

    def _run_worker(self, target, name: str) -> None:
        """Start a background thread; the worker never touches Tk widgets."""
        if self._busy:
            self.log_line(f"{name} is already running. Wait for it to finish.")
            return
        self._set_busy(True)
        self.log_line(f"--- {name} started ---")

        def runner():
            try:
                target()
            except Exception as exc:  # noqa: BLE001 - report, never crash the GUI
                self._events.put(("error", f"{name} failed: {type(exc).__name__}: {exc}", {}))
            else:
                self._events.put(("done", f"--- {name} finished ---", {}))

        threading.Thread(target=runner, name=name, daemon=True).start()

    def on_cancel(self) -> None:
        self._cancel.set()
        self.log_line("Cancellation requested. The current step will finish first.")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def on_find_leads(self) -> None:
        """Run lead discovery in a worker process (network work, off the UI thread)."""
        def work():
            script = BASE_DIR / "lead_finder.py"
            if not script.is_file():
                self._events.put(("error", f"Missing {script.name}", {}))
                return
            result = subprocess.run([PYTHON_EXE, str(script)], cwd=str(BASE_DIR),
                                    capture_output=True, text=True, timeout=600)
            output = (result.stdout or "").strip()
            if result.returncode != 0:
                self._events.put(("error", f"lead_finder failed: "
                                           f"{(result.stderr or '').strip()[:500]}", {}))
                return
            for line in output.splitlines():
                self._events.put(("log", line, {}))
            self._events.put(("log", f"Discovery finished (exit {result.returncode}).", {}))

        self._run_worker(work, "Lead discovery")

    def on_preview(self) -> None:
        """Preview the campaign without sending anything."""
        def work():
            import email_agent_zoho
            recipients = email_agent_zoho.select_recipients()
            if not recipients:
                self._events.put(("log", "No approved leads to preview. "
                                        "Approve some leads first.", {}))
                return
            self._events.put(("log", f"--- Preview: {len(recipients)} recipient(s) ---", {}))
            for lead in recipients:
                try:
                    rendered = email_agent_zoho.render(self._template_choice, lead)
                    self._events.put(("log", f"To: {lead.get('email')} | "
                                            f"Subject: {rendered['subject']}", {}))
                except Exception as exc:  # noqa: BLE001
                    self._events.put(("log", f"render failed for "
                                            f"{lead.get('email')}: {exc}", {}))

        self._run_worker(work, "Preview")

    def on_send(self) -> None:
        """Send the campaign.  Requires an explicit confirmation."""
        approved = store.leads_by_status(store.ST_APPROVED)
        if not approved:
            self.log_line("Nothing to send: no approved leads.")
            return
        if store.campaign_running():
            self.log_line("A campaign is already running. Refusing to start a second one.")
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title("Confirm campaign")
        dialog.geometry("520x260")
        dialog.transient(self)
        ctk.CTkLabel(dialog, text=f"Send to {len(approved)} approved lead(s)?",
                     font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(18, 6))
        ctk.CTkLabel(dialog, text=f"Template: {self._template_choice}\n"
                                  f"Daily limit: {os.environ.get('OUTREACH_DAILY_LIMIT', '50')}\n"
                                  f"Interval: {os.environ.get('OUTREACH_MIN_INTERVAL', '45')}s\n\n"
                                  f"This transmits real emails.", justify="left"
                     ).pack(pady=4)

        def confirm():
            dialog.destroy()
            self._do_send()

        def cancel():
            dialog.destroy()
            self.log_line("Campaign cancelled by user.")

        ctk.CTkButton(dialog, text="Cancel", command=cancel, width=140).pack(side="left", padx=16, pady=16)
        ctk.CTkButton(dialog, text="Send now", command=confirm, width=140).pack(side="right", padx=16, pady=16)
        dialog.grab_set()

    def _do_send(self) -> None:
        def work():
            import email_agent_zoho
            email_agent_zoho._load_dotenv()
            # Refresh module-level config from the environment.
            email_agent_zoho.SMTP_USER = os.environ.get("ZOHO_SMTP_USER", email_agent_zoho.SMTP_USER)
            email_agent_zoho.SMTP_PASS = (os.environ.get("ZOHO_SMTP_PASS")
                                          or os.environ.get("SMTP_PASS", ""))

            def on_event(kind, message, **_extra):
                self._events.put(("log", message, {}))

            with store.campaign_guard():
                summary = email_agent_zoho.run_campaign(
                    self._template_choice,
                    dry_run=False,
                    attachments=email_agent_zoho.DEFAULT_ATTACHMENTS,
                    on_event=on_event,
                )
            self._events.put(("log", f"Campaign finished: {summary['sent']} sent, "
                                     f"{summary['failed']} failed.", {}))

        self._run_worker(work, "Campaign")

    def on_approve(self) -> None:
        emails = sorted(self._selected)
        if not emails:
            self.log_line("Nothing selected. Click a row in the Lead Queue to select it.")
            return
        changed = store.set_status_bulk(emails, store.ST_APPROVED)
        self.log_line(f"Approved {changed} lead(s).")
        self._selected.clear()
        self.refresh_all()

    def on_reject(self) -> None:
        emails = sorted(self._selected)
        if not emails:
            self.log_line("Nothing selected.")
            return
        dialog = ctk.CTkToplevel(self)
        dialog.title("Reject leads")
        dialog.geometry("440x200")
        dialog.transient(self)
        ctk.CTkLabel(dialog, text=f"Reject {len(emails)} lead(s)?",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(pady=16)
        ctk.CTkLabel(dialog, text="Rejected leads are logged and excluded from campaigns.",
                     justify="left").pack(pady=4)

        def confirm():
            dialog.destroy()
            for email in emails:
                lead = store.find_lead(email) or {}
                store.record_rejection(email, lead.get("name", ""), lead.get("company", ""),
                                       reason="rejected by user")
            changed = store.set_status_bulk(emails, store.ST_REJECTED, reason="rejected by user")
            self.log_line(f"Rejected {changed} lead(s); logged in rejected_leads.csv.")
            self._selected.clear()
            self.refresh_all()

        def cancel():
            dialog.destroy()

        ctk.CTkButton(dialog, text="Cancel", command=cancel, width=120).pack(side="left", padx=16, pady=14)
        ctk.CTkButton(dialog, text="Reject", command=confirm, width=120).pack(side="right", padx=16, pady=14)
        dialog.grab_set()

    def on_suppress(self) -> None:
        """Permanently suppress the selected contacts (opt-out / do-not-contact)."""
        emails = sorted(self._selected)
        if not emails:
            self.log_line("Nothing selected.")
            return
        dialog = ctk.CTkToplevel(self)
        dialog.title("Suppress contacts")
        dialog.geometry("480x220")
        dialog.transient(self)
        ctk.CTkLabel(dialog, text=f"Suppress {len(emails)} contact(s) permanently?",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(pady=14)
        ctk.CTkLabel(dialog, text="Suppressed addresses are never contacted again and are\n"
                                  "never re-added to a campaign automatically.",
                     justify="left").pack(pady=4)

        def confirm():
            dialog.destroy()
            count = 0
            for email in emails:
                if store.suppress(email, reason="suppressed by user"):
                    count += 1
                store.set_status(email, store.ST_SUPPRESSED, reason="suppressed by user")
            self.log_line(f"Suppressed {count} contact(s).")
            self._selected.clear()
            self.refresh_all()

        def cancel():
            dialog.destroy()

        ctk.CTkButton(dialog, text="Cancel", command=cancel, width=120).pack(side="left", padx=16, pady=14)
        ctk.CTkButton(dialog, text="Suppress", command=confirm, width=120).pack(side="right", padx=16, pady=14)
        dialog.grab_set()

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    def on_check_config(self) -> None:
        """Report whether the sender is ready, without revealing the password."""
        try:
            import email_agent_zoho
            email_agent_zoho._load_dotenv()
            password = (os.environ.get("ZOHO_SMTP_PASS") or os.environ.get("SMTP_PASS", ""))
            user = os.environ.get("ZOHO_SMTP_USER", "founder@embroiderycopilot.com")
            host = os.environ.get("ZOHO_SMTP_HOST", "smtppro.zoho.in")
            port = os.environ.get("ZOHO_SMTP_PORT", "587")
            if password:
                self.pw_status.configure(
                    text=f"✓ Ready. User {user} @ {host}:{port}. "
                         f"Password present in environment (not shown).", text_color="green")
            else:
                self.pw_status.configure(
                    text="✗ ZOHO_SMTP_PASS is not set. Set it in the environment or in a "
                         "local .env file (see .env.example).", text_color="red")
        except Exception as exc:  # noqa: BLE001
            self.pw_status.configure(text=f"Configuration check failed: {exc}", text_color="red")

    def on_theme_change(self, mode: str) -> None:
        self._appearance = mode
        ctk.set_appearance_mode(mode)
        self.log_line(f"Theme set to {mode}.")


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
