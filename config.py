import os
import socket
from dotenv import load_dotenv

load_dotenv()

# Force IPv4 resolution on platforms (particularly Windows) where IPv6 handshakes can stall or timeout
try:
    _orig_getaddrinfo = socket.getaddrinfo
    def _getaddrinfo_ipv4(host, port, family=0, type=0, proto=0, flags=0):
        return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
    socket.getaddrinfo = _getaddrinfo_ipv4
except Exception:
    pass

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

# Proactive Monitoring & Follow-up Settings (48-hour proactive tracking)
FOLLOWUP_DAYS = int(os.getenv("FOLLOWUP_DAYS", "2"))
ALERTS_PER_HOUR_MAX = int(os.getenv("ALERTS_PER_HOUR_MAX", "5"))

# Conversational Memory Settings
MEMORY_ENABLED = os.getenv("MEMORY_ENABLED", "true").lower() in ("true", "1", "yes")
MEMORY_MAX_TOKENS = int(os.getenv("MEMORY_MAX_TOKENS", "500"))
MEMORY_INCLUDES_EMAILS = os.getenv("MEMORY_INCLUDES_EMAILS", "false").lower() in ("true", "1", "yes")

# Deprecated
NGROK_AUTHTOKEN = os.getenv("NGROK_AUTHTOKEN", "")
