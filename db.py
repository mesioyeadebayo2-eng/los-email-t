import sqlite3
import os
import uuid
import json
from datetime import datetime
from contextlib import contextmanager
import logging

import config
import logger_util

logger = logging.getLogger(__name__)

def is_postgres(db_path=None) -> bool:
    target = db_path if db_path else config.DATABASE_URL
    return bool(target and ("postgres://" in target or "postgresql://" in target))

def get_db_path(custom_path=None):
    return custom_path or config.DB_PATH

@contextmanager
def get_db_connection(db_path=None):
    if is_postgres(db_path):
        import psycopg2
        from psycopg2.extras import RealDictCursor
        url = db_path if (db_path and ("postgres://" in db_path or "postgresql://" in db_path)) else config.DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        conn = psycopg2.connect(url)
        try:
            yield conn
        finally:
            conn.close()
    else:
        path = get_db_path(db_path)
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        conn = sqlite3.connect(path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

def _get_cursor(conn, is_pg: bool):
    if is_pg:
        import psycopg2.extras
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    return conn.cursor()

def _exec(cursor, query: str, params: tuple = (), is_pg: bool = False):
    if is_pg:
        # Translate SQLite ? placeholders to Postgres %s placeholders
        pg_query = query.replace("?", "%s")
        cursor.execute(pg_query, params)
    else:
        cursor.execute(query, params)

def init_db(db_path=None):
    pg = is_postgres(db_path)
    pk_type = "SERIAL PRIMARY KEY" if pg else "INTEGER PRIMARY KEY AUTOINCREMENT"
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS processed_emails (
                id {pk_type},
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
        
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS drafts (
                id {pk_type},
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
                attachments TEXT,
                status TEXT CHECK(status IN ('pending', 'sent', 'cancelled')),
                created_at TEXT
            )
        """)

        # Migration: add attachments column if it doesn't exist
        try:
            cursor.execute("ALTER TABLE pending_sends ADD COLUMN attachments TEXT")
            conn.commit()
        except Exception:
            pass

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_memory (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT
            )
        """)

        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS personal_notes (
                id {pk_type},
                content TEXT NOT NULL,
                created_at TEXT
            )
        """)
        
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_processed_lookup ON processed_emails(account, folder, message_uid)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_drafts_uid ON drafts(email_uid)")
        conn.commit()
    target_desc = "PostgreSQL (DATABASE_URL)" if pg else get_db_path(db_path)
    logger.info(f"Database initialized on {target_desc}")

def reset_db(db_path=None):
    pg = is_postgres(db_path)
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        cursor.execute("DROP TABLE IF EXISTS processed_emails")
        cursor.execute("DROP TABLE IF EXISTS drafts")
        cursor.execute("DROP TABLE IF EXISTS sender_policy")
        cursor.execute("DROP TABLE IF EXISTS pending_sends")
        conn.commit()
    init_db(db_path)
    logger.info(f"Database reset on {'PostgreSQL' if pg else get_db_path(db_path)}")

def is_email_processed(account: str, folder: str, message_uid: str, db_path=None) -> bool:
    pg = is_postgres(db_path)
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(
            cursor,
            "SELECT 1 FROM processed_emails WHERE account = ? AND folder = ? AND message_uid = ?",
            (account or "", folder or "INBOX", str(message_uid)),
            is_pg=pg
        )
        return cursor.fetchone() is not None

def mark_email_processed(account: str, message_uid: str, folder: str, subject: str, sender: str, digest_date: str, status: str = "processed", db_path=None):
    pg = is_postgres(db_path)
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(
            cursor,
            """
            INSERT INTO processed_emails (account, message_uid, folder, subject, sender, digest_date, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account, folder, message_uid) DO UPDATE SET
                subject = excluded.subject,
                sender = excluded.sender,
                digest_date = excluded.digest_date,
                status = excluded.status
            """,
            (account or "", str(message_uid), folder or "INBOX", subject or "", sender or "", digest_date or datetime.now().strftime("%Y-%m-%d"), status),
            is_pg=pg
        )
        conn.commit()

def save_draft_db(email_uid: str, sender: str, subject: str, draft_body: str, db_path=None):
    clean_uid = str(email_uid).lstrip("#").strip()
    pg = is_postgres(db_path)
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(
            cursor,
            """
            INSERT INTO drafts (email_uid, sender, subject, draft_body, created_at, reviewed, sent)
            VALUES (?, ?, ?, ?, ?, 0, 0)
            """,
            (clean_uid, sender or "", subject or "", draft_body or "", datetime.now().isoformat()),
            is_pg=pg
        )
        conn.commit()

def get_draft_by_uid(email_uid: str, db_path=None) -> dict | None:
    clean_uid = str(email_uid).lstrip("#").strip()
    pg = is_postgres(db_path)
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(
            cursor,
            """
            SELECT id, email_uid, sender, subject, draft_body, created_at, reviewed, sent
            FROM drafts
            WHERE email_uid = ?
            ORDER BY id DESC LIMIT 1
            """,
            (clean_uid,),
            is_pg=pg
        )
        row = cursor.fetchone()
        if row:
            return {
                "id": row["id"],
                "email_uid": row["email_uid"],
                "sender": row["sender"],
                "subject": row["subject"],
                "draft_body": row["draft_body"],
                "created_at": str(row["created_at"]),
                "reviewed": bool(row["reviewed"]),
                "sent": bool(row["sent"])
            }
    return None

def mark_draft_sent(email_uid: str, db_path=None):
    clean_uid = str(email_uid).lstrip("#").strip()
    pg = is_postgres(db_path)
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(
            cursor,
            """
            UPDATE drafts
            SET sent = 1, reviewed = 1
            WHERE email_uid = ?
            """,
            (clean_uid,),
            is_pg=pg
        )
        conn.commit()

def get_recent_processed_emails(limit: int = 5, db_path=None) -> list[dict]:
    pg = is_postgres(db_path)
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(
            cursor,
            """
            SELECT id, account, message_uid, folder, subject, sender, digest_date, status
            FROM processed_emails
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
            is_pg=pg
        )
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
                "digest_date": str(row["digest_date"]),
                "status": row["status"]
            }
            for row in rows
        ]

