import imaplib
import smtplib
import time
import os
import email
from email.header import decode_header
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from html.parser import HTMLParser
import html
import logging

import config
import db
import logger_util
from telegram_client import send_telegram_alert

logger = logging.getLogger(__name__)

# Minimal, dependency-free HTML-to-text converter with html2text fallback
try:
    import html2text
    def html_to_text(html_content: str) -> str:
        if not html_content:
            return ""
        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = True
        h.body_width = 0
        return h.handle(html_content).strip()
except ImportError:
    class _HTMLToTextParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.reset()
            self.fed = []
            self._skip = False
            
        def handle_starttag(self, tag, attrs):
            tag_lower = tag.lower()
            if tag_lower in ("script", "style", "head", "title"):
                self._skip = True
            elif tag_lower in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"):
                self.fed.append("\n")
                
        def handle_endtag(self, tag):
            tag_lower = tag.lower()
            if tag_lower in ("script", "style", "head", "title"):
                self._skip = False
            elif tag_lower in ("p", "div", "li", "tr"):
                self.fed.append("\n")
                
        def handle_data(self, d):
            if not self._skip:
                self.fed.append(d)
                
        def get_text(self):
            raw = "".join(self.fed)
            lines = [line.strip() for line in raw.splitlines()]
            return "\n".join(l for l in lines if l)

    def html_to_text(html_content: str) -> str:
        if not html_content:
            return ""
        parser = _HTMLToTextParser()
        parser.feed(html_content)
        return html.unescape(parser.get_text()).strip()

MOCK_EMAILS = [
    {
        "id": "1",
        "sender": "boss@company.com",
        "subject": "URGENT: Q3 Report missing",
        "body": "Where is the Q3 report? I need it by noon.",
        "date": "2026-10-06 09:00:00",
        "attachments": []
    },
    {
        "id": "2",
        "sender": "newsletter@techcrunch.com",
        "subject": "Top 10 AI startups this week",
        "body": "Here are the top AI startups for your reading pleasure...",
        "date": "2026-10-06 10:00:00",
        "attachments": []
    },
    {
        "id": "3",
        "sender": "mom@family.com",
        "subject": "Dinner this weekend?",
        "body": "Are we still on for dinner on Sunday? Let me know!",
        "date": "2026-10-06 11:00:00",
        "attachments": ["recipe.pdf"]
    }
]

def _decode_header_str(header_val) -> str:
    if not header_val:
        return ""
    try:
        decoded_parts = decode_header(header_val)
        out = []
        for text, enc in decoded_parts:
            if isinstance(text, bytes):
                out.append(text.decode(enc if enc else "utf-8", errors="ignore"))
            else:
                out.append(str(text))
        return "".join(out)
    except Exception:
        return str(header_val)

def parse_email_message(msg_bytes: bytes, e_id: str) -> dict:
    """Parse raw RFC822 email bytes into structured email dictionary."""
    msg = email.message_from_bytes(msg_bytes)
    subject = _decode_header_str(msg.get("Subject", ""))
    sender = _decode_header_str(msg.get("From", ""))
    date = msg.get("Date", "")
    
    plain_parts = []
    html_parts = []
    attachments = []
    
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get_content_disposition() or "").lower()
            filename = part.get_filename()
            
            # Check attachments
            if filename or disposition == "attachment":
                if filename:
                    clean_filename = _decode_header_str(filename)
                else:
                    clean_filename = f"attachment_{part.get_content_type().replace('/', '_')}"
                if clean_filename not in attachments:
                    attachments.append(clean_filename)
                
                # Save attachment payload to disk
                payload = part.get_payload(decode=True)
                if payload:
                    try:
                        att_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "email_attachments", str(e_id))
                        os.makedirs(att_dir, exist_ok=True)
                        with open(os.path.join(att_dir, clean_filename), "wb") as f_att:
                            f_att.write(payload)
                    except Exception as e_save:
                        logger.warning(f"Could not save attachment {clean_filename}: {e_save}")
                continue
                
            payload = part.get_payload(decode=True)
            if not payload:
                continue
                
            charset = part.get_content_charset() or "utf-8"
            decoded_text = payload.decode(charset, errors="ignore")
            
            if content_type == "text/plain":
                plain_parts.append(decoded_text)
            elif content_type == "text/html":
                html_parts.append(decoded_text)
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            decoded_text = payload.decode(charset, errors="ignore")
            if msg.get_content_type() == "text/html":
                html_parts.append(decoded_text)
            else:
                plain_parts.append(decoded_text)
                
    if plain_parts:
        body = "\n\n".join(plain_parts).strip()
    elif html_parts:
        body = html_to_text("\n\n".join(html_parts))
    else:
        body = ""
        
    message_id = _decode_header_str(msg.get("Message-ID", ""))
    references = _decode_header_str(msg.get("References", ""))
    in_reply_to = _decode_header_str(msg.get("In-Reply-To", ""))

    return {
        "id": str(e_id),
        "sender": sender,
        "subject": subject,
        "body": body[:3200],  # Truncate body to ~800 tokens roughly
        "date": date,
        "attachments": attachments,
        "message_id": message_id,
        "references": references,
        "in_reply_to": in_reply_to
    }

