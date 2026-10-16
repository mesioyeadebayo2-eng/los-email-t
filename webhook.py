import os
import re
import logging
from flask import Flask, request, jsonify

import config
import db
import logger_util
from gmail_client import get_unread_emails, send_email
from ai_engine import process_emails, save_drafts, parse_user_command
from policy_engine import apply_policy_actions
from telegram_client import (
    send_telegram_message,
    format_telegram_digest,
    send_send_confirmation,
    answer_callback_query
)
from scheduler import create_scheduler

logger = logging.getLogger(__name__)

app = Flask(__name__)

# Start background scheduler if enabled and not in testing
scheduler = None
def init_background_scheduler():
    global scheduler
    if os.getenv("TESTING") == "1":
        return
    if os.getenv("ENABLE_SCHEDULER", "true").lower() == "true":
        if scheduler is None:
            try:
                scheduler = create_scheduler(dry_run=False, mark_read=False, standalone=False)
                scheduler.start()
                logger.info("APScheduler started in background for Flask app.")
            except Exception as e:
                logger.error(f"Failed to start background scheduler: {e}")

init_background_scheduler()

@app.route('/healthz', methods=['GET'])
def healthz():
    db_healthy = db.check_db_health()
    status_code = 200 if db_healthy else 500
    return jsonify({"ok": db_healthy, "db": db_healthy}), status_code

@app.route('/webhook', methods=['POST'])
def webhook():
    # 1. Validate X-Telegram-Bot-Api-Secret-Token Header
    expected_token = config.WEBHOOK_SECRET_TOKEN
    if expected_token:
        incoming_token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if incoming_token != expected_token:
            logger.warning("Rejected webhook request: Invalid or missing X-Telegram-Bot-Api-Secret-Token")
            return jsonify({"error": "Forbidden"}), 403

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "ok"}), 200

    # 2. Handle Inline Button Callback Queries (Confirm, Edit, Cancel)
    if "callback_query" in data:
        return handle_telegram_callback(data["callback_query"])

    # 3. Handle Regular Messages
    if "message" in data:
        return handle_telegram_incoming_message(data["message"])

    return jsonify({"status": "ok"}), 200

def handle_telegram_callback(cb: dict):
    query_id = cb.get("id")
    user_id = str(cb.get("from", {}).get("id", ""))
    message = cb.get("message", {})
    chat_id = str(message.get("chat", {}).get("id", ""))

    # Verify sender matches exact Chat ID in config
    if config.TELEGRAM_CHAT_ID and user_id != config.TELEGRAM_CHAT_ID and chat_id != config.TELEGRAM_CHAT_ID:
        logger.warning(f"Unauthorized callback query from user ID: {user_id}")
        answer_callback_query(query_id, "Unauthorized")
        return jsonify({"status": "ignored"}), 200

    callback_data = cb.get("data", "")
    if ":" not in callback_data:
        answer_callback_query(query_id)
        return jsonify({"status": "ok"}), 200

    action, send_id = callback_data.split(":", 1)

    if action == "confirm":
        # Atomically confirm: double-tap protection
        acquired, send_data = db.confirm_pending_send(send_id)
        if acquired and send_data:
            answer_callback_query(query_id, "Sending email...")
            to_addr = send_data["to_address"]
            subj = send_data["subject"]
            body = send_data["body"]
            
            send_telegram_message(chat_id, f"✉️ <b>Sending email to {to_addr}...</b>")
            success = send_email(to_addr, subj, body)
            if success:
                send_telegram_message(chat_id, f"✅ Email sent successfully to <b>{to_addr}</b>!")
            else:
                send_telegram_message(chat_id, f"❌ Failed to send email to <b>{to_addr}</b>. Check logs.")
        else:
            answer_callback_query(query_id, "Already sent or cancelled")
            send_telegram_message(chat_id, "⚠️ This email has already been sent, cancelled, or does not exist.")

    elif action == "cancel":
        cancelled = db.cancel_pending_send(send_id)
        answer_callback_query(query_id, "Cancelled")
        if cancelled:
            send_telegram_message(chat_id, "❌ Email sending was cancelled.")
        else:
            send_telegram_message(chat_id, "⚠️ Email was already processed or does not exist.")

    elif action == "edit":
        answer_callback_query(query_id, "Edit requested")
        send_data = db.get_pending_send(send_id)
        if send_data:
            send_telegram_message(
                chat_id,
                f"✏️ <b>To edit this email</b>, please send a message with your updated instructions, or type:\n"
                f"<code>Send email to {send_data['to_address']} about {send_data['subject']} saying &lt;new body&gt;</code>"
            )
        else:
            send_telegram_message(chat_id, "✏️ Please type a new command to draft your email.")

    return jsonify({"status": "ok"}), 200