def get_sender_policy(email: str, db_path=None) -> str:
    if not email:
        return "none"
    clean_email = email.strip().lower()
    pg = is_postgres(db_path)
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(
            cursor,
            "SELECT auto_action FROM sender_policy WHERE email = ?",
            (clean_email,),
            is_pg=pg
        )
        row = cursor.fetchone()
        if row:
            return row["auto_action"]
    return "none"

def set_sender_policy(email: str, auto_action: str, db_path=None):
    if auto_action not in ('none', 'auto_archive', 'auto_reply_whitelist'):
        raise ValueError(f"Invalid auto_action: {auto_action}")
    clean_email = email.strip().lower()
    pg = is_postgres(db_path)
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(
            cursor,
            """
            INSERT INTO sender_policy (email, auto_action)
            VALUES (?, ?)
            ON CONFLICT(email) DO UPDATE SET auto_action = excluded.auto_action
            """,
            (clean_email, auto_action),
            is_pg=pg
        )
        conn.commit()

def list_sender_policies(db_path=None) -> list[dict]:
    pg = is_postgres(db_path)
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(cursor, "SELECT email, auto_action FROM sender_policy ORDER BY email ASC", is_pg=pg)
        rows = cursor.fetchall()
        return [{"email": row["email"], "auto_action": row["auto_action"]} for row in rows]

def create_pending_send(to_address: str, subject: str, body: str, attachments: list = None, db_path=None) -> str:
    send_id = uuid.uuid4().hex[:12]
    pg = is_postgres(db_path)
    att_json = json.dumps(attachments or [])
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(
            cursor,
            """
            INSERT INTO pending_sends (id, to_address, subject, body, attachments, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?)
            """,
            (send_id, to_address, subject, body, att_json, datetime.now().isoformat()),
            is_pg=pg
        )
        conn.commit()
    return send_id

def get_pending_send(send_id: str, db_path=None) -> dict | None:
    pg = is_postgres(db_path)
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(
            cursor,
            "SELECT id, to_address, subject, body, attachments, status, created_at FROM pending_sends WHERE id = ?",
            (send_id,),
            is_pg=pg
        )
        row = cursor.fetchone()
        if row:
            raw_att = row["attachments"] if "attachments" in row.keys() else "[]"
            try:
                parsed_att = json.loads(raw_att) if raw_att else []
            except Exception:
                parsed_att = []
            return {
                "id": row["id"],
                "to_address": row["to_address"],
                "subject": row["subject"],
                "body": row["body"],
                "attachments": parsed_att,
                "status": row["status"],
                "created_at": str(row["created_at"])
            }
    return None

def get_latest_pending_send(db_path=None) -> dict | None:
    pg = is_postgres(db_path)
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(
            cursor,
            "SELECT id, to_address, subject, body, attachments, status, created_at FROM pending_sends WHERE status = 'pending' ORDER BY created_at DESC LIMIT 1",
            is_pg=pg
        )
        row = cursor.fetchone()
        if row:
            raw_att = row["attachments"] if "attachments" in row.keys() else "[]"
            try:
                parsed_att = json.loads(raw_att) if raw_att else []
            except Exception:
                parsed_att = []
            return {
                "id": row["id"],
                "to_address": row["to_address"],
                "subject": row["subject"],
                "body": row["body"],
                "attachments": parsed_att,
                "status": row["status"],
                "created_at": str(row["created_at"])
            }
    return None


