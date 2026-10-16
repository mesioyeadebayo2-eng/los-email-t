import sqlite3
import os
import uuid
from datetime import datetime
from contextlib import contextmanager
import logging

import config
import logger_util

logger = logging.getLogger(__name__)

def get_db_path(custom_path=None):
    return custom_path or config.DB_PATH

@contextmanager
def get_db_connection(db_path=None):
    path = get_db_path(db_path)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db(db_path=None):
    path = get_db_path(db_path)
    with get_db_connection(path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS processed_emails (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account TEXT NOT NULL,
                message_uid TEXT NOT NULL,
                folder TEXT NOT NULL,
                subject TEXT,
                sender TEXT,
                digest_date TEXT,
                status TEXT,
                UNIQUE(account, folder, message_uid)
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS drafts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email_uid TEXT NOT NULL,
                sender TEXT,
                subject TEXT,
                draft_body TEXT,
                created_at TEXT,
                reviewed INTEGER DEFAULT 0,
                sent INTEGER DEFAULT 0
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sender_policy (
                email TEXT PRIMARY KEY,
                auto_action TEXT CHECK(auto_action IN ('none', 'auto_archive', 'auto_reply_whitelist'))
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pending_sends (
                id TEXT PRIMARY KEY,
                to_address TEXT NOT NULL,
                subject TEXT,
                body TEXT,
                status TEXT CHECK(status IN ('pending', 'sent', 'cancelled')),
                created_at TEXT
            )
        """)
        
        # Indexes for fast lookups
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_processed_lookup ON processed_emails(account, folder, message_uid)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_drafts_uid ON drafts(email_uid)")
        conn.commit()
    logger.info(f"Database initialized at {path}")

def reset_db(db_path=None):
    path = get_db_path(db_path)
    with get_db_connection(path) as conn:
        cursor = conn.cursor()
        cursor.execute("DROP TABLE IF EXISTS processed_emails")
        cursor.execute("DROP TABLE IF EXISTS drafts")
        cursor.execute("DROP TABLE IF EXISTS sender_policy")
        cursor.execute("DROP TABLE IF EXISTS pending_sends")
        conn.commit()
    init_db(path)
    logger.info(f"Database reset at {path}")

def is_email_processed(account: str, folder: str, message_uid: str, db_path=None) -> bool:
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM processed_emails WHERE account = ? AND folder = ? AND message_uid = ?",
            (account or "", folder or "INBOX", str(message_uid))
        )
        return cursor.fetchone() is not None

def mark_email_processed(account: str, message_uid: str, folder: str, subject: str, sender: str, digest_date: str, status: str = "processed", db_path=None):
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO processed_emails (account, message_uid, folder, subject, sender, digest_date, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account, folder, message_uid) DO UPDATE SET
                subject = excluded.subject,
                sender = excluded.sender,
                digest_date = excluded.digest_date,
                status = excluded.status
        """, (account or "", str(message_uid), folder or "INBOX", subject or "", sender or "", digest_date or datetime.now().strftime("%Y-%m-%d"), status))
        conn.commit()

def save_draft_db(email_uid: str, sender: str, subject: str, draft_body: str, db_path=None):
    clean_uid = str(email_uid).lstrip("#").strip()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO drafts (email_uid, sender, subject, draft_body, created_at, reviewed, sent)
            VALUES (?, ?, ?, ?, ?, 0, 0)
        """, (clean_uid, sender or "", subject or "", draft_body or "", datetime.now().isoformat()))
        conn.commit()

def get_draft_by_uid(email_uid: str, db_path=None) -> dict | None:
    clean_uid = str(email_uid).lstrip("#").strip()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, email_uid, sender, subject, draft_body, created_at, reviewed, sent
            FROM drafts
            WHERE email_uid = ?
            ORDER BY id DESC LIMIT 1
        """, (clean_uid,))
        row = cursor.fetchone()
        if row:
            return {
                "id": row["id"],
                "email_uid": row["email_uid"],
                "sender": row["sender"],
                "subject": row["subject"],
                "draft_body": row["draft_body"],
                "created_at": row["created_at"],
                "reviewed": bool(row["reviewed"]),
                "sent": bool(row["sent"])
            }
    return None

def mark_draft_sent(email_uid: str, db_path=None):
    clean_uid = str(email_uid).lstrip("#").strip()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE drafts
            SET sent = 1, reviewed = 1
            WHERE email_uid = ?
        """, (clean_uid,))
        conn.commit()

def get_recent_processed_emails(limit: int = 5, db_path=None) -> list[dict]:
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, account, message_uid, folder, subject, sender, digest_date, status
            FROM processed_emails
            ORDER BY id DESC
            LIMIT ?
        """, (limit,))
        rows = cursor.fetchall()
        return [
            {
                "id": row["message_uid"],
                "db_id": row["id"],
                "account": row["account"],
                "message_uid": row["message_uid"],
                "folder": row["folder"],
                "subject": row["subject"],
                "sender": row["sender"],
                "digest_date": row["digest_date"],
                "status": row["status"]
            }
            for row in rows
        ]

