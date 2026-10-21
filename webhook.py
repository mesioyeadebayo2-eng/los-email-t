import os
import re
import html
import logging
from flask import Flask, request, jsonify

import config
import db
import logger_util
from gmail_client import get_unread_emails, send_email, get_unread_count
from ai_engine import process_emails, save_drafts, parse_user_command
from policy_engine import apply_policy_actions
from telegram_client import (
    send_telegram_message,
    format_telegram_digest,
    send_send_confirmation,
    answer_callback_query,
    send_telegram_voice,
    send_chat_action,
    download_telegram_file
)
import voice_engine
import attachment_handler
from scheduler import create_scheduler

logger = logging.getLogger(__name__)

app = Flask(__name__)

# Start background scheduler if enabled and not in testing
scheduler = None
def init_background_scheduler():
    global scheduler
    if os.getenv("TESTING") == "1":
        return
    # On Render or multi-service deployments, the dedicated background worker runs the scheduler.
    default_enable = "false" if os.getenv("RENDER") else "true"
    if os.getenv("ENABLE_SCHEDULER", default_enable).lower() == "true":
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
    gmail_configured = bool(config.GMAIL_USER and config.GMAIL_APP_PASSWORD)
    openai_configured = bool(config.OPENAI_API_KEY)
    telegram_configured = bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)
    status_code = 200 if db_healthy else 500
    return jsonify({
        "ok": db_healthy,
        "db": db_healthy,
        "gmail": gmail_configured,
        "openai": openai_configured,
        "telegram": telegram_configured,
        "gmail_user": config.GMAIL_USER[:4] + "***" if config.GMAIL_USER else ""
    }), status_code

@app.route('/smtp-check', methods=['GET'])
def smtp_check():
    import smtplib
    results = {}
    # Test 465 SSL
    try:
        s = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=7)
        s.login(config.GMAIL_USER, config.GMAIL_APP_PASSWORD)
        s.quit()
        results["port_465"] = "OK"
    except Exception as e:
        results["port_465"] = f"{type(e).__name__}: {e}"
        
    # Test 587 STARTTLS
    try:
        s587 = smtplib.SMTP("smtp.gmail.com", 587, timeout=7)
        s587.starttls()
        s587.login(config.GMAIL_USER, config.GMAIL_APP_PASSWORD)
        s587.quit()
        results["port_587"] = "OK"
    except Exception as e:
        results["port_587"] = f"{type(e).__name__}: {e}"
        
    return jsonify(results), 200

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

def extract_clean_email(text: str):
    """Extract a valid, sanitized email address from arbitrary text, stripping trailing punctuation and extra words."""
    if not text:
        return None
    matches = re.findall(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,10}', text)
    if matches:
        candidate = matches[0].strip().rstrip('.,;:')
        return candidate.lower()
    return None

def is_confirmation_phrase(text: str) -> bool:
    if not text:
        return False
    clean = text.strip().lower()
    # 1. Exact confirmations ALWAYS return True
    if clean in (
        "yes", "y", "send", "send it", "send now", "confirm", "send to client", "send to receiver", 
        "do it", "approved", "go ahead", "ok", "okay", "proceed", "deliver", "ship it", "sure", 
        "please send", "send please", "send the email", "send email now", "yes please", "yes send", "send it now"
    ):
        return True
    # 2. If the user's message contains an email address or is a new send command, it is NEVER a confirmation!
    if "@" in clean:
        return False
    if any(clean.startswith(prefix) for prefix in (
        "send to ", "send an email to ", "send email to ", "email to ", "write to ", "mail to ", "send a "
    )):
        return False
    # 3. Match only entire phrases, never open-ended start of sentence
    return bool(re.match(r'^(yes|confirm|send\s*(it|now|to\s*client|to\s*receiver|the\s*email)?|do\s*it|approved?|proceed|deliver)$', clean))

def is_cancellation_phrase(text: str) -> bool:
    if not text:
        return False
    clean = text.strip().lower()
    if "@" in clean:
        return False
    return clean in ("cancel", "stop", "don't send", "dont send", "no", "nevermind", "abort", "cancel it", "discard")

