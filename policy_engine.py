import email.utils
from datetime import datetime
import logging
import config
import db
import logger_util

logger = logging.getLogger(__name__)

def extract_email_address(sender_str: str) -> str:
    """Extract and normalize clean email address from sender string."""
    if not sender_str:
        return ""
    name, addr = email.utils.parseaddr(sender_str)
    if addr:
        return addr.strip().lower()
    return sender_str.strip().lower()

def evaluate_email_policy(email_item: dict, db_path=None) -> dict:
    """
    Tiered Autonomy Engine (deterministic):
    1. Sender in sender_policy whitelist -> auto-send draft reply
    2. Sender in sender_policy auto_archive -> auto-archive
    3. Category in {Newsletter, Spam-risk} -> auto-archive
    4. Everything else -> draft + queue for review
    """
    sender = email_item.get("sender", "")
    sender_email = extract_email_address(sender)
    
    # 1 & 2: Check explicit sender policy
    policy = db.get_sender_policy(sender_email, db_path=db_path)
    if policy == "auto_reply_whitelist":
        if email_item.get("draft_reply"):
            return {
                "action": "auto_send",
                "reason": "sender_whitelist",
                "sender_email": sender_email,
                "folder": None
            }
        else:
            return {
                "action": "queue_review",
                "reason": "whitelist_no_draft",
                "sender_email": sender_email,
                "folder": None
            }
    elif policy == "auto_archive":
        return {
            "action": "auto_archive",
            "reason": "sender_policy",
            "sender_email": sender_email,
            "folder": config.AUTO_ARCHIVE_FOLDER
        }
    
    # 3: Check category rules
    category = email_item.get("category", "")
    if category in ("Newsletter", "Spam-risk"):
        return {
            "action": "auto_archive",
            "reason": "category",
            "sender_email": sender_email,
            "folder": config.AUTO_ARCHIVE_FOLDER
        }
        
    # 4: Default review queue
    return {
        "action": "queue_review",
        "reason": "default",
        "sender_email": sender_email,
        "folder": None
    }

def apply_policy_actions(processed_emails: list, dry_run: bool = False, db_path=None) -> dict:
    """
    Apply policy decisions to a list of AI-processed emails.
    """
    from gmail_client import archive_email, send_email
    from telegram_client import send_telegram_message
    
    results = {
        "auto_archived": [],
        "auto_sent": [],
        "queued": []
    }
    
    today_str = datetime.now().strftime("%Y-%m-%d")
    account = config.GMAIL_USER or "user@gmail.com"
    
    for email_item in processed_emails:
        decision = evaluate_email_policy(email_item, db_path=db_path)
        action = decision["action"]
        email_uid = str(email_item.get("id", ""))
        subject = email_item.get("subject", "")
        sender = email_item.get("sender", "")
        draft_reply = email_item.get("draft_reply", "")
        
        if action == "auto_archive":
            folder = decision.get("folder") or config.AUTO_ARCHIVE_FOLDER
            logger.info(f"Policy: Auto-archiving email [{email_uid}] '{subject}' from '{sender}' to '{folder}'")
            archive_success = archive_email(email_uid, folder_name=folder, dry_run=dry_run)
            
            db.mark_email_processed(
                account=account,
                message_uid=email_uid,
                folder="INBOX",
                subject=subject,
                sender=sender,
                digest_date=today_str,
                status="archived",
                db_path=db_path
            )
            results["auto_archived"].append({
                "email": email_item,
                "folder": folder,
                "success": archive_success
            })
            
        elif action == "auto_send":
            logger.info(f"Policy: Auto-sending reply for whitelisted sender [{email_uid}] '{sender}'")
            reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
            
            if dry_run:
                logger.info(f"[DRY RUN] Would send email to {sender}: '{reply_subject}' with body: {draft_reply}")
                send_success = True
            else:
                send_success = send_email(sender, reply_subject, draft_reply)
                
            if send_success:
                db.mark_draft_sent(email_uid, db_path=db_path)
                db.mark_email_processed(
                    account=account,
                    message_uid=email_uid,
                    folder="INBOX",
                    subject=subject,
                    sender=sender,
                    digest_date=today_str,
                    status="auto_sent",
                    db_path=db_path
                )
                try:
                    db.create_followup(
                        email_uid=email_uid,
                        sender=sender,
                        subject=reply_subject,
                        direction="waiting_on_them",
                        db_path=db_path
                    )
                except Exception as ex_f:
                    logger.warning(f"Failed creating auto-send followup: {ex_f}")
                
                import html
                # Notify Telegram with the full text sent
                safe_sender = html.escape(str(sender), quote=False)
                safe_subject = html.escape(str(reply_subject), quote=False)
                safe_body = html.escape(str(draft_reply), quote=False)
                notify_msg = (
                    f"🤖 <b>MailPilot Auto-Sent Reply</b> (Whitelisted Sender)\n"
                    f"<b>To:</b> {safe_sender}\n"
                    f"<b>Subject:</b> {safe_subject}\n\n"
                    f"<b>Message Body:</b>\n{safe_body}"
                )
                if config.TELEGRAM_CHAT_ID:
                    send_telegram_message(config.TELEGRAM_CHAT_ID, notify_msg)
                    
            results["auto_sent"].append({
                "email": email_item,
                "to": sender,
                "subject": reply_subject,
                "body": draft_reply,
                "success": send_success
            })
            
        else: # queue_review
            logger.info(f"Policy: Queued email [{email_uid}] '{subject}' for review")
            db.mark_email_processed(
                account=account,
                message_uid=email_uid,
                folder="INBOX",
                subject=subject,
                sender=sender,
                digest_date=today_str,
                status="queued_review",
                db_path=db_path
            )
            results["queued"].append({
                "email": email_item
            })
            
    return results
