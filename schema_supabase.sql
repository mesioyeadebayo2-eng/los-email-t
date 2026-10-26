-- ============================================================
-- Supabase PostgreSQL Schema for Cedar / MailPilot Assistant
-- Life Operating System (AURA Partner)
-- Run this in your Supabase SQL Editor to initialize or verify
-- all tables, constraints, and indexes.
-- ============================================================

-- 1. Processed Emails (Prevents duplicate processing)
CREATE TABLE IF NOT EXISTS processed_emails (
    id SERIAL PRIMARY KEY,
    account TEXT NOT NULL,
    message_uid TEXT NOT NULL,
    folder TEXT NOT NULL,
    subject TEXT,
    sender TEXT,
    digest_date TEXT,
    status TEXT,
    UNIQUE(account, folder, message_uid)
);

-- 2. Draft Replies
CREATE TABLE IF NOT EXISTS drafts (
    id SERIAL PRIMARY KEY,
    email_uid TEXT NOT NULL,
    sender TEXT,
    subject TEXT,
    draft_body TEXT,
    created_at TEXT,
    reviewed INTEGER DEFAULT 0,
    sent INTEGER DEFAULT 0
);

-- 3. Sender Policies (Whitelist / Auto-Archive)
CREATE TABLE IF NOT EXISTS sender_policy (
    email TEXT PRIMARY KEY,
    auto_action TEXT CHECK(auto_action IN ('none', 'auto_archive', 'auto_reply_whitelist'))
);

-- 4. Pending Sends (2-Step Human Confirmation Flow)
CREATE TABLE IF NOT EXISTS pending_sends (
    id TEXT PRIMARY KEY,
    to_address TEXT NOT NULL,
    subject TEXT,
    body TEXT,
    attachments TEXT,
    status TEXT CHECK(status IN ('pending', 'sent', 'cancelled')),
    created_at TEXT
);

-- 5. User Profile Memory
CREATE TABLE IF NOT EXISTS user_memory (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT
);

-- 6. Personal Notes & Reminders
CREATE TABLE IF NOT EXISTS personal_notes (
    id SERIAL PRIMARY KEY,
    content TEXT NOT NULL,
    created_at TEXT
);

-- 7. VIP Watchlist Rules
CREATE TABLE IF NOT EXISTS watchlist (
    id SERIAL PRIMARY KEY,
    match_type TEXT NOT NULL CHECK(match_type IN ('sender', 'domain', 'keyword', 'regex')),
    match_value TEXT NOT NULL,
    note TEXT
);

-- 8. Proactive Alerts & Cooldown History
CREATE TABLE IF NOT EXISTS alerts (
    id SERIAL PRIMARY KEY,
    email_uid TEXT NOT NULL,
    alert_reason TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    acknowledged INTEGER DEFAULT 0
);

-- 9. Proactive Follow-ups Tracking
CREATE TABLE IF NOT EXISTS followups (
    id SERIAL PRIMARY KEY,
    email_uid TEXT NOT NULL,
    sender TEXT,
    subject TEXT,
    direction TEXT NOT NULL CHECK(direction IN ('waiting_on_them', 'waiting_on_me')),
    expected_by TEXT NOT NULL,
    status TEXT DEFAULT 'open' CHECK(status IN ('open', 'closed', 'cancelled')),
    created_at TEXT NOT NULL
);

-- 10. Natural Language Snoozes
CREATE TABLE IF NOT EXISTS snoozes (
    id SERIAL PRIMARY KEY,
    email_uid TEXT NOT NULL,
    wake_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'fired', 'cancelled'))
);

-- 11. Multi-turn Conversation Logs
CREATE TABLE IF NOT EXISTS conversations (
    id SERIAL PRIMARY KEY,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    context_tag TEXT
);

-- 12. Distilled Long-Term Memories
CREATE TABLE IF NOT EXISTS memory (
    id SERIAL PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 13. Memory Provenance References
CREATE TABLE IF NOT EXISTS memory_refs (
    id SERIAL PRIMARY KEY,
    memory_id TEXT NOT NULL,
    conversation_id INTEGER
);

-- Performance Indexes
CREATE INDEX IF NOT EXISTS idx_processed_lookup ON processed_emails(account, folder, message_uid);
CREATE INDEX IF NOT EXISTS idx_drafts_uid ON drafts(email_uid);
CREATE INDEX IF NOT EXISTS idx_alerts_email_uid ON alerts(email_uid);
CREATE INDEX IF NOT EXISTS idx_alerts_sent_at ON alerts(sent_at);
CREATE INDEX IF NOT EXISTS idx_followups_status ON followups(status);
CREATE INDEX IF NOT EXISTS idx_snoozes_status_wake ON snoozes(status, wake_at);
CREATE INDEX IF NOT EXISTS idx_conversations_created ON conversations(created_at);
CREATE INDEX IF NOT EXISTS idx_memory_key ON memory(key);
