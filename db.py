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

        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS watchlist (
                id {pk_type},
                match_type TEXT NOT NULL CHECK(match_type IN ('sender', 'domain', 'keyword', 'regex')),
                match_value TEXT NOT NULL,
                note TEXT
            )
        """)

        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS alerts (
                id {pk_type},
                email_uid TEXT NOT NULL,
                alert_reason TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                acknowledged INTEGER DEFAULT 0
            )
        """)

        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS followups (
                id {pk_type},
                email_uid TEXT NOT NULL,
                sender TEXT,
                subject TEXT,
                direction TEXT NOT NULL CHECK(direction IN ('waiting_on_them', 'waiting_on_me')),
                expected_by TEXT NOT NULL,
                status TEXT DEFAULT 'open' CHECK(status IN ('open', 'closed', 'cancelled')),
                created_at TEXT NOT NULL
            )
        """)

        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS snoozes (
                id {pk_type},
                email_uid TEXT NOT NULL,
                wake_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'fired', 'cancelled'))
            )
        """)

        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS conversations (
                id {pk_type},
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                context_tag TEXT
            )
        """)

        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS memory (
                id {pk_type},
                key TEXT UNIQUE NOT NULL,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS memory_refs (
                id {pk_type},
                memory_id TEXT NOT NULL,
                conversation_id INTEGER
            )
        """)
        
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_processed_lookup ON processed_emails(account, folder, message_uid)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_drafts_uid ON drafts(email_uid)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_email_uid ON alerts(email_uid)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_alerts_sent_at ON alerts(sent_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_followups_status ON followups(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_snoozes_status_wake ON snoozes(status, wake_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_conversations_created ON conversations(created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_memory_key ON memory(key)")
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
        cursor.execute("DROP TABLE IF EXISTS personal_notes")
        cursor.execute("DROP TABLE IF EXISTS user_memory")
        cursor.execute("DROP TABLE IF EXISTS watchlist")
        cursor.execute("DROP TABLE IF EXISTS alerts")
        cursor.execute("DROP TABLE IF EXISTS followups")
        cursor.execute("DROP TABLE IF EXISTS snoozes")
        cursor.execute("DROP TABLE IF EXISTS conversations")
        cursor.execute("DROP TABLE IF EXISTS memory")
        cursor.execute("DROP TABLE IF EXISTS memory_refs")
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
        # Cancel any previous stale pending sends so only the new draft is pending
        _exec(cursor, "UPDATE pending_sends SET status = 'cancelled' WHERE status = 'pending'", is_pg=pg)
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

# ==================== WATCHLIST ====================
def add_watchlist_item(match_type: str, match_value: str, note: str = "", db_path=None) -> int:
    match_type = match_type.strip().lower()
    if match_type not in ('sender', 'domain', 'keyword', 'regex'):
        raise ValueError(f"Invalid match_type: {match_type}")
    match_value = match_value.strip()
    pg = is_postgres(db_path)
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        if pg:
            cursor.execute("INSERT INTO watchlist (match_type, match_value, note) VALUES (%s, %s, %s) RETURNING id", (match_type, match_value, note))
            row = cursor.fetchone()
            item_id = row["id"] if isinstance(row, dict) else row[0]
        else:
            cursor.execute("INSERT INTO watchlist (match_type, match_value, note) VALUES (?, ?, ?)", (match_type, match_value, note))
            item_id = cursor.lastrowid
        conn.commit()
        return item_id

def list_watchlist(db_path=None) -> list:
    pg = is_postgres(db_path)
    items = []
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(cursor, "SELECT id, match_type, match_value, note FROM watchlist ORDER BY id ASC", (), is_pg=pg)
        for row in cursor.fetchall():
            if isinstance(row, dict) or pg:
                items.append({
                    "id": row["id"],
                    "match_type": row["match_type"],
                    "match_value": row["match_value"],
                    "note": row.get("note", "")
                })
            else:
                items.append({
                    "id": row[0],
                    "match_type": row[1],
                    "match_value": row[2],
                    "note": row[3] or ""
                })
    return items

def remove_watchlist_item(item_id: int, db_path=None) -> bool:
    pg = is_postgres(db_path)
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(cursor, "DELETE FROM watchlist WHERE id = ?", (item_id,), is_pg=pg)
        deleted = cursor.rowcount > 0
        conn.commit()
        return deleted

def match_watchlist(email_item: dict, db_path=None) -> list:
    """
    Check if an email matches any watchlist rule.
    Returns a list of matched rule dicts with 'reason'.
    """
    rules = list_watchlist(db_path=db_path)
    if not rules:
        return []

    sender = (email_item.get("sender") or "").strip()
    subject = (email_item.get("subject") or "").strip()
    body = (email_item.get("body") or email_item.get("snippet") or "").strip()

    from policy_engine import extract_email_address
    sender_addr = extract_email_address(sender)

    matches = []
    for r in rules:
        mtype = r["match_type"]
        mval = r["match_value"].strip()
        matched = False
        reason = ""

        if mtype == "sender":
            if mval.lower() == sender_addr or mval.lower() in sender.lower():
                matched = True
                reason = f"matched watchlist sender '{mval}'"
        elif mtype == "domain":
            clean_dom = mval.lower().lstrip("@")
            if f"@{clean_dom}" in sender.lower() or sender_addr.endswith(f"@{clean_dom}") or sender_addr.endswith(f".{clean_dom}"):
                matched = True
                reason = f"matched watchlist domain '{mval}'"
        elif mtype == "keyword":
            mval_lower = mval.lower()
            if mval_lower in subject.lower() or mval_lower in body.lower():
                matched = True
                reason = f"matched watchlist keyword '{mval}'"
        elif mtype == "regex":
            import re
            try:
                pattern = re.compile(mval, re.IGNORECASE)
                if pattern.search(subject) or pattern.search(sender) or pattern.search(body):
                    matched = True
                    reason = f"matched watchlist regex '{mval}'"
            except re.error:
                logger.warning(f"Invalid regex in watchlist rule {r['id']}: {mval}")

        if matched:
            matches.append({
                "rule_id": r["id"],
                "match_type": mtype,
                "match_value": mval,
                "reason": reason
            })

    return matches

# ==================== ALERTS ====================
def record_alert(email_uid: str, alert_reason: str, sent_at: str = None, acknowledged: int = 0, db_path=None) -> int:
    pg = is_postgres(db_path)
    sent_at = sent_at or datetime.now().isoformat()
    clean_uid = str(email_uid).lstrip("#").strip()
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        if pg:
            cursor.execute(
                "INSERT INTO alerts (email_uid, alert_reason, sent_at, acknowledged) VALUES (%s, %s, %s, %s) RETURNING id",
                (clean_uid, alert_reason, sent_at, acknowledged)
            )
            row = cursor.fetchone()
            alert_id = row["id"] if isinstance(row, dict) else row[0]
        else:
            cursor.execute(
                "INSERT INTO alerts (email_uid, alert_reason, sent_at, acknowledged) VALUES (?, ?, ?, ?)",
                (clean_uid, alert_reason, sent_at, acknowledged)
            )
            alert_id = cursor.lastrowid
        conn.commit()
        return alert_id

def has_alerted_for_email(email_uid: str, db_path=None) -> bool:
    pg = is_postgres(db_path)
    clean_uid = str(email_uid).lstrip("#").strip()
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(cursor, "SELECT 1 FROM alerts WHERE email_uid = ? LIMIT 1", (clean_uid,), is_pg=pg)
        return cursor.fetchone() is not None

def count_alerts_in_last_hour(db_path=None) -> int:
    pg = is_postgres(db_path)
    from datetime import timedelta
    one_hour_ago = (datetime.now() - timedelta(hours=1)).isoformat()
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(cursor, "SELECT COUNT(*) FROM alerts WHERE sent_at >= ?", (one_hour_ago,), is_pg=pg)
        row = cursor.fetchone()
        if isinstance(row, dict) or pg:
            return list(row.values())[0] if isinstance(row, dict) else row[0]
        return row[0] if row else 0

def get_latest_alert(db_path=None) -> dict | None:
    pg = is_postgres(db_path)
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(cursor, "SELECT id, email_uid, alert_reason, sent_at, acknowledged FROM alerts ORDER BY id DESC LIMIT 1", (), is_pg=pg)
        row = cursor.fetchone()
        if not row:
            return None
        if isinstance(row, dict) or pg:
            return {"id": row["id"], "email_uid": row["email_uid"], "alert_reason": row["alert_reason"], "sent_at": row["sent_at"], "acknowledged": row["acknowledged"]}
        return {"id": row[0], "email_uid": row[1], "alert_reason": row[2], "sent_at": row[3], "acknowledged": row[4]}

def acknowledge_alert(email_uid: str, db_path=None) -> bool:
    pg = is_postgres(db_path)
    clean_uid = str(email_uid).lstrip("#").strip()
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(cursor, "UPDATE alerts SET acknowledged = 1 WHERE email_uid = ?", (clean_uid,), is_pg=pg)
        updated = cursor.rowcount > 0
        conn.commit()
        return updated

# ==================== FOLLOWUPS ====================
def create_followup(email_uid: str, sender: str, subject: str, direction: str = "waiting_on_them", expected_by: str = None, status: str = "open", db_path=None) -> int:
    pg = is_postgres(db_path)
    from datetime import timedelta
    clean_uid = str(email_uid).lstrip("#").strip()
    created_at = datetime.now().isoformat()
    if not expected_by:
        days = getattr(config, "FOLLOWUP_DAYS", 2)
        expected_by = (datetime.now() + timedelta(days=days)).isoformat()
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        if pg:
            cursor.execute(
                "INSERT INTO followups (email_uid, sender, subject, direction, expected_by, status, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (clean_uid, sender or "", subject or "", direction, expected_by, status, created_at)
            )
            row = cursor.fetchone()
            f_id = row["id"] if isinstance(row, dict) else row[0]
        else:
            cursor.execute(
                "INSERT INTO followups (email_uid, sender, subject, direction, expected_by, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (clean_uid, sender or "", subject or "", direction, expected_by, status, created_at)
            )
            f_id = cursor.lastrowid
        conn.commit()
        return f_id

def list_open_followups(db_path=None) -> list:
    pg = is_postgres(db_path)
    items = []
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(cursor, "SELECT id, email_uid, sender, subject, direction, expected_by, status, created_at FROM followups WHERE status = 'open' ORDER BY expected_by ASC", (), is_pg=pg)
        for row in cursor.fetchall():
            if isinstance(row, dict) or pg:
                items.append({
                    "id": row["id"],
                    "email_uid": row["email_uid"],
                    "sender": row["sender"],
                    "subject": row["subject"],
                    "direction": row["direction"],
                    "expected_by": row["expected_by"],
                    "status": row["status"],
                    "created_at": row["created_at"]
                })
            else:
                items.append({
                    "id": row[0],
                    "email_uid": row[1],
                    "sender": row[2],
                    "subject": row[3],
                    "direction": row[4],
                    "expected_by": row[5],
                    "status": row[6],
                    "created_at": row[7]
                })
    return items

def get_overdue_followups(now_dt: datetime = None, db_path=None) -> list:
    now_str = (now_dt or datetime.now()).isoformat()
    pg = is_postgres(db_path)
    items = []
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(cursor, "SELECT id, email_uid, sender, subject, direction, expected_by, status, created_at FROM followups WHERE status = 'open' AND expected_by <= ? ORDER BY expected_by ASC", (now_str,), is_pg=pg)
        for row in cursor.fetchall():
            if isinstance(row, dict) or pg:
                items.append({
                    "id": row["id"],
                    "email_uid": row["email_uid"],
                    "sender": row["sender"],
                    "subject": row["subject"],
                    "direction": row["direction"],
                    "expected_by": row["expected_by"],
                    "status": row["status"],
                    "created_at": row["created_at"]
                })
            else:
                items.append({
                    "id": row[0],
                    "email_uid": row[1],
                    "sender": row[2],
                    "subject": row[3],
                    "direction": row[4],
                    "expected_by": row[5],
                    "status": row[6],
                    "created_at": row[7]
                })
    return items

def close_followup(followup_id: int, db_path=None) -> bool:
    pg = is_postgres(db_path)
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(cursor, "UPDATE followups SET status = 'closed' WHERE id = ?", (followup_id,), is_pg=pg)
        updated = cursor.rowcount > 0
        conn.commit()
        return updated

def close_matching_followups(email_item: dict, db_path=None) -> list:
    open_items = [f for f in list_open_followups(db_path=db_path) if f["direction"] == "waiting_on_them"]
    if not open_items:
        return []

    import re
    from policy_engine import extract_email_address
    incoming_sender = extract_email_address(email_item.get("sender", ""))
    incoming_subject = (email_item.get("subject") or "").strip().lower()
    norm_in_subj = re.sub(r'^(?:re:\s*|fwd:\s*)+', '', incoming_subject).strip()
    refs = str(email_item.get("references") or "") + " " + str(email_item.get("in_reply_to") or "")

    closed_ids = []
    for f in open_items:
        f_sender = extract_email_address(f["sender"])
        f_subj = (f["subject"] or "").strip().lower()
        norm_f_subj = re.sub(r'^(?:re:\s*|fwd:\s*)+', '', f_subj).strip()
        f_uid = str(f["email_uid"]).strip()

        matched = False
        if f_uid and f_uid in refs:
            matched = True
        elif f_sender and incoming_sender and f_sender == incoming_sender:
            if norm_in_subj and norm_f_subj and (norm_in_subj == norm_f_subj or norm_in_subj in norm_f_subj or norm_f_subj in norm_in_subj):
                matched = True
        elif norm_in_subj and norm_f_subj and norm_in_subj == norm_f_subj:
            matched = True

        if matched:
            close_followup(f["id"], db_path=db_path)
            closed_ids.append(f["id"])

    return closed_ids

# ==================== SNOOZES ====================
def create_snooze(email_uid: str, wake_at: str, db_path=None) -> int:
    pg = is_postgres(db_path)
    clean_uid = str(email_uid).lstrip("#").strip()
    created_at = datetime.now().isoformat()
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        if pg:
            cursor.execute(
                "INSERT INTO snoozes (email_uid, wake_at, created_at, status) VALUES (%s, %s, %s, 'pending') RETURNING id",
                (clean_uid, wake_at, created_at)
            )
            row = cursor.fetchone()
            s_id = row["id"] if isinstance(row, dict) else row[0]
        else:
            cursor.execute(
                "INSERT INTO snoozes (email_uid, wake_at, created_at, status) VALUES (?, ?, ?, 'pending')",
                (clean_uid, wake_at, created_at)
            )
            s_id = cursor.lastrowid
        conn.commit()
        return s_id

def get_due_snoozes(now_dt: datetime = None, db_path=None, base_time=None) -> list:
    target_dt = base_time or now_dt or datetime.now()
    now_str = target_dt.isoformat() if isinstance(target_dt, datetime) else str(target_dt)
    pg = is_postgres(db_path)
    items = []
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(cursor, "SELECT id, email_uid, wake_at, created_at, status FROM snoozes WHERE status = 'pending' AND wake_at <= ? ORDER BY wake_at ASC", (now_str,), is_pg=pg)
        for row in cursor.fetchall():
            if isinstance(row, dict) or pg:
                items.append({
                    "id": row["id"],
                    "email_uid": row["email_uid"],
                    "wake_at": row["wake_at"],
                    "created_at": row["created_at"],
                    "status": row["status"]
                })
            else:
                items.append({
                    "id": row[0],
                    "email_uid": row[1],
                    "wake_at": row[2],
                    "created_at": row[3],
                    "status": row[4]
                })
    return items

def mark_snooze_fired(snooze_id: int, db_path=None) -> bool:
    pg = is_postgres(db_path)
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(cursor, "UPDATE snoozes SET status = 'fired' WHERE id = ?", (snooze_id,), is_pg=pg)
        updated = cursor.rowcount > 0
        conn.commit()
        return updated

# ==================== CONVERSATIONS & MEMORY ====================
def log_conversation(role: str, content: str, context_tag: str = "telegram", created_at: str = None, db_path=None) -> int:
    if not getattr(config, "MEMORY_ENABLED", True):
        return -1
    pg = is_postgres(db_path)
    created_at = created_at or datetime.now().isoformat()
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        if pg:
            cursor.execute(
                "INSERT INTO conversations (role, content, created_at, context_tag) VALUES (%s, %s, %s, %s) RETURNING id",
                (role, content, created_at, context_tag)
            )
            row = cursor.fetchone()
            c_id = row["id"] if isinstance(row, dict) else row[0]
        else:
            cursor.execute(
                "INSERT INTO conversations (role, content, created_at, context_tag) VALUES (?, ?, ?, ?)",
                (role, content, created_at, context_tag)
            )
            c_id = cursor.lastrowid
        conn.commit()
        return c_id

def get_recent_conversations(limit: int = 20, db_path=None) -> list:
    pg = is_postgres(db_path)
    turns = []
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(cursor, "SELECT id, role, content, created_at, context_tag FROM conversations ORDER BY id DESC LIMIT ?", (limit,), is_pg=pg)
        for row in cursor.fetchall():
            if isinstance(row, dict) or pg:
                turns.append({
                    "id": row["id"],
                    "role": row["role"],
                    "content": row["content"],
                    "created_at": row["created_at"],
                    "context_tag": row.get("context_tag")
                })
            else:
                turns.append({
                    "id": row[0],
                    "role": row[1],
                    "content": row[2],
                    "created_at": row[3],
                    "context_tag": row[4]
                })
    turns.reverse()
    return turns

def set_memory(key: str, value: str, conversation_id: int = None, db_path=None) -> int:
    if not getattr(config, "MEMORY_ENABLED", True):
        return -1
    pg = is_postgres(db_path)
    updated_at = datetime.now().isoformat()
    key_clean = key.strip().lower()
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        if pg:
            cursor.execute(
                """
                INSERT INTO memory (key, value, updated_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (key) DO UPDATE SET
                    value = EXCLUDED.value,
                    updated_at = EXCLUDED.updated_at
                RETURNING id
                """,
                (key_clean, value.strip(), updated_at)
            )
            row = cursor.fetchone()
            mem_id = row["id"] if isinstance(row, dict) else row[0]
        else:
            cursor.execute(
                """
                INSERT INTO memory (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key_clean, value.strip(), updated_at)
            )
            cursor.execute("SELECT id FROM memory WHERE key = ?", (key_clean,))
            row = cursor.fetchone()
            mem_id = row[0] if row else cursor.lastrowid

        if conversation_id is not None:
            if pg:
                cursor.execute(
                    "INSERT INTO memory_refs (memory_id, conversation_id) VALUES (%s, %s)",
                    (str(mem_id), conversation_id)
                )
            else:
                cursor.execute(
                    "INSERT INTO memory_refs (memory_id, conversation_id) VALUES (?, ?)",
                    (str(mem_id), conversation_id)
                )

        conn.commit()
        return mem_id

def get_all_memories(db_path=None) -> list:
    pg = is_postgres(db_path)
    memories = []
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        _exec(cursor, "SELECT id, key, value, updated_at FROM memory ORDER BY id ASC", (), is_pg=pg)
        for row in cursor.fetchall():
            if isinstance(row, dict) or pg:
                memories.append({
                    "id": row["id"],
                    "key": row["key"],
                    "value": row["value"],
                    "updated_at": row["updated_at"]
                })
            else:
                memories.append({
                    "id": row[0],
                    "key": row[1],
                    "value": row[2],
                    "updated_at": row[3]
                })
    return memories

def get_memory(key_or_id, db_path=None) -> dict | None:
    pg = is_postgres(db_path)
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        if isinstance(key_or_id, int) or (isinstance(key_or_id, str) and key_or_id.isdigit()):
            _exec(cursor, "SELECT id, key, value, updated_at FROM memory WHERE id = ?", (int(key_or_id),), is_pg=pg)
        else:
            _exec(cursor, "SELECT id, key, value, updated_at FROM memory WHERE key = ?", (str(key_or_id).strip().lower(),), is_pg=pg)
        row = cursor.fetchone()
        if not row:
            return None
        if isinstance(row, dict) or pg:
            return {"id": row["id"], "key": row["key"], "value": row["value"], "updated_at": row["updated_at"]}
        return {"id": row[0], "key": row[1], "value": row[2], "updated_at": row[3]}

def delete_memory(key_or_id, db_path=None) -> bool:
    pg = is_postgres(db_path)
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        if isinstance(key_or_id, int) or (isinstance(key_or_id, str) and key_or_id.isdigit()):
            _exec(cursor, "DELETE FROM memory WHERE id = ?", (int(key_or_id),), is_pg=pg)
        else:
            _exec(cursor, "DELETE FROM memory WHERE key = ?", (str(key_or_id).strip().lower(),), is_pg=pg)
        deleted = cursor.rowcount > 0
        conn.commit()
        return deleted

def wipe_all_memory(db_path=None) -> bool:
    pg = is_postgres(db_path)
    with get_db_connection(db_path) as conn:
        cursor = _get_cursor(conn, pg)
        cursor.execute("DELETE FROM memory_refs")
        cursor.execute("DELETE FROM memory")
        cursor.execute("DELETE FROM conversations")
        conn.commit()
        return True

def get_relevant_memories(query: str = "", limit: int = 8, max_tokens: int = 500, db_path=None) -> list:
    if not getattr(config, "MEMORY_ENABLED", True):
        return []
    all_mems = get_all_memories(db_path=db_path)
    if not all_mems:
        return []

    import re
    if not query:
        sorted_mems = sorted(all_mems, key=lambda m: m["id"], reverse=True)
    else:
        query_words = set(re.findall(r'\w+', query.lower()))
        def score_mem(m):
            text = f"{m['key']} {m['value']}".lower()
            mem_words = set(re.findall(r'\w+', text))
            overlap = len(query_words.intersection(mem_words))
            return (overlap, m["id"])

        sorted_mems = sorted(all_mems, key=score_mem, reverse=True)

    selected = []
    current_tokens = 0
    for m in sorted_mems[:limit]:
        approx_tokens = max(1, len(f"{m['key']}: {m['value']}") // 4)
        if current_tokens + approx_tokens > max_tokens and selected:
            break
        selected.append(m)
        current_tokens += approx_tokens

    return selected

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
