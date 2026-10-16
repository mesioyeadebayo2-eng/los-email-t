import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import config
import db
import logger_util
from gmail_client import get_unread_emails
from ai_engine import process_emails, save_drafts
from policy_engine import apply_policy_actions
from telegram_client import send_telegram_message, format_telegram_digest, send_telegram_alert

logger = logging.getLogger(__name__)

def run_daily_digest(dry_run: bool = False, mark_read: bool = False, send_telegram: bool = True, db_path=None) -> dict:
    """
    Daily digest job:
    Fetch, process, save drafts, apply policy, and send Telegram digest.
    Never fails silently; all errors alert Telegram.
    """
    logger.info("Executing daily digest job...")
    try:
        emails = get_unread_emails(dry_run=dry_run, mark_read=mark_read, filter_processed=True)
        if not emails:
            logger.info("Daily digest: No unread emails found.")
            if send_telegram and config.TELEGRAM_CHAT_ID:
                send_telegram_message(config.TELEGRAM_CHAT_ID, "📬 <b>MailPilot Daily Digest</b>\nNo new unread emails.")
            return {"emails": [], "policy_actions": {}}

        logger.info(f"Daily digest: Processing {len(emails)} unread email(s)...")
        processed = process_emails(emails, dry_run=dry_run)
        save_drafts(processed, db_path=db_path)
        policy_actions = apply_policy_actions(processed, dry_run=dry_run, db_path=db_path)

        if send_telegram and config.TELEGRAM_CHAT_ID:
            digest_text = format_telegram_digest(processed)
            send_telegram_message(config.TELEGRAM_CHAT_ID, digest_text)
            
        logger.info("Daily digest job completed successfully.")
        return {"emails": processed, "policy_actions": policy_actions}

    except Exception as e:
        err_msg = f"Daily digest failed: {e}"
        logger.error(err_msg, exc_info=True)
        send_telegram_alert(err_msg)
        raise

def run_hourly_triage(dry_run: bool = False, mark_read: bool = False, db_path=None) -> dict:
    """
    Hourly light triage job:
    Fetch unread, apply sender_policy auto-actions (archive newsletters/spam-risk,
    auto-send only for whitelisted senders), and notify via Telegram ONLY when something
    was auto-acted on or something URGENT arrives.
    """
    logger.info("Executing hourly light triage job...")
    try:
        emails = get_unread_emails(dry_run=dry_run, mark_read=mark_read, filter_processed=True)
        if not emails:
            logger.info("Hourly triage: No unread emails found.")
            return {"emails": [], "policy_actions": {}}

        processed = process_emails(emails, dry_run=dry_run)
        save_drafts(processed, db_path=db_path)
        policy_actions = apply_policy_actions(processed, dry_run=dry_run, db_path=db_path)

        auto_archived = policy_actions.get("auto_archived", [])
        auto_sent = policy_actions.get("auto_sent", [])
        urgent_emails = [e for e in processed if e.get("urgency") == "URGENT"]

        # Only notify if something was auto-acted on OR something URGENT arrived
        should_notify = bool(auto_archived or auto_sent or urgent_emails)
        if should_notify and config.TELEGRAM_CHAT_ID:
            lines = ["⚡ <b>MailPilot Light Triage Update</b>"]
            
            if urgent_emails:
                lines.append("\n🔴 <b>URGENT Emails Arrived:</b>")
                for u in urgent_emails:
                    lines.append(f"• <b>[{u['id']}] {u['sender']}:</b> {u['subject']}")
                    
            if auto_archived:
                lines.append(f"\n📦 <b>Auto-Archived:</b> {len(auto_archived)} item(s) moved to {config.AUTO_ARCHIVE_FOLDER}")
                for a in auto_archived:
                    lines.append(f"• [{a['email']['id']}] {a['email']['sender']} - {a['email']['subject']}")
                    
            if auto_sent:
                lines.append(f"\n✉️ <b>Auto-Sent:</b> {len(auto_sent)} reply/replies to whitelisted sender(s)")
                for s in auto_sent:
                    lines.append(f"• To: {s['to']} ({s['subject']})")
                    
            send_telegram_message(config.TELEGRAM_CHAT_ID, "\n".join(lines))
            
        logger.info("Hourly triage job completed successfully.")
        return {"emails": processed, "policy_actions": policy_actions}

    except Exception as e:
        err_msg = f"Hourly triage failed: {e}"
        logger.error(err_msg, exc_info=True)
        send_telegram_alert(err_msg)
        raise

def create_scheduler(dry_run: bool = False, mark_read: bool = False, standalone: bool = False):
    """Factory to create and configure APScheduler."""
    scheduler_cls = BlockingScheduler if standalone else BackgroundScheduler
    scheduler = scheduler_cls()

    # Daily digest job at DIGEST_HOUR:00
    scheduler.add_job(
        func=run_daily_digest,
        trigger=CronTrigger(hour=config.DIGEST_HOUR, minute=0),
        args=[dry_run, mark_read, True],
        id="daily_digest_job",
        name="MailPilot Daily Digest",
        replace_existing=True
    )

    # Hourly light triage job every TRIAGE_INTERVAL_MINUTES
    scheduler.add_job(
        func=run_hourly_triage,
        trigger=IntervalTrigger(minutes=config.TRIAGE_INTERVAL_MINUTES),
        args=[dry_run, mark_read],
        id="hourly_triage_job",
        name="MailPilot Hourly Triage",
        replace_existing=True
    )

    return scheduler
