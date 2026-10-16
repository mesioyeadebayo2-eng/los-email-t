import requests
import logging
import html
import time
import config
import logger_util

logger = logging.getLogger(__name__)

def send_telegram_message(chat_id: str, message_body: str, reply_markup: dict = None) -> bool:
    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("Telegram bot token missing.")
        return False

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message_body,
        "parse_mode": "HTML"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    for attempt in range(2):
        try:
            response = requests.post(url, json=payload, timeout=(15, 30))
            if response.status_code == 400 and "can't parse entities" in response.text:
                logger.warning("Telegram HTML parse error; retrying as plain text without HTML.")
                payload_fallback = dict(payload)
                payload_fallback.pop("parse_mode", None)
                fallback_resp = requests.post(url, json=payload_fallback, timeout=(15, 30))
                fallback_resp.raise_for_status()
                return True
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            if attempt == 0:
                time.sleep(1)
                continue
            logger.error(f"Telegram API error: {e}")
            if e.response is not None:
                logger.error(f"Response data: {e.response.text}")
            return False
    return False

def send_telegram_alert(error_message: str) -> bool:
    """Send an error alert to TELEGRAM_CHAT_ID, never failing silently."""
    short_msg = str(error_message).strip()
    if len(short_msg) > 500:
        short_msg = short_msg[:500] + "..."
    safe_err = html.escape(short_msg, quote=False)
    alert_text = f"⚠️ <b>MailPilot error:</b> {safe_err}"
    logger.error(f"ALERT: {alert_text}")
    
    if not config.TELEGRAM_CHAT_ID:
        logger.warning("TELEGRAM_CHAT_ID not configured; cannot deliver Telegram alert.")
        return False
        
    return send_telegram_message(config.TELEGRAM_CHAT_ID, alert_text)

def answer_callback_query(callback_query_id: str, text: str = None) -> bool:
    """Acknowledge Telegram callback query from button clicks."""
    if not config.TELEGRAM_BOT_TOKEN:
        return False
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Error answering callback query: {e}")
        return False

def send_send_confirmation(chat_id: str, confirmation_id: str, to_address: str, subject: str, body: str) -> bool:
    """
    Send an email preview with inline confirmation keyboard:
    [✅ Confirm] [✏️ Edit] [❌ Cancel]
    """
    safe_to = html.escape(str(to_address), quote=False)
    safe_subject = html.escape(str(subject), quote=False)
    safe_body = html.escape(str(body), quote=False)
    
    message_body = (
        f"✉️ <b>Outgoing Email Preview</b>\n\n"
        f"<b>To:</b> <code>{safe_to}</code>\n"
        f"<b>Subject:</b> {safe_subject}\n\n"
        f"<b>Body:</b>\n{safe_body}\n\n"
        f"<i>Please review and confirm sending:</i>"
    )
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Confirm", "callback_data": f"confirm:{confirmation_id}"},
                {"text": "✏️ Edit", "callback_data": f"edit:{confirmation_id}"},
                {"text": "❌ Cancel", "callback_data": f"cancel:{confirmation_id}"}
            ]
        ]
    }
    return send_telegram_message(chat_id, message_body, reply_markup=keyboard)

def format_telegram_digest(emails: list) -> str:
    if not emails:
        return "📬 <b>MailPilot Digest</b>\nNo new emails today!"

    lines = ["📬 <b>MailPilot Digest</b>\n"]
    for e in emails:
        urgency = e.get("urgency", "NORMAL")
        emoji = "🔴" if urgency == "URGENT" else "🟡" if urgency == "NORMAL" else "⚪"
        cat = html.escape(str(e.get("category", "Unknown")), quote=False)
        draft_status = "✍️ Draft ready" if e.get("draft_reply") else "🚫 No draft"

        safe_id = html.escape(str(e.get("id", "")), quote=False)
        safe_sender = html.escape(str(e.get("sender", "")), quote=False)
        safe_subject = html.escape(str(e.get("subject", "")), quote=False)
        safe_summary = html.escape(str(e.get("summary", "")), quote=False)

        block = f"{emoji} <b>[{safe_id}] {safe_sender}</b>\n"
        block += f"<b>Subject:</b> {safe_subject}\n"
        block += f"<b>Cat:</b> {cat} | {draft_status}\n"
        
        attachments = e.get("attachments", [])
        if attachments:
            safe_attachments = html.escape(", ".join(str(a) for a in attachments), quote=False)
            block += f"<b>Attachments:</b> {safe_attachments}\n"
            
        block += f"<b>Summary:</b> {safe_summary}"
        
        # Limit to 4096 chars for Telegram limits
        if len("\n\n".join(lines) + "\n\n" + block) > 4000:
            lines.append("\n<i>...truncated due to length...</i>")
            break
            
        lines.append(block)

    return "\n\n".join(lines)