def confirm_pending_send(send_id: str, db_path=None) -> tuple[bool, dict | None]:
    """
    Atomic idempotency guard: transitions status from 'pending' to 'sent'.
    Returns (True, send_data) on the first execution.
    Returns (False, None) if already sent, cancelled, or not found.
    """
    pg = is_postgres(db_path)
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(
            cursor,
            "SELECT id, to_address, subject, body, attachments, status, created_at FROM pending_sends WHERE id = ?",
            (send_id,),
            is_pg=pg
        )
        row = cursor.fetchone()
        if not row:
            return False, None
        
        raw_att = row["attachments"] if "attachments" in row.keys() else "[]"
        try:
            parsed_att = json.loads(raw_att) if raw_att else []
        except Exception:
            parsed_att = []

        send_data = {
            "id": row["id"],
            "to_address": row["to_address"],
            "subject": row["subject"],
            "body": row["body"],
            "attachments": parsed_att,
            "status": row["status"],
            "created_at": str(row["created_at"])
        }
        
        _exec(
            cursor,
            "UPDATE pending_sends SET status = 'sent' WHERE id = ? AND status = 'pending'",
            (send_id,),
            is_pg=pg
        )
        if cursor.rowcount == 1:
            conn.commit()
            return True, send_data
        else:
            return False, None

def cancel_pending_send(send_id: str, db_path=None) -> bool:
    pg = is_postgres(db_path)
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(
            cursor,
            "UPDATE pending_sends SET status = 'cancelled' WHERE id = ? AND status = 'pending'",
            (send_id,),
            is_pg=pg
        )
        updated = cursor.rowcount > 0
        conn.commit()
        return updated

def update_pending_send_body(send_id: str, new_body: str, new_subject: str = None, db_path=None) -> bool:
    pg = is_postgres(db_path)
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        if new_subject:
            _exec(
                cursor,
                "UPDATE pending_sends SET body = ?, subject = ? WHERE id = ? AND status = 'pending'",
                (new_body, new_subject, send_id),
                is_pg=pg
            )
        else:
            _exec(
                cursor,
                "UPDATE pending_sends SET body = ? WHERE id = ? AND status = 'pending'",
                (new_body, send_id),
                is_pg=pg
            )
        updated = cursor.rowcount > 0
        conn.commit()
        return updated

def set_user_memory(key: str, value: str, db_path=None):
    pg = is_postgres(db_path)
    now = datetime.now().isoformat()
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        if pg:
            cursor.execute("""
                INSERT INTO user_memory (key, value, updated_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at
            """, (key.lower(), value, now))
        else:
            cursor.execute("""
                INSERT OR REPLACE INTO user_memory (key, value, updated_at)
                VALUES (?, ?, ?)
            """, (key.lower(), value, now))
        conn.commit()

def get_user_memory(key: str, db_path=None):
    pg = is_postgres(db_path)
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(cursor, "SELECT value FROM user_memory WHERE key = ?", (key.lower(),), is_pg=pg)
        row = cursor.fetchone()
        if row:
            return row["value"] if isinstance(row, dict) or pg else row[0]
        return None

def get_all_user_memories(db_path=None) -> dict:
    pg = is_postgres(db_path)
    memories = {}
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(cursor, "SELECT key, value FROM user_memory", (), is_pg=pg)
        for row in cursor.fetchall():
            k = row["key"] if isinstance(row, dict) or pg else row[0]
            v = row["value"] if isinstance(row, dict) or pg else row[1]
            memories[k] = v
    return memories

def add_personal_note(content: str, db_path=None) -> int:
    pg = is_postgres(db_path)
    now = datetime.now().isoformat()
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        if pg:
            cursor.execute("INSERT INTO personal_notes (content, created_at) VALUES (%s, %s) RETURNING id", (content, now))
            row = cursor.fetchone()
            note_id = row["id"] if isinstance(row, dict) else row[0]
        else:
            cursor.execute("INSERT INTO personal_notes (content, created_at) VALUES (?, ?)", (content, now))
            note_id = cursor.lastrowid
        conn.commit()
        return note_id

def get_personal_notes(limit: int = 10, db_path=None) -> list:
    pg = is_postgres(db_path)
    notes = []
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(cursor, "SELECT id, content, created_at FROM personal_notes ORDER BY id DESC LIMIT ?", (limit,), is_pg=pg)
        for row in cursor.fetchall():
            if isinstance(row, dict) or pg:
                notes.append({"id": row["id"], "content": row["content"], "created_at": row["created_at"]})
            else:
                notes.append({"id": row[0], "content": row[1], "created_at": row[2]})
    return notes

def check_db_health(db_path=None) -> bool:
    try:
        pg = is_postgres(db_path)
        with get_db_connection(db_path) as conn:
            cursor = _get_cursor(conn, pg)
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
