import os
import pytest
from unittest.mock import patch, MagicMock

import db
import config
from webhook import app, handle_telegram_incoming_message

@pytest.fixture(autouse=True)
def test_db(tmp_path):
    temp_db = str(tmp_path / "test_assistant.db")
    db.init_db(temp_db)
    yield temp_db

def test_user_memory_persistence(test_db):
    # Test setting memory
    db.set_user_memory("user_name", "Prince", db_path=test_db)
    assert db.get_user_memory("user_name", db_path=test_db) == "Prince"
    
    # Test updating memory
    db.set_user_memory("user_name", "Prince Adebayo", db_path=test_db)
    assert db.get_user_memory("user_name", db_path=test_db) == "Prince Adebayo"
    
    # Test retrieving all memories
    db.set_user_memory("favorite_coffee", "Latte", db_path=test_db)
    all_mem = db.get_all_user_memories(db_path=test_db)
    assert all_mem.get("user_name") == "Prince Adebayo"
    assert all_mem.get("favorite_coffee") == "Latte"

def test_personal_notes_persistence(test_db):
    # Test adding notes
    n1 = db.add_personal_note("Call accountant about tax deadline", db_path=test_db)
    n2 = db.add_personal_note("Review MailPilot deployment on Render", db_path=test_db)
    
    notes = db.get_personal_notes(5, db_path=test_db)
    assert len(notes) == 2
    assert notes[0]["content"] == "Review MailPilot deployment on Render"
    assert notes[1]["content"] == "Call accountant about tax deadline"

def test_voice_engine_mock_generation():
    import voice_engine
    with patch("voice_engine.get_openai_client") as mock_client:
        mock_instance = MagicMock()
        mock_instance.audio.speech.create.return_value = MagicMock(content=b"dummy_mp3_bytes")
        mock_client.return_value = mock_instance
        
        audio = voice_engine.generate_speech("Hello Prince", voice="cedar")
        assert audio == b"dummy_mp3_bytes"
        mock_instance.audio.speech.create.assert_called_once()

def test_voice_engine_mock_transcription():
    import voice_engine
    with patch("voice_engine.get_openai_client") as mock_client:
        mock_instance = MagicMock()
        mock_instance.audio.transcriptions.create.return_value = MagicMock(text="give me my digest")
        mock_client.return_value = mock_instance
        
        text = voice_engine.transcribe_audio(b"fake_ogg_bytes")
        assert text == "give me my digest"

def test_webhook_receives_voice_note():
    with app.app_context():
        voice_msg = {
            "chat": {"id": config.TELEGRAM_CHAT_ID or "7444753312"},
            "voice": {"file_id": "mock_file_id_12345"}
        }
        with patch("webhook.download_telegram_file", return_value=b"fake_audio_bytes") as mock_dl, \
             patch("voice_engine.transcribe_audio", return_value="What is my name?") as mock_trans, \
             patch("webhook.parse_user_command", return_value={"intent": "conversational", "response": "Your name is Adebayo."}) as mock_parse, \
             patch("webhook.send_telegram_message") as mock_send_msg, \
             patch("webhook.send_reply") as mock_reply:
             
            resp = handle_telegram_incoming_message(voice_msg)
            assert resp[1] == 200
            mock_dl.assert_called_once_with("mock_file_id_12345")
            mock_trans.assert_called_once_with(b"fake_audio_bytes")
