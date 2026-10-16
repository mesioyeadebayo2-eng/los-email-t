import argparse
import logging
import sys
import time

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

import config
import db
import logger_util
from gmail_client import get_unread_emails
from ai_engine import process_emails, save_drafts
from policy_engine import apply_policy_actions
from digest import print_terminal_digest
from scheduler import create_scheduler

logger = logging.getLogger(__name__)

def run_single_digest(dry_run: bool = False, mark_read: bool = False, telegram: bool = False):
    logger.info("🚀 Starting MailPilot...")
    emails = get_unread_emails(dry_run=dry_run, mark_read=mark_read, filter_processed=True)
    
    if not emails:
        logger.info("No unread emails found.")
        print_terminal_digest([])
        return

    logger.info(f"📨 Fetched {len(emails)} unread emails. Passing to AI Engine...")
    processed_emails = process_emails(emails, dry_run=dry_run)
    save_drafts(processed_emails)
    apply_policy_actions(processed_emails, dry_run=dry_run)

    if telegram:
        from telegram_client import send_telegram_message, format_telegram_digest
        logger.info("📱 Sending digest to Telegram...")
        digest_text = format_telegram_digest(processed_emails)
        if config.TELEGRAM_CHAT_ID:
            success = send_telegram_message(config.TELEGRAM_CHAT_ID, digest_text)
            if success:
                logger.info("✅ Telegram digest sent!")
            else:
                logger.error("❌ Failed to send Telegram digest.")
        else:
            logger.error("TELEGRAM_CHAT_ID not set in .env")
    else:
        print_terminal_digest(processed_emails)

def main():
    parser = argparse.ArgumentParser(description="MailPilot - Personal Email Assistant")
    parser.add_argument("--dry-run", action="store_true", help="Run with mock data, no API calls.")
    parser.add_argument("--mark-read", action="store_true", help="Mark fetched emails as read.")
    parser.add_argument("--telegram", action="store_true", help="Send digest to Telegram instead of terminal.")
    parser.add_argument("--daemon", action="store_true", help="Run the scheduler daemon without webhook mode.")
    args = parser.parse_args()

    # Ensure persistence layer is ready
    db.init_db()

    if args.daemon:
        logger.info(f"🚀 Starting MailPilot Daemon (Scheduler-only mode, dry_run={args.dry_run})...")
        scheduler = create_scheduler(dry_run=args.dry_run, mark_read=args.mark_read, standalone=True)
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("🛑 Shutting down MailPilot Daemon...")
    else:
        run_single_digest(dry_run=args.dry_run, mark_read=args.mark_read, telegram=args.telegram)

if __name__ == "__main__":
    main()
