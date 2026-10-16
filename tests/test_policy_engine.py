import pytest
import tempfile
import os
import gc
import shutil
import db
from policy_engine import evaluate_email_policy, apply_policy_actions, extract_email_address

@pytest.fixture
def temp_db():
    temp_dir = tempfile.mkdtemp()
    temp_db_path = os.path.join(temp_dir, "test_policy.db")
    db.init_db(temp_db_path)
    yield temp_db_path
    gc.collect()
    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass

def test_extract_email_address():
    assert extract_email_address("Alice <alice@work.com>") == "alice@work.com"
    assert extract_email_address("bob@work.com") == "bob@work.com"
    assert extract_email_address("") == ""

def test_policy_auto_archive_category_newsletter(temp_db):
    email_item = {
        "id": "1",
        "sender": "news@medium.com",
        "subject": "Weekly Digest",
        "category": "Newsletter",
        "urgency": "LOW",
        "draft_reply": ""
    }
    decision = evaluate_email_policy(email_item, db_path=temp_db)
    assert decision["action"] == "auto_archive"
    assert decision["reason"] == "category"

def test_policy_auto_archive_category_spam_risk(temp_db):
    email_item = {
        "id": "2",
        "sender": "lottery@phishing.com",
        "subject": "You won 1,000,000!",
        "category": "Spam-risk",
        "urgency": "NORMAL",
        "draft_reply": ""
    }
    decision = evaluate_email_policy(email_item, db_path=temp_db)
    assert decision["action"] == "auto_archive"
    assert decision["reason"] == "category"

def test_policy_auto_send_whitelisted_sender(temp_db):
    db.set_sender_policy("vip@partner.com", "auto_reply_whitelist", db_path=temp_db)
    
    email_item = {
        "id": "3",
        "sender": "VIP Partner <vip@partner.com>",
        "subject": "Urgent update on contract",
        "category": "Work",
        "urgency": "URGENT",
        "draft_reply": "Thank you for reaching out, we are working on this right now."
    }
    decision = evaluate_email_policy(email_item, db_path=temp_db)
    assert decision["action"] == "auto_send"
    assert decision["reason"] == "sender_whitelist"

def test_policy_whitelisted_sender_without_draft_queues_review(temp_db):
    db.set_sender_policy("partner@company.com", "auto_reply_whitelist", db_path=temp_db)
    email_item = {
        "id": "4",
        "sender": "partner@company.com",
        "subject": "FYI: Office closed tomorrow",
        "category": "Work",
        "urgency": "NORMAL",
        "draft_reply": ""
    }
    decision = evaluate_email_policy(email_item, db_path=temp_db)
    assert decision["action"] == "queue_review"
    assert decision["reason"] == "whitelist_no_draft"

def test_policy_sender_blocked_auto_archive(temp_db):
    db.set_sender_policy("annoying@sales.com", "auto_archive", db_path=temp_db)
    email_item = {
        "id": "5",
        "sender": "annoying@sales.com",
        "subject": "Quick 5 min call?",
        "category": "Work",
        "urgency": "NORMAL",
        "draft_reply": "Not interested"
    }
    decision = evaluate_email_policy(email_item, db_path=temp_db)
    assert decision["action"] == "auto_archive"
    assert decision["reason"] == "sender_policy"

def test_policy_default_queue_review(temp_db):
    email_item = {
        "id": "6",
        "sender": "friend@gmail.com",
        "subject": "Movie night?",
        "category": "Personal",
        "urgency": "NORMAL",
        "draft_reply": "Sounds great!"
    }
    decision = evaluate_email_policy(email_item, db_path=temp_db)
    assert decision["action"] == "queue_review"
    assert decision["reason"] == "default"

def test_apply_policy_actions_dry_run(temp_db):
    db.set_sender_policy("whitelisted@client.com", "auto_reply_whitelist", db_path=temp_db)
    emails = [
        {
            "id": "10",
            "sender": "newsletter@domain.com",
            "subject": "Daily digest",
            "category": "Newsletter",
            "draft_reply": ""
        },
        {
            "id": "11",
            "sender": "whitelisted@client.com",
            "subject": "Project sync",
            "category": "Work",
            "draft_reply": "Let us meet tomorrow at 10 AM."
        },
        {
            "id": "12",
            "sender": "client@acme.com",
            "subject": "Inquiry",
            "category": "Work",
            "draft_reply": "We will review your inquiry."
        }
    ]
    results = apply_policy_actions(emails, dry_run=True, db_path=temp_db)
    assert len(results["auto_archived"]) == 1
    assert results["auto_archived"][0]["email"]["id"] == "10"
    
    assert len(results["auto_sent"]) == 1
    assert results["auto_sent"][0]["email"]["id"] == "11"
    
    assert len(results["queued"]) == 1
    assert results["queued"][0]["email"]["id"] == "12"
    
    assert db.is_email_processed("mock_account", "INBOX", "10", db_path=temp_db) or \
           db.is_email_processed("", "INBOX", "10", db_path=temp_db) or \
           len(db.get_recent_processed_emails(limit=10, db_path=temp_db)) == 3
