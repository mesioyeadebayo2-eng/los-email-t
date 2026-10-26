"""
Unit and integration tests for Cedar's Upgraded Intelligence & Conversational Core.
Validates:
1. Dynamic Triage (🔴 Action Required, 🟡 Informational, 🟢 Noise / Newsletters)
2. Contextual Drafting in the Principal's voice without placeholders
3. Multi-turn conversation history (last 6-8 turns) passed to LLM completion service
4. Elimination of robotic boilerplate in greetings, conversational replies, and fallbacks
5. AURA Partnership: structured task correlation (task_title, due_date, priority)
6. Graceful fallback logging with correlation IDs on rate limits, outages, and unsupported attachments
7. Proactive 48-hour follow-up detection window (2 days)
"""

import os
import json
import uuid
import tempfile
import gc
import shutil
from datetime import datetime, timedelta
import pytest
from unittest.mock import patch, MagicMock

import config
import db
import ai_engine
import memory_engine
import attachment_handler
from telegram_client import format_telegram_digest

@pytest.fixture
def test_db():
    temp_dir = tempfile.mkdtemp()
    temp_db_path = os.path.join(temp_dir, "test_cedar_core.db")
    db.init_db(temp_db_path)
    yield temp_db_path
    gc.collect()
    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass

def test_followup_48_hours_default(test_db, monkeypatch):
    """Verify followups default to 48 hours (2 days) proactive tracking."""
    monkeypatch.setattr(config, "FOLLOWUP_DAYS", 2)
    f_id = db.create_followup("uid_48h", "partner@venture.com", "Series A Term Sheet", db_path=test_db)
    followups = db.list_open_followups(db_path=test_db)
    assert len(followups) == 1
    f = followups[0]
    expected_dt = datetime.fromisoformat(f["expected_by"])
    created_dt = datetime.fromisoformat(f["created_at"])
    diff = expected_dt - created_dt
    assert diff.days == 2

def test_dynamic_triage_dry_run(test_db):
    """Verify Dynamic Triage categories in dry_run mode."""
    emails = [
        {"id": "1", "sender": "ceo@holding.com", "subject": "URGENT: Contract Approval Required", "body": "Need your decision today."},
        {"id": "2", "sender": "updates@newsletter.tech", "subject": "Tech Weekly Digest #42", "body": "Here are 10 articles."},
        {"id": "3", "sender": "friend@gmail.com", "subject": "Coffee on Tuesday?", "body": "Let me know if you are free."}
    ]
    processed = ai_engine.process_emails(emails, dry_run=True, db_path=test_db)
    assert len(processed) == 3

    # Email 1: 🔴 Action Required
    e1 = processed[0]
    assert e1["triage_bucket"] == "🔴 Action Required"
    assert e1["urgency"] == "URGENT"
    assert e1["aura_task"] is not None
    assert "Review" in e1["aura_task"]["task_title"]
    assert e1["aura_task"]["priority"] == "HIGH"

    # Email 2: 🟢 Noise / Newsletters
    e2 = processed[1]
    assert e2["triage_bucket"] == "🟢 Noise / Newsletters"
    assert e2["category"] == "Newsletter"
    assert e2["draft_reply"] == ""

    # Email 3: 🟡 Informational
    e3 = processed[2]
    assert e3["triage_bucket"] == "🟡 Informational"
    assert e3["urgency"] == "NORMAL"

def test_dynamic_triage_and_aura_sync_live(test_db):
    """Verify LLM prompt includes Cedar persona, Dynamic Triage, Contextual Drafting, and AURA correlation."""
    emails = [
        {
            "id": "101",
            "sender": "sarah@acme.com",
            "subject": "Acquisition Term Sheet - Deadline Friday",
            "body": "Principal, please sign the indemnity terms by Friday 5pm.",
            "attachments": ["term_sheet.pdf"]
        }
    ]

    mock_llm_response = {
        "results": [
            {
                "id": "101",
                "summary": "Sarah requested signed indemnity terms for the acquisition term sheet by Friday 5pm.",
                "urgency": "URGENT",
                "category": "Work",
                "triage_bucket": "🔴 Action Required",
                "draft_reply": "Sarah, we have reviewed the terms and agree with Section 4. Best regards.",
                "aura_task": {
                    "task_title": "Sign indemnity terms for acquisition",
                    "due_date": "2026-10-30",
                    "priority": "HIGH"
                }
            }
        ]
    }

    captured_prompt = []
    def fake_create(**kwargs):
        for m in kwargs.get("messages", []):
            if m.get("role") == "system":
                captured_prompt.append(m.get("content", ""))
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps(mock_llm_response)
        return MagicMock(choices=[mock_choice])

    with patch.object(ai_engine, "client") as mock_client:
        mock_client.chat.completions.create = fake_create
        processed = ai_engine.process_emails(emails, dry_run=False, db_path=test_db)

    assert len(processed) == 1
    res = processed[0]
    assert res["triage_bucket"] == "🔴 Action Required"
    assert res["aura_task"]["task_title"] == "Sign indemnity terms for acquisition"
    assert res["aura_task"]["due_date"] == "2026-10-30"
    assert res["aura_task"]["priority"] == "HIGH"
    assert "Sarah, we have reviewed" in res["draft_reply"]

    # Verify system prompt directives
    sys_text = captured_prompt[0]
    assert "DYNAMIC TRIAGE" in sys_text
    assert "🔴 Action Required" in sys_text
    assert "🟡 Informational" in sys_text
    assert "🟢 Noise / Newsletters" in sys_text
    assert "AURA PARTNERSHIP" in sys_text
    assert "Cedar" in sys_text

