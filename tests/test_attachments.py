import os
import json
import pytest
from unittest.mock import patch, MagicMock

import db
import config
import attachment_handler
import gmail_client
from webhook import app, handle_telegram_incoming_message

@pytest.fixture(autouse=True)
def test_db(tmp_path):
    temp_db = str(tmp_path / "test_attachments.db")
    db.init_db(temp_db)
    yield temp_db

def test_save_upload_and_collision(tmp_path):
    with patch("attachment_handler.UPLOADS_DIR", str(tmp_path)):
        p1 = attachment_handler.save_upload(b"file content 1", "test report.pdf")
        assert os.path.exists(p1)
        with open(p1, "rb") as f:
            assert f.read() == b"file content 1"
        assert "test_report.pdf" in os.path.basename(p1)

        # Test collision handling
        p2 = attachment_handler.save_upload(b"file content 2", "test report.pdf")
        assert os.path.exists(p2)
        assert p1 != p2
        assert "test_report_1.pdf" in os.path.basename(p2)

def test_extract_document_text():
    # Plain text
    txt_content = b"Line 1: Project Plan\nLine 2: Milestones"
    res_txt = attachment_handler.extract_document_text(txt_content, "notes.txt")
    assert "Project Plan" in res_txt

    # CSV
    csv_content = b"name,amount\nAlice,100\nBob,250"
    res_csv = attachment_handler.extract_document_text(csv_content, "data.csv")
    assert "Alice,100" in res_csv

    # Binary fallback
    bin_content = b"\x00\x01\x02\x03\x04"
    res_bin = attachment_handler.extract_document_text(bin_content, "archive.bin")
    assert "binary file" in res_bin.lower()

def test_extract_pdf_document_text():
    with patch("pypdf.PdfReader") as mock_pdf_reader:
        mock_page_1 = MagicMock()
        mock_page_1.extract_text.return_value = "Quarterly Revenue: $500,000"
        mock_reader_inst = MagicMock()
        mock_reader_inst.pages = [mock_page_1]
        mock_pdf_reader.return_value = mock_reader_inst

        extracted = attachment_handler.extract_document_text(b"%PDF-1.4...", "finance.pdf")
        assert "Quarterly Revenue: $500,000" in extracted
        assert "Page 1" in extracted

def test_analyze_image_vision():
    with patch("attachment_handler.get_openai_client") as mock_client:
        mock_inst = MagicMock()
        mock_resp = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "This image shows a receipt from Apple Store for $1,200."
        mock_resp.choices = [mock_choice]
        mock_inst.chat.completions.create.return_value = mock_resp
        mock_client.return_value = mock_inst

        analysis = attachment_handler.analyze_image(b"fake_jpeg_bytes", "What is the total?")
        assert "$1,200" in analysis
        mock_inst.chat.completions.create.assert_called_once()
        args, kwargs = mock_inst.chat.completions.create.call_args
        assert kwargs["model"] == "gpt-4o-mini"
        messages = kwargs["messages"]
        assert len(messages) == 1
        assert "What is the total?" in messages[0]["content"][0]["text"]

def test_summarize_document():
    with patch("attachment_handler.get_openai_client") as mock_client:
        mock_inst = MagicMock()
        mock_resp = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "Summary: Key milestone achieved in Q3."
        mock_resp.choices = [mock_choice]
        mock_inst.chat.completions.create.return_value = mock_resp
        mock_client.return_value = mock_inst

        summary = attachment_handler.summarize_document("Long text here", "report.txt")
        assert "Key milestone" in summary

def test_send_email_with_attachments(tmp_path):
    test_file = tmp_path / "invoice.pdf"
    test_file.write_bytes(b"%PDF-1.4 dummy pdf bytes")

    with patch("smtplib.SMTP_SSL") as mock_smtp_ssl:
        mock_server = MagicMock()
        mock_smtp_ssl.return_value = mock_server

        orig_user = config.GMAIL_USER
        orig_pwd = config.GMAIL_APP_PASSWORD
        config.GMAIL_USER = "sender@example.com"
        config.GMAIL_APP_PASSWORD = "apppassword"

        try:
            success = gmail_client.send_email(
                to_address="client@example.com",
                subject="Your Invoice",
                body="Please find attached your invoice.",
                attachments=[str(test_file)]
            )
            assert success is True
            mock_server.send_message.assert_called_once()
            sent_msg = mock_server.send_message.call_args[0][0]
            assert sent_msg["Subject"] == "Your Invoice"
            assert sent_msg["To"] == "client@example.com"
            # Verify attachment part exists
            parts = list(sent_msg.walk())
            attachment_parts = [p for p in parts if p.get_filename() == "invoice.pdf"]
            assert len(attachment_parts) == 1
        finally:
            config.GMAIL_USER = orig_user
            config.GMAIL_APP_PASSWORD = orig_pwd

def test_webhook_photo_analysis():
    with app.app_context():
        photo_msg = {
            "chat": {"id": config.TELEGRAM_CHAT_ID or "7444753312"},
            "photo": [{"file_id": "photo_small"}, {"file_id": "photo_large"}],
            "caption": "What does this whiteboard say?"
        }
        with patch("webhook.download_telegram_file", return_value=b"photo_bytes") as mock_dl, \
             patch("attachment_handler.analyze_image", return_value="The whiteboard says Launch Monday.") as mock_vis, \
             patch("webhook.send_chat_action"), \
             patch("webhook.send_telegram_message"), \
             patch("webhook.send_reply") as mock_reply:

            resp = handle_telegram_incoming_message(photo_msg)
            assert resp[1] == 200
            mock_dl.assert_called_once_with("photo_large")
            mock_vis.assert_called_once_with(b"photo_bytes", user_prompt="What does this whiteboard say?")
            mock_reply.assert_called_once()
            assert "The whiteboard says Launch Monday." in mock_reply.call_args[0][1]

def test_webhook_document_email_attachment_queue(test_db, tmp_path):
    with patch("attachment_handler.UPLOADS_DIR", str(tmp_path)):
        with app.app_context():
            doc_msg = {
                "chat": {"id": config.TELEGRAM_CHAT_ID or "7444753312"},
                "document": {"file_id": "doc_file_id", "file_name": "contract.pdf"},
                "caption": "send this to partner@example.com please"
            }
            with patch("webhook.download_telegram_file", return_value=b"doc_pdf_bytes") as mock_dl, \
                 patch("webhook.parse_user_command", return_value={"intent": "send_email", "to_email": "partner@example.com", "subject": "Document: contract.pdf", "body": "Please review."}), \
                 patch("webhook.send_send_confirmation") as mock_confirm:

                resp = handle_telegram_incoming_message(doc_msg)
                assert resp[1] == 200
                mock_dl.assert_called_once_with("doc_file_id")
                mock_confirm.assert_called_once()
                
                # Check confirmation args
                args, kwargs = mock_confirm.call_args
                assert args[2] == "partner@example.com"
                assert "contract.pdf" in args[3]
                
                # Check pending send record in db has attachments
                pending = db.get_pending_send(args[1])
                assert pending is not None
                att_list = pending["attachments"]
                assert len(att_list) == 1
                assert "contract" in att_list[0]
                assert att_list[0].endswith(".pdf")