def handle_telegram_incoming_message(msg: dict):
    chat_id = str(msg.get("chat", {}).get("id", ""))
    
    # Verify sender matches exact Chat ID in config
    if config.TELEGRAM_CHAT_ID and chat_id != config.TELEGRAM_CHAT_ID:
        logger.warning(f"Unauthorized chat ID attempt from: {chat_id}")
        return jsonify({"status": "ignored"}), 200

    text_body = msg.get("text", "").strip()
    if not text_body:
        return jsonify({"status": "ok"}), 200

    # Whitelist & policy management commands
    if text_body.startswith("/"):
        parts = text_body.split(maxsplit=1)
        command = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if command == "/allow":
            if not arg or "@" not in arg:
                send_telegram_message(chat_id, "Usage: <code>/allow email@example.com</code>")
            else:
                email_target = arg.lower()
                db.set_sender_policy(email_target, "auto_reply_whitelist")
                send_telegram_message(chat_id, f"✅ Whitelisted <code>{email_target}</code> for automatic replies.")
            return jsonify({"status": "ok"}), 200

        elif command == "/block":
            if not arg or "@" not in arg:
                send_telegram_message(chat_id, "Usage: <code>/block email@example.com</code>")
            else:
                email_target = arg.lower()
                db.set_sender_policy(email_target, "auto_archive")
                send_telegram_message(chat_id, f"🚫 Blocked <code>{email_target}</code>. Incoming emails will be auto-archived.")
            return jsonify({"status": "ok"}), 200

        elif command == "/policy":
            policies = db.list_sender_policies()
            if policies:
                lines = ["📋 <b>Sender Autonomy Policies:</b>\n"]
                for p in policies:
                    action_badge = "✅ Whitelist" if p["auto_action"] == "auto_reply_whitelist" else "🚫 Auto-Archive"
                    lines.append(f"• <code>{p['email']}</code>: {action_badge} (<code>{p['auto_action']}</code>)")
                send_telegram_message(chat_id, "\n".join(lines))
            else:
                send_telegram_message(chat_id, "📋 No custom sender policies configured.\nUse <code>/allow email@x.com</code> or <code>/block email@x.com</code>.")
            return jsonify({"status": "ok"}), 200

    # Process conversational intent
    handle_telegram_intent(text_body, chat_id)
    return jsonify({"status": "ok"}), 200

def handle_telegram_intent(cmd: str, chat_id: str):
    send_telegram_message(chat_id, "🤖 <i>Thinking...</i>")
    
    intent_data = parse_user_command(cmd)
    intent = intent_data.get("intent", "unknown")

    if intent == "digest":
        send_telegram_message(chat_id, "⏳ Fetching and processing your unread emails...")
        emails = get_unread_emails(dry_run=False, mark_read=False, filter_processed=True)
        if not emails:
            send_telegram_message(chat_id, "📬 <b>MailPilot Digest</b>\nNo new unread emails.")
            return
            
        processed = process_emails(emails, dry_run=False)
        save_drafts(processed)
        apply_policy_actions(processed, dry_run=False)
        
        digest_text = format_telegram_digest(processed)
        send_telegram_message(chat_id, digest_text)

    elif intent == "summarize":
        n = intent_data.get("count", 5)
        recent_emails = db.get_recent_processed_emails(limit=n)
        if not recent_emails:
            send_telegram_message(chat_id, "No recent processed emails found in the database. Ask me for a digest first!")
            return
        
        msg = f"<b>Summary of last {len(recent_emails)} processed email(s):</b>\n\n"
        for e in recent_emails:
            msg += f"• <b>[{e['message_uid']}] {e['subject']}</b>\n  <i>From:</i> {e['sender']}\n  <i>Status:</i> {e['status']}\n"
        send_telegram_message(chat_id, msg)

    elif intent == "get_draft":
        # Extract UID (handles "#3", "3", etc.)
        raw_id = str(intent_data.get("email_id", "")).strip()
        e_id = raw_id.lstrip("#")
        
        # Look up directly in SQLite database across restarts
        draft = db.get_draft_by_uid(e_id)
        if draft and draft.get("draft_body"):
            send_telegram_message(
                chat_id,
                f"<b>Draft for [{e_id}] (To: {draft.get('sender', '')}):</b>\n"
                f"<b>Subject:</b> Re: {draft.get('subject', '')}\n\n"
                f"{draft['draft_body']}"
            )
        else:
            send_telegram_message(chat_id, f"No draft found in database for ID {raw_id}.")
            
    elif intent == "send_email":
        to_email = intent_data.get("to_email")
        subject = intent_data.get("subject", "Update")
        body = intent_data.get("body", "")
        
        if not to_email or "@" not in to_email:
            send_telegram_message(chat_id, "I need a valid email address to send that. Could you repeat the command with the email address?")
            return

        # Two-step flow: store pending send and reply with preview + inline buttons
        confirmation_id = db.create_pending_send(to_email, subject, body)
        send_send_confirmation(chat_id, confirmation_id, to_email, subject, body)

    else:
        reply = intent_data.get("response", "I didn't quite catch that. You can ask for a digest, a summary of recent emails, or ask me to draft/send an email.")
        send_telegram_message(chat_id, reply)

if __name__ == "__main__":
    # Running directly in development (production uses gunicorn)
    app.run(port=5000, debug=False)