def process_pending_send_dispatch(chat_id: str, pending_data: dict, voice_reply: bool = False):
    acquired, send_data = db.confirm_pending_send(pending_data["id"])
    if acquired and send_data:
        to_addr = send_data["to_address"]
        subj = send_data["subject"]
        body = send_data["body"]
        attachments = send_data.get("attachments", [])
        att_desc = f" with {len(attachments)} attachment(s)" if attachments else ""
        send_telegram_message(chat_id, f"✉️ <b>Delivering email to client {to_addr}{att_desc}...</b>")
        success = send_email(to_addr, subj, body, attachments=attachments)
        if success:
            send_reply(
                chat_id,
                f"✅ <b>Email delivered successfully to client!</b>\n\n"
                f"<b>To:</b> <code>{to_addr}</code>\n"
                f"<b>Subject:</b> {subj}",
                voice_reply,
                spoken_override=f"Your email to {to_addr} has been delivered successfully."
            )
        else:
            send_reply(
                chat_id,
                f"❌ Failed to send email to <b>{to_addr}</b> via Gmail SMTP. Check server logs.",
                voice_reply,
                spoken_override=f"I was unable to send the email to {to_addr}. Please check your Gmail connection."
            )
    else:
        send_reply(chat_id, "⚠️ This email has already been sent or cancelled.", voice_reply)
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
            attachments = send_data.get("attachments", [])
            
            att_desc = f" with {len(attachments)} attachment(s)" if attachments else ""
            send_telegram_message(chat_id, f"✉️ <b>Delivering email to client {to_addr}{att_desc}...</b>")
            success = send_email(to_addr, subj, body, attachments=attachments)
            if success:
                send_telegram_message(chat_id, f"✅ <b>Email delivered successfully to client!</b>\n\n<b>To:</b> <code>{to_addr}</code>\n<b>Subject:</b> {subj}")
            else:
                send_telegram_message(chat_id, f"❌ Failed to send email to <b>{to_addr}</b> via Gmail SMTP. Check server logs.")
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

def send_reply(chat_id: str, text: str, voice_reply: bool = False, spoken_override: str = None):
    """Deliver a text message, and optionally a spoken voice note in Cedar's voice."""
    send_telegram_message(chat_id, text)
    if voice_reply:
        send_chat_action(chat_id, "record_voice")
        to_speak = spoken_override or re.sub(r'<[^>]+>', '', text)
        audio = voice_engine.generate_speech(to_speak, voice="cedar")
        if audio:
            send_telegram_voice(chat_id, audio)