def test_multi_turn_history_passed_to_parse_user_command(test_db):
    """Verify last 6-8 conversation turns are passed to LLM completion in parse_user_command."""
    # Seed past conversation turns
    turns_data = [
        ("user", "Sarah sent over the acquisition term sheet."),
        ("assistant", "Noted. Are we moving forward with Section 4 indemnity?"),
        ("user", "Yes, we agree with her indemnification clauses."),
        ("assistant", "Understood. Would you like me to prepare a draft to Sarah?"),
        ("user", "Yes, email Sarah at sarah@acme.com saying we accept the indemnity terms."),
    ]
    for role, content in turns_data:
        db.log_conversation(role=role, content=content, db_path=test_db)

    captured_messages = []
    def fake_create(**kwargs):
        captured_messages.extend(kwargs.get("messages", []))
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps({
            "intent": "send_email",
            "to_email": "sarah@acme.com",
            "subject": "Acquisition Term Sheet - Indemnity Acceptance",
            "body": "Sarah,\n\nWe accept the indemnity terms.\n\nBest regards,\nPrincipal"
        })
        return MagicMock(choices=[mock_choice])

    with patch.object(ai_engine, "client") as mock_client:
        mock_client.chat.completions.create = fake_create
        res = ai_engine.parse_user_command("Tell her we accept the terms", db_path=test_db)

    assert res.get("intent") == "send_email"
    assert len(captured_messages) > 3

    # Verify past conversation turns were injected into messages
    roles = [m.get("role") for m in captured_messages]
    assert "system" in roles
    assert "user" in roles
    assert "assistant" in roles
    contents = [m.get("content", "") for m in captured_messages]
    all_text = " ".join(contents)
    assert "Sarah sent over the acquisition term sheet." in all_text
    assert "Tell her we accept the terms" in all_text

    # Verify persona instructions eliminate robotic boilerplate
    system_prompt = captured_messages[0]["content"]
    assert "Eliminate Robotic Boilerplate" in system_prompt
    assert "Never say \"Hello, I am Cedar, an AI email assistant" in system_prompt
    assert "Multi-Turn Memory" in system_prompt

def test_elimination_of_robotic_boilerplate_in_fallbacks():
    """Verify fallback responses never use robotic boilerplate."""
    # 1. memory_engine fallback when client is None
    with patch.object(memory_engine, "client", None):
        reply = memory_engine.answer_conversational_turn("Good morning")
        assert "AI email assistant" not in reply
        assert "How may I help you today" not in reply
        assert "Principal" in reply

    # 2. ai_engine fallback when client is None
    with patch.object(ai_engine, "client", None):
        reply2 = ai_engine.generate_memory_aware_response("Hello")
        assert "AI email assistant" not in reply2
        assert "Principal" in reply2

def test_graceful_fallback_logging_on_outage(test_db):
    """Verify graceful fallback logging with correlation ID on rate limit or outage."""
    with patch.object(ai_engine, "client") as mock_client, \
         patch("ai_engine.send_telegram_alert") as mock_tg:
        mock_client.chat.completions.create.side_effect = Exception("Rate limit exceeded (429)")

        emails = [{"id": "999", "sender": "vip@investor.com", "subject": "Urgent Bridge Round", "body": "Need terms"}]
        results = ai_engine.process_emails(emails, dry_run=False, db_path=test_db)

        # Verified fallback occurred without crashing
        assert len(results) == 1
        assert "AI processing failed" in results[0]["summary"] or "failed" in results[0]["summary"].lower()

    # Unsupported attachment format logs fallback state
    unsupported_bytes = b"\x00\xff\xee\xdd\xcc"
    ret = attachment_handler.extract_document_text(unsupported_bytes, "unknown_payload.bin")
    assert "UNSUPPORTED_ATTACHMENT" in ret
    assert "Correlation ID:" in ret

def test_telegram_digest_dynamic_triage_and_aura_format():
    """Verify format_telegram_digest displays Dynamic Triage badges and AURA action items."""
    emails = [
        {
            "id": "1",
            "sender": "alice@partner.com",
            "subject": "Contract Due Today",
            "summary": "Alice sent contract for signature.",
            "urgency": "URGENT",
            "triage_bucket": "🔴 Action Required",
            "category": "Work",
            "draft_reply": "On it.",
            "aura_task": {
                "task_title": "Sign partner contract",
                "due_date": "2026-10-26",
                "priority": "HIGH"
            }
        },
        {
            "id": "2",
            "sender": "marketing@news.com",
            "subject": "Weekly Tech Pulse",
            "summary": "Overview of AI news.",
            "urgency": "LOW",
            "triage_bucket": "🟢 Noise / Newsletters",
            "category": "Newsletter",
            "draft_reply": ""
        }
    ]
    digest = format_telegram_digest(emails)
    assert "🔴 Action Required" in digest
    assert "🟢 Noise / Newsletters" in digest
    assert "📌 <b>AURA Task:</b> Sign partner contract (Due: 2026-10-26 | Priority: HIGH)" in digest
