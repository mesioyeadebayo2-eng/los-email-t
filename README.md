# 📬 MailPilot: AI Personal Email Assistant

MailPilot is a production-grade personal email assistant that connects to your Gmail via IMAP, uses OpenAI's `gpt-4o-mini` to summarize, categorize, and draft replies, and integrates with the Telegram API to deliver scheduled digests, immediate proactive alerts, natural language snoozing, follow-up tracking, and conversational memory.

For a full step-by-step walkthrough, deployment instructions, and complete documentation, please see [WALKTHROUGH.md](file:///c:/Users/DELL/Downloads/email%20assistant/WALKTHROUGH.md).

---

## ⚡ Quick Start

### 1. Installation
```bash
pip install -r requirements.txt
```

### 2. Environment Configuration
Copy `.env.example` to `.env` and fill in your credentials:
```bash
cp .env.example .env
```
Key configuration settings:
* `FOLLOWUP_DAYS=3`: Days before an unanswered email reply is marked overdue.
* `ALERTS_PER_HOUR_MAX=5`: Maximum individual Telegram alerts per hour before queuing into a summary.
* `MEMORY_ENABLED=true`: Enable/disable conversational memory and turn logging.
* `MEMORY_MAX_TOKENS=500`: Maximum tokens for injected memory context in prompts.
* `MEMORY_INCLUDES_EMAILS=false`: Strict isolation of email bodies from long-term memory.

### 3. Running MailPilot
* **Dry Run (Mock Data):** `python main.py --dry-run`
* **Real Run:** `python main.py`
* **Background Scheduler Daemon:** `python main.py --daemon`
* **Webhook Server:** `python webhook.py` (or via Gunicorn: `gunicorn -w 1 -k gthread --threads 8 --timeout 120 webhook:app`)

### 4. Running Tests
```bash
python -m pytest
```

---

## 🚨 Proactive Monitoring & Watchlist Commands
* `/watch add sender <email>` — Monitor VIP sender.
* `/watch add domain <domain>` — Monitor specific domain.
* `/watch add keyword <keyword>` — Monitor key terms (e.g. invoice).
* `/watch add regex <pattern>` — Regular expression matching.
* `/watch list` — View all active watchlist rules.
* `/watch remove <id>` — Delete rule by ID.
* `/followup list` — List open follow-ups and days overdue.
* Snoozing: Reply to any alert with `"remind me about this in 2 hours"`, `"tomorrow 9am"`, or `"next Monday"`.

---

## 🧠 Conversational Memory Commands
* `/memory` — View stored preferences and durable facts.
* `/memory add <text>` — Manually save a personal fact or preference.
* `/memory forget <key-or-index>` — Forget a specific memory (with confirmation).
* `/memory export` — Download memory archive as a `.txt` file via Telegram.
* `/memory wipe` — Delete all memory and conversation history (with button confirmation).