def handle_telegram_incoming_message(msg: dict):
    chat_id = str(msg.get("chat", {}).get("id", ""))
    
    # Verify sender matches exact Chat ID in config
    if config.TELEGRAM_CHAT_ID and chat_id != config.TELEGRAM_CHAT_ID:
        logger.warning(f"Unauthorized chat ID attempt from: {chat_id}")
        return jsonify({"status": "ignored"}), 200

    # 1. Voice Note / Audio handling
    voice_obj = msg.get("voice") or msg.get("audio")
    if voice_obj:
        file_id = voice_obj.get("file_id")
        if file_id:
            logger.info(f"Received Telegram voice note from chat {chat_id} (file_id: {file_id[:16]}...)")
            send_chat_action(chat_id, "record_voice")
            audio_bytes = download_telegram_file(file_id)
            if not audio_bytes:
                send_telegram_message(chat_id, "⚠️ Could not download voice note. Please try again.")
                return jsonify({"status": "ok"}), 200
            
            transcribed_text = voice_engine.transcribe_audio(audio_bytes)
            if not transcribed_text:
                send_telegram_message(chat_id, "🎙️ I received your voice note, but could not transcribe speech. Could you please try again?")
                return jsonify({"status": "ok"}), 200

            logger.info(f"Voice note transcribed: '{transcribed_text}'")
            send_telegram_message(chat_id, f"🎙️ <i>Voice input:</i> \"{html.escape(transcribed_text)}\"")

            latest_pending = db.get_latest_pending_send()
            if latest_pending:
                if any(phrase in transcribed_text.lower() for phrase in (
                    "the body of the letter", "body of the letter", "the body of email", "what is the body", 
                    "show body", "what does it say", "show letter", "view body", "check body", "read the body"
                )):
                    send_send_confirmation(chat_id, latest_pending["id"], latest_pending["to_address"], latest_pending["subject"], latest_pending["body"], attachments=latest_pending.get("attachments"))
                    send_reply(
                        chat_id,
                        f"📄 <b>Letter Body for {latest_pending['to_address']}:</b>\n\n"
                        f"{html.escape(latest_pending['body'])}\n\n"
                        f"━━━━━━━━━━━━━━━━━━━\n"
                        f"👉 <b>To edit body:</b> Reply <code>Change body to: &lt;new text&gt;</code>\n"
                        f"👉 <b>To send:</b> Tap <b>[✅ Send to Client]</b> above or reply <b>\"SEND\"</b>.",
                        voice_reply=True,
                        spoken_override=f"The current letter body to {latest_pending['to_address']} reads: {latest_pending['body']}"
                    )
                    return jsonify({"status": "ok"}), 200

                if is_confirmation_phrase(transcribed_text):
                    return process_pending_send_dispatch(chat_id, latest_pending, voice_reply=True)
                elif is_cancellation_phrase(transcribed_text):
                    db.cancel_pending_send(latest_pending["id"])
                    send_reply(chat_id, f"❌ Cancelled pending email to <b>{latest_pending['to_address']}</b>.", voice_reply=True)
                    return jsonify({"status": "ok"}), 200

            handle_telegram_intent(transcribed_text, chat_id, voice_reply=True)
            return jsonify({"status": "ok"}), 200

    # 2. Photo / Image handling (Multimodal Vision or Email attachment)
    if "photo" in msg:
        photos = msg.get("photo", [])
        if photos:
            best_photo = photos[-1]
            file_id = best_photo.get("file_id")
            caption = (msg.get("caption") or "").strip()
            logger.info(f"Received Telegram photo from chat {chat_id} (file_id: {file_id[:16]}...)")
            send_chat_action(chat_id, "typing")
            
            img_bytes = download_telegram_file(file_id)
            if not img_bytes:
                send_telegram_message(chat_id, "⚠️ Could not download the photo. Please try again.")
                return jsonify({"status": "ok"}), 200
                
            local_path = attachment_handler.save_upload(img_bytes, f"photo_{file_id[:8]}.jpg")
            
            # Check if user instructed to email this photo
            if any(k in caption.lower() for k in ["send to", "email to", "send this", "email this"]):
                intent_data = parse_user_command(caption)
                to_email = extract_clean_email(caption) or extract_clean_email(intent_data.get("to_email") or "")
                subject = intent_data.get("subject") or "Photo Attached"
                body = intent_data.get("body") or "Please find the attached photo."
                if to_email and "@" in to_email:
                    confirmation_id = db.create_pending_send(to_email, subject, body, attachments=[local_path])
                    send_send_confirmation(chat_id, confirmation_id, to_email, subject, body, attachments=[local_path])
                    return jsonify({"status": "ok"}), 200

            # Otherwise analyze the image with GPT-4o-mini Vision
            send_telegram_message(chat_id, "🔍 <i>Analyzing image with Cedar Vision...</i>")
            analysis = attachment_handler.analyze_image(img_bytes, user_prompt=caption)
            send_reply(chat_id, f"🖼️ <b>Image Analysis:</b>\n\n{html.escape(analysis)}", voice_reply=("voice" in caption.lower()))
            return jsonify({"status": "ok"}), 200

    # 3. Document handling (PDF, CSV, TXT, images sent as files)
    if "document" in msg:
        doc = msg.get("document", {})
        file_id = doc.get("file_id")
        file_name = doc.get("file_name", "document.pdf")
        mime_type = doc.get("mime_type", "")
        caption = (msg.get("caption") or "").strip()
        logger.info(f"Received Telegram document '{file_name}' from chat {chat_id}")
        send_chat_action(chat_id, "typing")
        
        doc_bytes = download_telegram_file(file_id)
        if not doc_bytes:
            send_telegram_message(chat_id, "⚠️ Could not download the document. Please try again.")
            return jsonify({"status": "ok"}), 200
            
        local_path = attachment_handler.save_upload(doc_bytes, file_name)
        
        # Check if user wants to email this document
        if any(k in caption.lower() for k in ["send to", "email to", "send this", "email this"]):
            intent_data = parse_user_command(caption)
            to_email = extract_clean_email(caption) or extract_clean_email(intent_data.get("to_email") or "")
            subject = intent_data.get("subject") or f"Document: {file_name}"
            body = intent_data.get("body") or f"Please find attached {file_name}."
            if to_email and "@" in to_email:
                confirmation_id = db.create_pending_send(to_email, subject, body, attachments=[local_path])
                send_send_confirmation(chat_id, confirmation_id, to_email, subject, body, attachments=[local_path])
                return jsonify({"status": "ok"}), 200

        # If document is an image (sent as file)
        if mime_type.startswith("image/"):
            send_telegram_message(chat_id, "🔍 <i>Analyzing image with Cedar Vision...</i>")
            analysis = attachment_handler.analyze_image(doc_bytes, user_prompt=caption)
            send_reply(chat_id, f"🖼️ <b>Image Analysis:</b>\n\n{html.escape(analysis)}", voice_reply=("voice" in caption.lower()))
            return jsonify({"status": "ok"}), 200
            
        # If document is PDF or text
        send_telegram_message(chat_id, f"📄 <i>Reading document '{html.escape(file_name)}'...</i>")
        doc_text = attachment_handler.extract_document_text(doc_bytes, file_name)
        summary = attachment_handler.summarize_document(doc_text, file_name, user_instruction=caption)
        send_reply(chat_id, f"📄 <b>Document Insights ({html.escape(file_name)}):</b>\n\n{html.escape(summary)}", voice_reply=("voice" in caption.lower()))
        return jsonify({"status": "ok"}), 200

    # 4. Text message handling
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

        elif command == "/notes":
            notes = db.get_personal_notes(10)
            if not notes:
                send_telegram_message(chat_id, "📝 You don't have any notes saved yet. Tell me 'Note down...' to save one!")
            else:
                lines = ["📝 <b>Your Personal Notes & Reminders:</b>\n"]
                for n in notes:
                    lines.append(f"• <b>#{n['id']}</b>: {html.escape(n['content'])}")
                send_telegram_message(chat_id, "\n".join(lines))
            return jsonify({"status": "ok"}), 200

        elif command == "/briefing":
            send_telegram_message(chat_id, "🎙️ <i>Preparing your Cedar audio briefing...</i>")
            handle_telegram_intent("give me my morning audio briefing", chat_id, voice_reply=True)
            return jsonify({"status": "ok"}), 200

    # Check if user confirmed or cancelled an existing pending send
    latest_pending = db.get_latest_pending_send()
    if latest_pending:
        # 1. User wants to check/view the body of the letter
        if any(phrase in text_body.lower() for phrase in (
            "the body of the letter", "body of the letter", "the body of email", "what is the body", 
            "show body", "what does it say", "show letter", "view body", "check body", "read the body"
        )):
            send_send_confirmation(chat_id, latest_pending["id"], latest_pending["to_address"], latest_pending["subject"], latest_pending["body"], attachments=latest_pending.get("attachments"))
            send_telegram_message(
                chat_id,
                f"📄 <b>Letter Body for {latest_pending['to_address']}:</b>\n\n"
                f"{html.escape(latest_pending['body'])}\n\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"👉 <b>To edit body:</b> Reply <code>Change body to: &lt;new text&gt;</code>\n"
                f"👉 <b>To send:</b> Tap <b>[✅ Send to Client]</b> above or reply <b>\"SEND\"</b>."
            )
            return jsonify({"status": "ok"}), 200

        # 2. User wants to update/edit the body directly in chat
        if bool(re.match(r'^(change\s*body\s*to|update\s*body\s*to|edit\s*body\s*to|change\s*message\s*to|update\s*message\s*to|edit\s*body|update\s*body|body|message)\s*[:=]\s*', text_body, flags=re.IGNORECASE)):
            from ai_engine import sanitize_email_body
            new_body = re.sub(r'^(change\s*body\s*to|update\s*body\s*to|edit\s*body\s*to|change\s*message\s*to|update\s*message\s*to|edit\s*body|update\s*body|body|message)\s*[:=]\s*', '', text_body, flags=re.IGNORECASE).strip()
            clean_body = sanitize_email_body(new_body)
            db.update_pending_send_body(latest_pending["id"], clean_body)
            send_send_confirmation(chat_id, latest_pending["id"], latest_pending["to_address"], latest_pending["subject"], clean_body, attachments=latest_pending.get("attachments"))
            send_telegram_message(chat_id, f"✏️ <b>Letter body updated!</b> Review the preview above and tap <b>[✅ Send to Client]</b> or reply <b>\"SEND\"</b>.")
            return jsonify({"status": "ok"}), 200

        # 3. User confirmed sending
        if is_confirmation_phrase(text_body):
            return process_pending_send_dispatch(chat_id, latest_pending, voice_reply=False)
        # 4. User cancelled sending
        elif is_cancellation_phrase(text_body):
            db.cancel_pending_send(latest_pending["id"])
            send_telegram_message(chat_id, f"❌ Cancelled pending email to <b>{latest_pending['to_address']}</b>.")
            return jsonify({"status": "ok"}), 200

    # Check if user specifically requested a voice reply in text
    voice_requested = any(w in text_body.lower() for w in ["voice note", "in voice", "speak to me", "voice reply", "audio message", "say it"])
    handle_telegram_intent(text_body, chat_id, voice_reply=voice_requested)
    return jsonify({"status": "ok"}), 200