def get_unread_emails(dry_run: bool = False, mark_read: bool = False, max_fetch: int = None, filter_processed: bool = True) -> list:
    effective_max = max_fetch if max_fetch is not None else config.MAX_FETCH
    account = config.GMAIL_USER or "mock_account"
    
    if dry_run:
        logger.info(f"Dry run enabled. Loading mock emails (max {effective_max}).")
        emails = list(MOCK_EMAILS)
        if filter_processed:
            emails = [e for e in emails if not db.is_email_processed(account, "INBOX", e["id"])]
        if len(emails) > effective_max:
            # Truncate oldest first (keep newest)
            emails = emails[-effective_max:]
        return emails

    if not config.GMAIL_USER or not config.GMAIL_APP_PASSWORD:
        err_msg = "Gmail credentials missing in configuration."
        logger.error(err_msg)
        send_telegram_alert(err_msg)
        return []

    def _fetch_from_imap():
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(config.GMAIL_USER, config.GMAIL_APP_PASSWORD)
        mail.select("inbox")
        
        status, messages = mail.uid("search", None, "(UNSEEN)")
        if status != "OK":
            mail.logout()
            raise RuntimeError(f"IMAP search failed with status: {status}")
            
        uids = messages[0].split()
        if not uids:
            mail.logout()
            return []
            
        # Truncate oldest first if over MAX_FETCH
        if len(uids) > effective_max:
            logger.info(f"Inbox has {len(uids)} unseen messages. Truncating to newest {effective_max} (oldest-first truncation).")
            uids = uids[-effective_max:]
            
        parsed_emails = []
        for uid_bytes in uids:
            uid_str = uid_bytes.decode()
            if filter_processed and db.is_email_processed(account, "INBOX", uid_str):
                logger.info(f"Skipping already processed email UID {uid_str}")
                continue
                
            res, msg_data = mail.uid("fetch", uid_bytes, "(RFC822)")
            if res != "OK":
                continue
                
            for response_part in msg_data:
                if isinstance(response_part, tuple):
                    parsed_email = parse_email_message(response_part[1], uid_str)
                    parsed_emails.append(parsed_email)
                    
            if mark_read:
                mail.uid("store", uid_bytes, "+FLAGS", "(\\Seen)")
            else:
                mail.uid("store", uid_bytes, "-FLAGS", "(\\Seen)")
                
        mail.logout()
        return parsed_emails

    # Retry IMAP calls once with backoff on transient error
    attempts = 2
    for attempt in range(1, attempts + 1):
        try:
            return _fetch_from_imap()
        except Exception as e:
            logger.warning(f"IMAP fetch attempt {attempt} failed: {e}")
            if attempt < attempts:
                time.sleep(2)
            else:
                err_msg = f"IMAP Error after retry: {e}"
                logger.error(err_msg)
                send_telegram_alert(err_msg)
                return []

def get_unread_count(dry_run: bool = False) -> int:
    """Lightweight check to get total count of unseen emails in Inbox."""
    if dry_run:
        return len(MOCK_EMAILS)
    if not config.GMAIL_USER or not config.GMAIL_APP_PASSWORD:
        return 0
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(config.GMAIL_USER, config.GMAIL_APP_PASSWORD)
        mail.select("inbox", readonly=True)
        status, messages = mail.search(None, "(UNSEEN)")
        count = len(messages[0].split()) if status == "OK" and messages and messages[0] else 0
        mail.logout()
        return count
    except Exception as e:
        logger.error(f"Failed to check unread count: {e}")
        return 0

