import logging
import re
from datetime import datetime, timedelta
import dateutil.parser
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
    Fetch, process, save drafts, apply policy, and send Telegram digest with due followups.
    Never fails silently; all errors alert Telegram.
    """
    logger.info("Executing daily digest job...")
    try:
        emails = get_unread_emails(dry_run=dry_run, mark_read=mark_read, filter_processed=True)
        
        # Check overdue followups
        overdue_followups = []
        try:
            overdue_followups = db.get_overdue_followups(db_path=db_path)
        except Exception as ex_fo:
            logger.warning(f"Error fetching overdue followups: {ex_fo}")

        # Auto-close followups matching incoming emails
        for e in (emails or []):
            try:
                db.close_matching_followups(e, db_path=db_path)
            except Exception as ex_fc:
                logger.warning(f"Error closing followups: {ex_fc}")

        if not emails and not overdue_followups:
            logger.info("Daily digest: No unread emails and no overdue followups found.")
            if send_telegram and config.TELEGRAM_CHAT_ID:
                send_telegram_message(config.TELEGRAM_CHAT_ID, "📬 <b>MailPilot Daily Digest</b>\nNo new unread emails.")
            return {"emails": [], "policy_actions": {}, "overdue_followups": []}

        processed = []
        policy_actions = {}
        if emails:
            logger.info(f"Daily digest: Processing {len(emails)} unread email(s)...")
            processed = process_emails(emails, dry_run=dry_run)
            save_drafts(processed, db_path=db_path)
            policy_actions = apply_policy_actions(processed, dry_run=dry_run, db_path=db_path)

        if send_telegram and config.TELEGRAM_CHAT_ID:
            digest_text = format_telegram_digest(processed, due_followups=overdue_followups)
            send_telegram_message(config.TELEGRAM_CHAT_ID, digest_text)
            
            # Proactive Cedar morning audio briefing
            try:
                import voice_engine
                from telegram_client import send_telegram_voice
                user_name = db.get_user_memory("user_name") or "Principal"
                urgent_count = sum(1 for e in processed if e.get("urgency") == "URGENT" or "Action" in e.get("triage_bucket", ""))
                spoken = f"Good morning {user_name}. Cedar here with your executive briefing. "
                if not processed:
                    spoken += "You are all caught up with zero unread emails in your inbox."
                else:
                    spoken += f"You have {len(processed)} email{'s' if len(processed) != 1 else ''} in your morning digest. "
                    if urgent_count > 0:
                        spoken += f"Heads up: {urgent_count} action-required item{'s' if urgent_count != 1 else ''} needing your attention. "
                    top_senders = [e.get('sender', '') for e in processed[:3]]
                    spoken += f"Key communications are from: {', '.join(top_senders)}. Have a productive day ahead!"
                audio = voice_engine.generate_speech(spoken, voice="cedar")
                if audio:
                    send_telegram_voice(config.TELEGRAM_CHAT_ID, audio, caption="🎙️ Cedar Morning Voice Briefing")
            except Exception as e_voice:
                logger.warning(f"Failed generating scheduled voice briefing: {e_voice}")
            
        logger.info("Daily digest job completed successfully.")
        return {"emails": processed, "policy_actions": policy_actions, "overdue_followups": overdue_followups}

    except Exception as e:
        err_msg = f"Daily digest failed: {e}"
        logger.error(err_msg, exc_info=True)
        send_telegram_alert(err_msg)
        raise

def run_hourly_triage(dry_run: bool = False, mark_read: bool = False, db_path=None) -> dict:
    """
    Hourly light triage job:
    1. Fetch unread emails.
    2. Auto-close open 'waiting_on_them' followups matching reply emails.
    3. Evaluate emails against watchlist and AI urgency rules.
    4. Immediately alert for important emails with dedup and cooldown rate limiting.
    5. Apply sender policy actions (auto-archive / auto-send).
    """
    logger.info("Executing hourly light triage job...")
    try:
        emails = get_unread_emails(dry_run=dry_run, mark_read=mark_read, filter_processed=True)
        if not emails:
            logger.info("Hourly triage: No unread emails found.")
            return {"emails": [], "policy_actions": {}}

        # Auto-close followups for incoming emails
        for e in emails:
            try:
                db.close_matching_followups(e, db_path=db_path)
            except Exception as ex_fc:
                logger.warning(f"Error auto-closing followups in hourly triage: {ex_fc}")

        processed = process_emails(emails, dry_run=dry_run)
        save_drafts(processed, db_path=db_path)
        policy_actions = apply_policy_actions(processed, dry_run=dry_run, db_path=db_path)

        # Important Email Monitoring & Alerts
        hourly_limit = getattr(config, "ALERTS_PER_HOUR_MAX", 5)
        overflow_alerts = []

        for e in processed:
            email_uid = str(e.get("id", ""))
            sender = e.get("sender", "")
            subject = e.get("subject", "")
            urgency = e.get("urgency", "NORMAL")
            category = e.get("category", "")

            # Alert dedup: never re-alert for the same email_uid
            if db.has_alerted_for_email(email_uid, db_path=db_path):
                continue

            matched_rules = db.match_watchlist(e, db_path=db_path)
            is_urgent_work_finance = (urgency == "URGENT" and category in ("Work", "Finance"))

            if not matched_rules and not is_urgent_work_finance:
                continue

            # Construct why reason
            reasons = []
            if matched_rules:
                reasons.extend([r["reason"] for r in matched_rules])
            if is_urgent_work_finance:
                reasons.append(f"AI flagged urgent ({category})")
            why_text = "; ".join(reasons)

            # Check hourly cooldown
            recent_alert_count = db.count_alerts_in_last_hour(db_path=db_path)
            if recent_alert_count < hourly_limit:
                alert_text = f"🚨 Important: {sender} — {subject} — {why_text}"
                if config.TELEGRAM_CHAT_ID:
                    send_telegram_message(config.TELEGRAM_CHAT_ID, alert_text)
                db.record_alert(email_uid, alert_reason=why_text, db_path=db_path)
            else:
                overflow_alerts.append({
                    "email_uid": email_uid,
                    "sender": sender,
                    "subject": subject,
                    "why": why_text
                })
                db.record_alert(email_uid, alert_reason=why_text, db_path=db_path)

        # If alerts overflowed, send ONE single summary message
        if overflow_alerts and config.TELEGRAM_CHAT_ID:
            summary_lines = [f"🚨 <b>Important Email Overflow Summary</b> ({len(overflow_alerts)} queued):"]
            for o in overflow_alerts:
                summary_lines.append(f"• <b>{o['sender']}</b> — {o['subject']} — <i>{o['why']}</i>")
            send_telegram_message(config.TELEGRAM_CHAT_ID, "\n".join(summary_lines))

        auto_archived = policy_actions.get("auto_archived", [])
        auto_sent = policy_actions.get("auto_sent", [])
        urgent_emails = [e for e in processed if e.get("urgency") == "URGENT"]

        # Notify routine light triage updates if actions took place
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

def parse_snooze_time(time_str: str, base_time: datetime = None) -> datetime:
    """
    Parse natural language snooze expressions to datetime:
    - 'in 2 hours', '2 hours', 'in 30 mins'
    - 'tomorrow 9am', 'tomorrow at 9am', 'tomorrow 9:00', 'tomorrow'
    - 'next monday', 'next monday 9am'
    """
    now = base_time or datetime.now()
    clean = time_str.strip().lower()
    clean = re.sub(r'^(?:remind me (?:about this )?)?(?:in|at|until)?\s*', '', clean).strip()

    # Relative minutes/hours/days
    m_rel = re.match(r'^(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)$', clean)
    if m_rel:
        val = int(m_rel.group(1))
        unit = m_rel.group(2)
        if unit.startswith('m'):
            return now + timedelta(minutes=val)
        elif unit.startswith('h'):
            return now + timedelta(hours=val)
        elif unit.startswith('d'):
            return now + timedelta(days=val)

    # Tomorrow with hour (e.g., "tomorrow 9am", "tomorrow at 9am", "tomorrow 9:00am", "tomorrow")
    m_tom = re.search(r'tomorrow(?:\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?)?', clean)
    if m_tom:
        target_date = now.date() + timedelta(days=1)
        hour_str = m_tom.group(1)
        minute_str = m_tom.group(2)
        ampm = m_tom.group(3)
        if hour_str:
            hour = int(hour_str)
            minute = int(minute_str) if minute_str else 0
            if ampm:
                if ampm == "pm" and hour < 12:
                    hour += 12
                elif ampm == "am" and hour == 12:
                    hour = 0
            return datetime.combine(target_date, datetime.min.time()).replace(hour=hour, minute=minute)
        else:
            return datetime.combine(target_date, datetime.min.time()).replace(hour=9, minute=0)

    # Next weekday (e.g., "next monday", "next monday 9am")
    weekdays = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6
    }
    m_day = re.search(r'(?:next\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)(?:\s+(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?)?', clean)
    if m_day:
        day_name = m_day.group(1)
        target_weekday = weekdays[day_name]
        days_ahead = (target_weekday - now.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        target_date = now.date() + timedelta(days=days_ahead)
        hour_str = m_day.group(2)
        minute_str = m_day.group(3)
        ampm = m_day.group(4)
        if hour_str:
            hour = int(hour_str)
            minute = int(minute_str) if minute_str else 0
            if ampm:
                if ampm == "pm" and hour < 12:
                    hour += 12
                elif ampm == "am" and hour == 12:
                    hour = 0
            return datetime.combine(target_date, datetime.min.time()).replace(hour=hour, minute=minute)
        else:
            return datetime.combine(target_date, datetime.min.time()).replace(hour=9, minute=0)

    # Fallback to dateutil fuzzy parse
    try:
        parsed = dateutil.parser.parse(clean, default=now, fuzzy=True)
        if parsed <= now:
            parsed = parsed + timedelta(days=1)
        return parsed
    except Exception:
        return now + timedelta(hours=2)

def run_snooze_check(dry_run: bool = False, db_path=None) -> list:
    """
    Check for pending snoozes that have matured and resurface the email via Telegram.
    """
    try:
        due = db.get_due_snoozes(db_path=db_path)
        if not due:
            return []

        logger.info(f"Snooze checker: Resurfacing {len(due)} due email(s)...")
        fired = []
        for s in due:
            db.mark_snooze_fired(s["id"], db_path=db_path)
            email_uid = s["email_uid"]
            draft = db.get_draft_by_uid(email_uid, db_path=db_path)
            subject = draft.get("subject", "") if draft else f"Email #{email_uid}"
            sender = draft.get("sender", "") if draft else "Unknown sender"
            draft_body = draft.get("draft_body", "") if draft else ""

            body_preview = f"\n\n<b>Draft Reply:</b>\n{draft_body}" if draft_body else ""
            msg = (
                f"⏰ <b>Snooze Reminder</b>\n"
                f"Email <b>[#{email_uid}]</b> is ready for your attention!\n"
                f"<b>From:</b> {sender}\n"
                f"<b>Subject:</b> {subject}"
                f"{body_preview}"
            )
            if config.TELEGRAM_CHAT_ID:
                send_telegram_message(config.TELEGRAM_CHAT_ID, msg)
            fired.append(s)

        return fired
    except Exception as e:
        logger.error(f"Snooze check failed: {e}", exc_info=True)
        return []

def create_scheduler(dry_run: bool = False, mark_read: bool = False, standalone: bool = False):
    """Factory to create and configure APScheduler with restart-tolerant misfire parameters."""
    scheduler_cls = BlockingScheduler if standalone else BackgroundScheduler
    scheduler = scheduler_cls()

    # Daily digest job at DIGEST_HOUR:00 (misfire-tolerant: coalesce=True, max_instances=1, misfire_grace_time=300)
    scheduler.add_job(
        func=run_daily_digest,
        trigger=CronTrigger(hour=config.DIGEST_HOUR, minute=0),
        args=[dry_run, mark_read, True],
        id="daily_digest_job",
        name="MailPilot Daily Digest",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300
    )

    # Hourly light triage job every TRIAGE_INTERVAL_MINUTES (misfire-tolerant: coalesce=True, max_instances=1, misfire_grace_time=300)
    scheduler.add_job(
        func=run_hourly_triage,
        trigger=IntervalTrigger(minutes=config.TRIAGE_INTERVAL_MINUTES),
        args=[dry_run, mark_read],
        id="hourly_triage_job",
        name="MailPilot Hourly Triage",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300
    )

    # Snooze checker job running every minute (misfire-tolerant: coalesce=True, max_instances=1, misfire_grace_time=300)
    scheduler.add_job(
        func=run_snooze_check,
        trigger=IntervalTrigger(minutes=1),
        args=[dry_run],
        id="snooze_check_job",
        name="MailPilot Snooze Checker",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300
    )

    return scheduler