def handle_telegram_intent(cmd: str, chat_id: str, voice_reply: bool = False):
    send_chat_action(chat_id, "record_voice" if voice_reply else "typing")
    
    intent_data = parse_user_command(cmd)
    intent = intent_data.get("intent", "conversational")

    if intent == "confirm_send":
        latest_pending = db.get_latest_pending_send()
        if latest_pending:
            return process_pending_send_dispatch(chat_id, latest_pending, voice_reply)
        else:
            send_reply(chat_id, "There are no pending emails waiting to be sent.", voice_reply)

    elif intent == "cancel_send":
        latest_pending = db.get_latest_pending_send()
        if latest_pending:
            db.cancel_pending_send(latest_pending["id"])
            send_reply(chat_id, f"❌ Cancelled pending email to <b>{latest_pending['to_address']}</b>.", voice_reply)
        else:
            send_reply(chat_id, "There are no pending emails to cancel.", voice_reply)

    elif intent == "update_body":
        latest_pending = db.get_latest_pending_send()
        new_body = intent_data.get("new_body", "").strip()
        if latest_pending and new_body:
            from ai_engine import sanitize_email_body
            clean_body = sanitize_email_body(new_body)
            db.update_pending_send_body(latest_pending["id"], clean_body)
            send_send_confirmation(chat_id, latest_pending["id"], latest_pending["to_address"], latest_pending["subject"], clean_body, attachments=latest_pending.get("attachments"))
            send_reply(chat_id, f"✏️ <b>Letter body updated!</b> Review the preview above and tap <b>[✅ Send to Client]</b> or reply <b>\"SEND\"</b>.", voice_reply)
        else:
            send_reply(chat_id, "There are no pending emails waiting to be edited.", voice_reply)

    elif intent == "remember":
        key = intent_data.get("key", "user_profile")
        val = intent_data.get("value", "")
        db.set_user_memory(key, val)
        reply = intent_data.get("response", f"Got it! I have remembered that for you.")
        send_reply(chat_id, reply, voice_reply)

    elif intent == "take_note":
        note_content = intent_data.get("note", cmd)
        note_id = db.add_personal_note(note_content)
        reply = intent_data.get("response", f"📝 Saved note #{note_id}: \"{note_content}\"")
        send_reply(chat_id, reply, voice_reply)

    elif intent == "get_notes":
        notes = db.get_personal_notes(10)
        if not notes:
            send_reply(chat_id, "📝 You don't have any notes saved yet. Tell me 'Note down...' or send a voice note to add one!", voice_reply)
        else:
            lines = ["📝 <b>Your Saved Notes & Reminders:</b>\n"]
            for n in notes:
                lines.append(f"• <b>#{n['id']}</b>: {html.escape(n['content'])}")
            spoken = "Here are your latest notes: " + "; ".join([n['content'] for n in notes[:3]])
            send_reply(chat_id, "\n".join(lines), voice_reply, spoken_override=spoken)

    elif intent == "unread_count":
        count = get_unread_count(dry_run=False)
        reply = f"📬 You currently have <b>{count}</b> unread email(s) in your inbox."
        spoken = f"You currently have {count} unread emails in your inbox."
        send_reply(chat_id, reply, voice_reply, spoken_override=spoken)

    elif intent == "digest":
        send_telegram_message(chat_id, "⏳ Fetching and processing your unread emails...")
        emails = get_unread_emails(dry_run=False, mark_read=False, filter_processed=True)
        if not emails:
            msg = "📬 <b>MailPilot Digest</b>\nNo new unread emails."
            send_reply(chat_id, msg, voice_reply, spoken_override="You're all caught up! There are no new unread emails in your inbox.")
            return
            
        processed = process_emails(emails, dry_run=False)
        save_drafts(processed)
        apply_policy_actions(processed, dry_run=False)
        
        digest_text = format_telegram_digest(processed)
        send_telegram_message(chat_id, digest_text)

        email_to_owner = intent_data.get("email_to_owner", False) or any(w in cmd.lower() for w in ("email to myself", "send to my email", "send digest to my email", "email digest to me", "email to the owner", "email to owner"))
        if email_to_owner and config.GMAIL_USER:
            try:
                from digest import send_email_digest_to_owner
                email_sent = send_email_digest_to_owner(processed)
                if email_sent:
                    send_telegram_message(chat_id, f"📧 <i>A copy of this digest has also been sent to your email (<b>{config.GMAIL_USER}</b>)</i>")
            except Exception as e_dig:
                logger.warning(f"Failed sending email digest to owner: {e_dig}")

        if voice_reply:
            urgent_count = sum(1 for e in processed if e.get("urgency") == "URGENT")
            user_name = db.get_user_memory("user_name") or "there"
            spoken = f"Hello {user_name}! I have processed {len(processed)} email{'s' if len(processed) != 1 else ''} for your digest. "
            if urgent_count > 0:
                spoken += f"You have {urgent_count} urgent message that needs your attention. "
            top_emails = [e.get('subject', 'Untitled') for e in processed[:3]]
            spoken += "Top topics include: " + ", ".join(top_emails) + ". Check the cards above for full details."
            audio = voice_engine.generate_speech(spoken, voice="cedar")
            if audio:
                send_telegram_voice(chat_id, audio)

    elif intent == "summarize":
        n = intent_data.get("count", 5)
        recent_emails = db.get_recent_processed_emails(limit=n)
        if not recent_emails:
            send_reply(chat_id, "No recent processed emails found in the database. Ask me for a digest first!", voice_reply)
            return
        
        msg = f"<b>Summary of last {len(recent_emails)} processed email(s):</b>\n\n"
        for e in recent_emails:
            safe_uid = html.escape(str(e.get("message_uid", "")), quote=False)
            safe_subj = html.escape(str(e.get("subject", "")), quote=False)
            safe_sender = html.escape(str(e.get("sender", "")), quote=False)
            safe_status = html.escape(str(e.get("status", "")), quote=False)
            msg += f"• <b>[{safe_uid}] {safe_subj}</b>\n  <i>From:</i> {safe_sender}\n  <i>Status:</i> {safe_status}\n"
        spoken = f"Here is the summary of your last {len(recent_emails)} processed emails: " + "; ".join([e.get('subject', '') for e in recent_emails[:3]])
        send_reply(chat_id, msg, voice_reply, spoken_override=spoken)

    elif intent == "get_draft":
        raw_id = str(intent_data.get("email_id", "")).strip()
        e_id = raw_id.lstrip("#")
        draft = db.get_draft_by_uid(e_id)
        if draft and draft.get("draft_body"):
            safe_sender = html.escape(str(draft.get("sender", "")), quote=False)
            safe_subj = html.escape(str(draft.get("subject", "")), quote=False)
            safe_body = html.escape(str(draft.get("draft_body", "")), quote=False)
            msg = (
                f"<b>Draft for [{e_id}] (To: {safe_sender}):</b>\n"
                f"<b>Subject:</b> Re: {safe_subj}\n\n"
                f"{safe_body}"
            )
            spoken = f"Here is the draft for email {e_id} to {draft.get('sender', '')}. The reply reads: {draft.get('draft_body', '')}"
            send_reply(chat_id, msg, voice_reply, spoken_override=spoken)
        else:
            send_reply(chat_id, f"No draft found in database for ID {raw_id}.", voice_reply)

    elif intent == "send_email":
        to_email = intent_data.get("to_email")
        subject = intent_data.get("subject", "Update")
        body = intent_data.get("body", "")
        
        # 1. Deterministic regex search for recipient email address in the user's command
        extracted = extract_clean_email(cmd)
        if extracted:
            to_email = extracted
        elif to_email and "@" in to_email:
            to_email = extract_clean_email(to_email) or to_email.strip()
        else:
            # Only resolve to owner if the user EXPLICITLY requested to email themselves
            cmd_lower = cmd.lower()
            if any(w in cmd_lower for w in ("to myself", "to my own inbox", "to the owner", "to my email")) and not any(w in cmd_lower for w in ("client", "customer", "receiver", "why will i")):
                to_email = config.GMAIL_USER
            else:
                to_email = None

        if not to_email or "@" not in to_email:
            send_reply(
                chat_id,
                "Who would you like to send this email to? Please provide the client's email address (e.g. <code>client@example.com</code>).",
                voice_reply,
                spoken_override="Who would you like to send this email to? Please provide the client's email address."
            )
            return

        cmd_lower = cmd.lower()
        is_direct = intent_data.get("direct_send", False) or any(w in cmd_lower for w in ("immediately", "right now", "directly", "auto send", "send now", "without preview", "deliver now"))

        if is_direct:
            send_telegram_message(chat_id, f"✉️ <b>Delivering email directly to client {to_email}...</b>")
            success = send_email(to_email, subject, body)
            if success:
                confirmation_id = db.create_pending_send(to_email, subject, body)
                db.confirm_pending_send(confirmation_id)
                send_reply(
                    chat_id,
                    f"✅ <b>Email delivered successfully to client!</b>\n\n<b>To:</b> <code>{to_email}</code>\n<b>Subject:</b> {subject}",
                    voice_reply,
                    spoken_override=f"Your email to {to_email} has been sent directly."
                )
            else:
                send_reply(
                    chat_id,
                    f"❌ Failed to send email to <b>{to_email}</b> via Gmail SMTP. Check server logs.",
                    voice_reply,
                    spoken_override=f"Failed to deliver email to {to_email}."
                )
            return

        confirmation_id = db.create_pending_send(to_email, subject, body)
        send_send_confirmation(chat_id, confirmation_id, to_email, subject, body)
        if voice_reply:
            spoken = f"I've drafted your email to {to_email} with subject {subject}. Please review and tap send to client in Telegram, or reply send."
            audio = voice_engine.generate_speech(spoken, voice="cedar")
            if audio:
                send_telegram_voice(chat_id, audio)

    else:
        # Conversational / general query / personal assistant response
        reply = intent_data.get("response", "I am Cedar, your personal executive assistant. How can I assist you today?")
        send_reply(chat_id, reply, voice_reply)

if __name__ == "__main__":
    # Running directly in development (production uses gunicorn)
    app.run(port=5000, debug=False)
