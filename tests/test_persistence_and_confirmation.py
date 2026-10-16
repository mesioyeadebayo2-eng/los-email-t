import pytest
import tempfile
import os
import gc
import shutil
import db
from gmail_client import get_unread_emails
from ai_engine import process_emails, save_drafts
from policy_engine import apply_policy_actions

@pytest.fixture
def temp_db():
    temp_dir = tempfile.mkdtemp()
    temp_db_path = os.path.join(temp_dir, "test_persistence.db")
    db.init_db(temp_db_path)
    yield temp_db_path
    gc.collect()
    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass

def test_duplicate_processing_prevention(temp_db, monkeypatch):
    import config
    monkeypatch.setattr(config, "DB_PATH", temp_db)
    
    # Run 1: fetch unread mock emails
    emails_run_1 = get_unread_emails(dry_run=True, filter_processed=True)
    assert len(emails_run_1) == 3
    
    # Process & apply policy (records them as processed in DB)
    processed_1 = process_emails(emails_run_1, dry_run=True)
    save_drafts(processed_1, db_path=temp_db)
    apply_policy_actions(processed_1, dry_run=True, db_path=temp_db)
    
    # Verify emails are now marked processed
    account = config.GMAIL_USER or "mock_account"
    for e in emails_run_1:
        assert db.is_email_processed(account, "INBOX", e["id"], db_path=temp_db)
        
    # Run 2: fetch unread mock emails again
    emails_run_2 = get_unread_emails(dry_run=True, filter_processed=True)
    # Since all 3 are already marked processed, run 2 should return 0 emails!
    assert len(emails_run_2) == 0

def test_draft_retrieval_across_restarts(temp_db):
    # Save drafts directly to database
    db.save_draft_db(
        email_uid="3",
        sender="mom@family.com",
        subject="Dinner this weekend?",
        draft_body="Hi Mom, Sunday at 6 PM works great for me!",
        db_path=temp_db
    )
    
    # Simulate a new server session/restart: no in-memory cache
    draft_direct = db.get_draft_by_uid("3", db_path=temp_db)
    assert draft_direct is not None
    assert draft_direct["email_uid"] == "3"
    assert "Sunday at 6 PM" in draft_direct["draft_body"]
    
    # Check that "#3" prefix also retrieves the draft
    draft_prefixed = db.get_draft_by_uid("#3", db_path=temp_db)
    assert draft_prefixed is not None
    assert draft_prefixed["email_uid"] == "3"
    assert draft_prefixed["draft_body"] == draft_direct["draft_body"]

def test_send_confirmation_idempotency_guard(temp_db):
    # 1. User wants to send email -> pending_send created
    send_id = db.create_pending_send(
        to_address="boss@company.com",
        subject="Q3 Report",
        body="Attached is the requested Q3 report.",
        db_path=temp_db
    )
    assert send_id is not None
    
    # Verify state is pending
    pending = db.get_pending_send(send_id, db_path=temp_db)
    assert pending["status"] == "pending"
    assert pending["to_address"] == "boss@company.com"
    
    # 2. First tap on Confirm
    first_attempt, send_data = db.confirm_pending_send(send_id, db_path=temp_db)
    assert first_attempt is True
    assert send_data is not None
    assert send_data["to_address"] == "boss@company.com"
    
    # 3. Double-tap on Confirm (second tap)
    second_attempt, duplicate_data = db.confirm_pending_send(send_id, db_path=temp_db)
    assert second_attempt is False
    assert duplicate_data is None
    
    # 4. Third tap on Confirm
    third_attempt, _ = db.confirm_pending_send(send_id, db_path=temp_db)
    assert third_attempt is False
