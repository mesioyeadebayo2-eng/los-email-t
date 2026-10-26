import pytest
import tempfile
import os
import gc
import shutil
import db
import config
from scheduler import run_daily_digest, run_hourly_triage

@pytest.fixture
def temp_db(monkeypatch):
    temp_dir = tempfile.mkdtemp()
    temp_db_path = os.path.join(temp_dir, "test_scheduler.db")
    db.init_db(temp_db_path)
    monkeypatch.setattr(config, "DB_PATH", temp_db_path)
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "")
    monkeypatch.setattr("voice_engine.generate_speech", lambda *a, **k: None)
    yield temp_db_path
    gc.collect()
    try:
        shutil.rmtree(temp_dir, ignore_errors=True)
    except Exception:
        pass

def test_daily_scheduler_dry_run_produces_mock_digest_and_archive(temp_db):
    # Daily scheduler in dry-run mode
    result = run_daily_digest(dry_run=True, mark_read=False, send_telegram=False, db_path=temp_db)
    
    emails = result["emails"]
    assert len(emails) == 3
    
    # Verify mock digest content
    subjects = [e["subject"] for e in emails]
    assert "URGENT: Q3 Report missing" in subjects
    assert "Top 10 AI startups this week" in subjects
    assert "Dinner this weekend?" in subjects
    
    # Verify mock auto-archive action on newsletter
    policy_actions = result["policy_actions"]
    archived = policy_actions.get("auto_archived", [])
    assert len(archived) == 1
    assert archived[0]["email"]["id"] == "2"
    assert "newsletter" in archived[0]["email"]["sender"]
    assert archived[0]["folder"] == config.AUTO_ARCHIVE_FOLDER

    # Verify duplicate prevention: running second time processes 0
    second_run = run_daily_digest(dry_run=True, mark_read=False, send_telegram=False, db_path=temp_db)
    assert len(second_run["emails"]) == 0

def test_hourly_triage_dry_run(temp_db):
    result = run_hourly_triage(dry_run=True, mark_read=False, db_path=temp_db)
    emails = result["emails"]
    assert len(emails) == 3
    
    policy_actions = result["policy_actions"]
    assert len(policy_actions.get("auto_archived", [])) == 1
