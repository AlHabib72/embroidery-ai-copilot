# EmbroideryCopilot Outreach CRM v2 — operating instructions

Windows 11 · Python 3.14 · CustomTkinter desktop GUI · Zoho Mail SMTP
Sender identity: **founder@embroiderycopilot.com**

---

## 1. One-time setup

```powershell
cd C:\Users\sugar\embroidery-ai-copilot

# Create and activate the virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements-outreach.txt
```

Copy the environment template and fill it in:

```powershell
Copy-Item .env.example .env
notepad .env
```

`.env` is git-ignored. Put your **Zoho app password** in `ZOHO_SMTP_PASS`
(Zoho → https://accounts.zoho.com/ → Mail → *Generate password for desktop
client*). Never commit real credentials. If a password was ever pasted into a
chat, a document or a commit, **rotate it**.

---

## 2. Running

```powershell
# GUI dashboard (six tabs)
.\.venv\Scripts\python.exe app_dashboard.py

# Or through the launcher
.\run_outreach.bat gui
```

### Launcher actions

| Command | What it does |
|---|---|
| `run_outreach.bat gui` | Start the desktop dashboard |
| `run_outreach.bat discover` | Run lead discovery (writes to `leads.csv`) |
| `run_outreach.bat preview` | Show every message that *would* be sent — sends nothing |
| `run_outreach.bat dryrun` | Render all messages, transmit nothing |
| `run_outreach.bat send` | Send the approved campaign (requires `ZOHO_SMTP_PASS`) |
| `run_outreach.bat bot` | Start the Telegram bot |
| `run_outreach.bat test` | Run the outreach test suite |

---

## 3. The normal workflow

1. **Dashboard → Find Leads** — discovery runs in a background worker; the
   window stays responsive. New leads land in `leads.csv` with status
   `pending`.
2. **Lead Queue tab** — click a row to select it (or *Select All Visible*).
   Use the status dropdown and the search box to narrow the list.
3. **Approve Selected → Ready** — moves leads to status `approved`. This is
   the required human approval step; nothing is emailed without it.
4. **Dashboard → Preview Campaign** — see the exact recipient, subject and
   rendered body before anything is transmitted.
5. **Dashboard → Send Approved** — a confirmation dialog appears, then the
   campaign runs with the daily cap and the minimum interval between
   messages.
6. **Reject History tab** — rejected and suppressed contacts. Suppressed
   addresses are never contacted again.

---

## 4. Safety model

| Safeguard | Where it lives |
|---|---|
| Human approval before any send | `app_dashboard.py` → `on_send` confirmation dialog |
| Daily cap + minimum interval | `OUTREACH_DAILY_LIMIT`, `OUTREACH_MIN_INTERVAL` |
| Duplicate-send protection | `sent_log.csv` — a `sent` row blocks re-sending |
| Suppression list (opt-out, do-not-contact) | `suppressed.csv` — never re-added automatically |
| Rejected leads excluded | `rejected_leads.csv` + `status=rejected` |
| One campaign at a time | `campaign.lock` — shared by the GUI and the bot |
| No overlapping CSV writes | `outreach_store.file_lock()` (cross-process) |
| Credentials out of source | environment / `.env` only |
| Dry-run and preview modes | `email_agent_zoho.py --preview` / `--dry-run` |

> SMTP acceptance is **not** proof of inbox delivery. `sent_log.csv` records
> what the server accepted, plus the failure reason when it did not.

---

## 5. Data files

All live next to the scripts (paths are anchored to the project root, so the
tools work from any working directory).

| File | Contents |
|---|---|
| `leads.csv` | Lead ledger: `name,email,subject,company,niche,source_url,discovered_at,status,verified,notes` |
| `sent_log.csv` | Every delivery attempt: `timestamp,email,name,company,status,subject,message_id,error` |
| `rejected_leads.csv` | Rejections: `timestamp,email,name,company,reason` |
| `suppressed.csv` | Permanent opt-out list: `email,timestamp,reason` |
| `campaign.lock` | Present only while a campaign is running |

Statuses: `pending` · `approved` · `sent` · `failed` · `rejected` · `suppressed`

The original five `leads.csv` columns keep their names and order, so existing
rows stay compatible.

---

## 6. Telegram bot

```powershell
$env:TELEGRAM_TOKEN = "123456:ABC-your-botfather-token"
$env:TELEGRAM_ALLOWED_IDS = "11111111"     # your numeric Telegram ID
.\.venv\Scripts\python.exe telegram_bot.py
```

Get your numeric ID from [@userinfobot](https://t.me/userinfobot).

**Only IDs in `TELEGRAM_ALLOWED_IDS` can run any command.** Everyone else gets
a rejection and learns nothing about the lead database. If the variable is
empty, every command is denied.

Commands: `/find` `/leads` `/stats` `/approve <email>` `/reject <email>`
`/preview` `/sent` `/campaign`

`/campaign` deliberately does **not** send. It tells you to run the sender
from the desktop app, so a human always sees the preview and confirms.

---

## 7. Testing

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_outreach.py -q
```

The suite never opens a network connection and never sends a real email —
SMTP is exercised through a fake connection object, and every test uses its
own temporary CSV directory.

---

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| `AttributeError: ... 'save'` | Outdated `app_dashboard.py` — copy the new one over. |
| `Refusing to send: ZOHO_SMTP_PASS is not set` | Set the variable, or fill in `.env`. |
| `ModuleNotFoundError: telebot` | `pip install pyTelegramBotAPI` (**not** `telebot` — that is an unrelated placeholder package). |
| `No search package installed` | `pip install ddgs` |
| `A campaign is already running` | Wait, or delete `campaign.lock` if you are sure nothing is running. |
| Bot says "not authorised" | Your ID is not in `TELEGRAM_ALLOWED_IDS`. |
