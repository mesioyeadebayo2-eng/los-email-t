import email
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import pytest
from gmail_client import html_to_text, parse_email_message

def test_html_to_text_basic():
    html_content = "<html><body><h1>Welcome!</h1><p>This is a <b>test</b> email.</p><br><p>Second paragraph &amp; more.</p></body></html>"
    text = html_to_text(html_content)
    assert "Welcome!" in text
    assert "This is a test email." in text
    assert "Second paragraph & more." in text
    assert "<p>" not in text
    assert "<h1>" not in text

def test_html_to_text_ignores_scripts_and_styles():
    html_content = """
    <html>
        <head>
            <style>body { font-size: 14px; }</style>
            <script>alert("malicious");</script>
        </head>
        <body>
            <p>Real content</p>
        </body>
    </html>
    """
    text = html_to_text(html_content)
    assert "Real content" in text
    assert "font-size" not in text
    assert "malicious" not in text

def test_parse_email_html_only():
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "HTML Only Notification"
    msg["From"] = "notifications@service.com"
    msg["Date"] = "Fri, 16 Oct 2026 10:00:00 +0000"
    
    html_part = MIMEText("<html><body><h3>Order Confirmation</h3><p>Your order #1234 has shipped.</p></body></html>", "html")
    msg.attach(html_part)
    
    parsed = parse_email_message(msg.as_bytes(), "101")
    assert parsed["id"] == "101"
    assert parsed["subject"] == "HTML Only Notification"
    assert parsed["sender"] == "notifications@service.com"
    assert "Order Confirmation" in parsed["body"]
    assert "Your order #1234 has shipped." in parsed["body"]
    assert parsed["attachments"] == []

def test_parse_email_multipart_mixed_with_attachments():
    msg = MIMEMultipart("mixed")
    msg["Subject"] = "Monthly Statement"
    msg["From"] = "bank@finance.com"
    
    body_part = MIMEText("Please find your monthly statement attached.", "plain")
    msg.attach(body_part)
    
    # Attachment
    attachment = MIMEBase("application", "pdf")
    attachment.set_payload(b"%PDF-1.4 mock pdf bytes")
    encoders.encode_base64(attachment)
    attachment.add_header("Content-Disposition", "attachment", filename="statement_october.pdf")
    msg.attach(attachment)
    
    parsed = parse_email_message(msg.as_bytes(), "102")
    assert parsed["id"] == "102"
    assert "Please find your monthly statement attached." in parsed["body"]
    assert "statement_october.pdf" in parsed["attachments"]