def get_sender_policy(email: str, db_path=None) -> str:
    if not email:
        return "none"
    clean_email = email.strip().lower()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT auto_action FROM sender_policy WHERE email = ?", (clean_email,))
        row = cursor.fetchone()
        if row:
            return row["auto_action"]
    return "none"

def set_sender_policy(email: str, auto_action: str, db_path=None):
    if auto_action not in ('none', 'auto_archive', 'auto_reply_whitelist'):
        raise ValueError(f"Invalid auto_action: {auto_action}")
    clean_email = email.strip().lower()
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO sender_policy (email, auto_action)
            VALUES (?, ?)
            ON CONFLICT(email) DO UPDATE SET auto_action = excluded.auto_action
        """, (clean_email, auto_action))
        conn.commit()

def list_sender_policies(db_path=None) -> list[dict]:
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT email, auto_action FROM sender_policy ORDER BY email ASC")
        rows = cursor.fetchall()
        return [{"email": row["email"], "auto_action": row["auto_action"]} for row in rows]

def create_pending_send(to_address: str, subject: str, body: str, db_path=None) -> str:
    send_id = uuid.uuid4().hex[:12]
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO pending_sends (id, to_address, subject, body, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
        """, (send_id, to_address, subject, body, datetime.now().isoformat()))
        conn.commit()
    return send_id

def get_pending_send(send_id: str, db_path=None) -> dict | None:
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, to_address, subject, body, status, created_at FROM pending_sends WHERE id = ?", (send_id,))
        row = cursor.fetchone()
        if row:
            return {
                "id": row["id"],
                "to_address": row["to_address"],
                "subject": row["subject"],
                "body": row["body"],
                "status": row["status"],
                "created_at": row["created_at"]
            }
    return None

def confirm_pending_send(send_id: str, db_path=None) -> tuple[bool, dict | None]:
    """
    Atomic idempotency guard: transitions status from 'pending' to 'sent'.
    Returns (True, send_data) on the first execution.
    Returns (False, None) if already sent, cancelled, or not found.
    """
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, to_address, subject, body, status, created_at FROM pending_sends WHERE id = ?", (send_id,))
        row = cursor.fetchone()
        if not row:
            return False, None
        
        send_data = {
            "id": row["id"],
            "to_address": row["to_address"],
            "subject": row["subject"],
            "body": row["body"],
            "status": row["status"],
            "created_at": row["created_at"]
        }
        
        # Atomically update only if status is pending
        cursor.execute("UPDATE pending_sends SET status = 'sent' WHERE id = ? AND status = 'pending'", (send_id,))
        if cursor.rowcount == 1:
            conn.commit()
            return True, send_data
        else:
            return False, None

def cancel_pending_send(send_id: str, db_path=None) -> bool:
    with get_db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE pending_sends SET status = 'cancelled' WHERE id = ? AND status = 'pending'", (send_id,))
        updated = cursor.rowcount > 0
        conn.commit()
        return updated

def check_db_health(db_path=None) -> bool:
    try:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            return cursor.fetchone() is not None
    except Exception as e:
        logger.error(f"DB health check failed: {e}")
        return False

# Initialize database automatically on module import
init_db()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="MailPilot Database Management")
    parser.add_argument("--init", action="store_true", help="Initialize tables")
    parser.add_argument("--reset", action="store_true", help="Reset all tables")
    args = parser.parse_args()
    
    if args.reset:
        reset_db()
        print("Database reset successfully.")
    else:
        init_db()
        print("Database initialized successfully.")
