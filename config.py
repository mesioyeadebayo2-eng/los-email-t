import os
from dotenv import load_dotenv

load_dotenv()

# Gmail Credentials
GMAIL_USER = os.getenv("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

# OpenAI Credentials
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Telegram Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
WEBHOOK_SECRET_TOKEN = os.getenv("WEBHOOK_SECRET_TOKEN", "mailpilot_secret_token")

# Policy & Autonomy Settings
AUTO_ARCHIVE_FOLDER = os.getenv("AUTO_ARCHIVE_FOLDER", "MailPilot/Archived")

# Scheduler Settings
DIGEST_HOUR = int(os.getenv("DIGEST_HOUR", "8"))
TRIAGE_INTERVAL_MINUTES = int(os.getenv("TRIAGE_INTERVAL_MINUTES", "60"))

# IMAP Fetch Limits
MAX_FETCH = int(os.getenv("MAX_FETCH", "20"))

# Database Configuration (Postgres support via DATABASE_URL or SQLite fallback)
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Paths & Directories
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.getenv("MAILPILOT_DATA_DIR", os.path.join(BASE_DIR, "data"))
DB_PATH = os.getenv("MAILPILOT_DB_PATH", os.path.join(DATA_DIR, "mailpilot.db"))
LOG_PATH = os.getenv("MAILPILOT_LOG_PATH", os.path.join(DATA_DIR, "mailpilot.log"))

# Deprecated
NGROK_AUTHTOKEN = os.getenv("NGROK_AUTHTOKEN", "")
