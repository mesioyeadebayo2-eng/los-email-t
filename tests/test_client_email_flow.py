import pytest
from unittest.mock import patch, MagicMock
import db
import config
from webhook import app, is_confirmation_phrase, is_cancellation_phrase

@pytest.fixture
def client_app(tmp_path):
    test_db = str(tmp_path / "test_mailpilot.db")
    db.init_db(test_db)
    app.config["TESTING"] = True
    with patch("config.DB_PATH", test_db), \
         patch("config.WEBHOOK_SECRET_TOKEN", "test_secret"), \
         patch("config.TELEGRAM_CHAT_ID", "123456"), \
         patch("config.GMAIL_USER", "sender_owner@gmail.com"), \
         patch("config.GMAIL_APP_PASSWORD", "test_pass"):
        with app.test_client() as c:
            yield c, test_db

def test_confirmation_phrases():
    for phrase in ["yes", "y", "send", "send it", "send now", "confirm", "send to client", "send to receiver", "do it", "approved", "go ahead", "ok", "deliver"]:
        assert is_confirmation_phrase(phrase), f"Failed for {phrase}"

    for phrase in ["cancel", "stop", "don't send", "dont send", "no", "nevermind", "abort"]:
        assert is_cancellation_phrase(phrase), f"Failed for cancellation {phrase}"

def test_text_confirmation_sends_to_client(client_app):
    client, test_db = client_app
    
    # Create a pending email targeting a client
    client_email = "client_recipient@example.com"
    send_id = db.create_pending_send(client_email, "Invoice #101", "Please find invoice attached.", db_path=test_db)
    
    latest = db.get_latest_pending_send(db_path=test_db)
    assert latest is not None
    assert latest["id"] == send_id
    assert latest["to_address"] == client_email

    # User texts "SEND" in Telegram
    with patch("webhook.send_email", return_value=True) as mock_send, \
         patch("webhook.send_telegram_message") as mock_tg:
        resp = client.post(
            "/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test_secret"},
            json={
                "message": {
                    "chat": {"id": 123456},
                    "text": "send it"
                }
            }
        )
        assert resp.status_code == 200
        # Verify the email was sent to the client, NOT the owner
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        assert args[0] == client_email
        assert args[1] == "Invoice #101"

    # Verify pending send is now marked 'sent'
    updated = db.get_pending_send(send_id, db_path=test_db)
    assert updated["status"] == "sent"

    # Verify get_latest_pending_send returns None now
    assert db.get_latest_pending_send(db_path=test_db) is None

def test_direct_send_to_client(client_app):
    client, test_db = client_app
    client_email = "acme_corp@example.com"

    with patch("webhook.send_email", return_value=True) as mock_send, \
         patch("webhook.send_telegram_message") as mock_tg, \
         patch("webhook.parse_user_command", return_value={"intent": "send_email", "to_email": client_email, "subject": "Contract", "body": "Attached", "direct_send": True}):
        resp = client.post(
            "/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test_secret"},
            json={
                "message": {
                    "chat": {"id": 123456},
                    "text": f"send email directly to {client_email} saying contract ready"
                }
            }
        )
        assert resp.status_code == 200
        mock_send.assert_called_once()
        args, _ = mock_send.call_args
        assert args[0] == client_email

def test_cancellation_text(client_app):
    client, test_db = client_app
    client_email = "cancel_me@example.com"
    send_id = db.create_pending_send(client_email, "Title", "Body", db_path=test_db)

    with patch("webhook.send_telegram_message") as mock_tg:
        resp = client.post(
            "/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test_secret"},
            json={
                "message": {
                    "chat": {"id": 123456},
                    "text": "cancel"
                }
            }
        )
        assert resp.status_code == 200
        
    updated = db.get_pending_send(send_id, db_path=test_db)
    assert updated["status"] == "cancelled"

def test_sanitize_email_body():
    from ai_engine import sanitize_email_body
    raw = "Dear [Recipient's Name],\n\nProposal attached.\n\nBest regards,\n\n[Your Name]"
    sanitized_no_name = sanitize_email_body(raw)
    assert "[Your Name]" not in sanitized_no_name
    assert "[Recipient's Name]" not in sanitized_no_name
    assert sanitized_no_name.endswith("Best regards,")

    sanitized_with_name = sanitize_email_body(raw, sender_name="Adebayo")
    assert "[Your Name]" not in sanitized_with_name
    assert "Adebayo" in sanitized_with_name

def test_view_letter_body(client_app):
    client, test_db = client_app
    client_email = "investor@example.com"
    send_id = db.create_pending_send(client_email, "Q3 Report", "Here is the comprehensive Q3 financial review.", db_path=test_db)

    with patch("webhook.send_telegram_message") as mock_tg:
        resp = client.post(
            "/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test_secret"},
            json={
                "message": {
                    "chat": {"id": 123456},
                    "text": "the body of the letter?"
                }
            }
        )
        assert resp.status_code == 200
        # Check that the body was sent in a telegram message
        assert any("Here is the comprehensive Q3 financial review." in str(call) for call in mock_tg.call_args_list)

def test_edit_letter_body(client_app):
    client, test_db = client_app
    client_email = "investor@example.com"
    send_id = db.create_pending_send(client_email, "Q3 Report", "Old initial draft.", db_path=test_db)

    with patch("webhook.send_telegram_message") as mock_tg:
        resp = client.post(
            "/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "test_secret"},
            json={
                "message": {
                    "chat": {"id": 123456},
                    "text": "Change body to: Updated final review numbers for Q3."
                }
            }
        )
        assert resp.status_code == 200
        
    updated = db.get_pending_send(send_id, db_path=test_db)
    assert updated["body"] == "Updated final review numbers for Q3."