def send_email(to_address: str, subject: str, body: str, attachments: list = None) -> bool:
    if not config.GMAIL_USER or not config.GMAIL_APP_PASSWORD:
        err_msg = "Gmail credentials missing; cannot send email."
        logger.error(err_msg)
        send_telegram_alert(err_msg)
        return False

    def _attempt_smtp_send():
        msg = MIMEMultipart()
        msg["From"] = config.GMAIL_USER
        msg["To"] = to_address
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        # Attach documents & images if specified
        if attachments:
            import mimetypes
            from email.mime.base import MIMEBase
            from email import encoders
            
            for att in attachments:
                file_path = att if isinstance(att, str) else att.get("path")
                file_name = os.path.basename(file_path) if isinstance(att, str) else att.get("filename", os.path.basename(file_path))
                
                if not file_path or not os.path.exists(file_path):
                    logger.warning(f"Attachment file not found: {file_path}")
                    continue
                    
                ctype, encoding = mimetypes.guess_type(file_path)
                if ctype is None or encoding is not None:
                    ctype = "application/octet-stream"
                maintype, subtype = ctype.split("/", 1)
                
                try:
                    with open(file_path, "rb") as fp:
                        part = MIMEBase(maintype, subtype)
                        part.set_payload(fp.read())
                    encoders.encode_base64(part)
                    part.add_header(
                        "Content-Disposition",
                        f"attachment; filename=\"{file_name}\""
                    )
                    msg.attach(part)
                    logger.info(f"Attached file '{file_name}' to email to {to_address}")
                except Exception as ex_att:
                    logger.error(f"Failed to attach file {file_path}: {ex_att}")

        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(config.GMAIL_USER, config.GMAIL_APP_PASSWORD)
        server.send_message(msg)
        server.quit()
        logger.info(f"Email successfully sent to {to_address} with {len(attachments or [])} attachment(s)")
        return True

    # Retry SMTP once with backoff on transient error
    attempts = 2
    for attempt in range(1, attempts + 1):
        try:
            return _attempt_smtp_send()
        except Exception as e:
            logger.warning(f"SMTP send attempt {attempt} failed: {e}")
            if attempt < attempts:
                time.sleep(2)
            else:
                err_msg = f"SMTP Error sending to {to_address}: {e}"
                logger.error(err_msg)
                send_telegram_alert(err_msg)
                return False

def archive_email(uid: str, folder_name: str = None, dry_run: bool = False) -> bool:
    """Move email to archive folder via IMAP or mock action in dry-run."""
    target_folder = folder_name or config.AUTO_ARCHIVE_FOLDER
    
    if dry_run:
        logger.info(f"[DRY RUN] Mock auto-archive: moved email UID {uid} to '{target_folder}'")
        return True

    if not config.GMAIL_USER or not config.GMAIL_APP_PASSWORD:
        err_msg = "Gmail credentials missing; cannot archive email."
        logger.error(err_msg)
        send_telegram_alert(err_msg)
        return False

    def _attempt_archive():
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(config.GMAIL_USER, config.GMAIL_APP_PASSWORD)
        mail.select("inbox")
        
        # Ensure destination folder exists
        try:
            mail.create(f'"{target_folder}"')
        except Exception:
            pass  # Folder might already exist
            
        uid_bytes = str(uid).encode()
        res, _ = mail.uid("copy", uid_bytes, f'"{target_folder}"')
        if res == "OK":
            mail.uid("store", uid_bytes, "+FLAGS", "(\\Deleted)")
            mail.expunge()
            mail.logout()
            logger.info(f"Archived email UID {uid} to '{target_folder}'")
            return True
        else:
            mail.logout()
            raise RuntimeError(f"IMAP copy failed for UID {uid}")

    attempts = 2
    for attempt in range(1, attempts + 1):
        try:
            return _attempt_archive()
        except Exception as e:
            logger.warning(f"IMAP archive attempt {attempt} failed: {e}")
            if attempt < attempts:
                time.sleep(2)
            else:
                err_msg = f"IMAP Archive Error for UID {uid}: {e}"
                logger.error(err_msg)
                send_telegram_alert(err_msg)
                return False
