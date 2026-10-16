import requests
import logging
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

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Telegram API error: {e}")
        if e.response is not None:
            logger.error(f"Response data: {e.response.text}")
        return False

def send_telegram_alert(error_message: str) -> bool:
    """Send an error alert to TELEGRAM_CHAT_ID, never failing silently."""
    short_msg = str(error_message).strip()
    # Telegram max message length safety
    if len(short_msg) > 500:
        short_msg = short_msg[:500] + "..."
    alert_text = f"⚠️ MailPilot error: {short_msg}"
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
        response = requests.post(url, json=payload, timeout=5)
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
    message_body = (
        f"✉️ <b>Outgoing Email Preview</b>\n\n"
        f"<b>To:</b> <code>{to_address}</code>\n"
        f"<b>Subject:</b> {subject}\n\n"
        f"<b>Body:</b>\n{body}\n\n"
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
        cat = e.get("category", "Unknown")
        draft_status = "✍️ Draft ready" if e.get("draft_reply") else "🚫 No draft"

        block = f"{emoji} <b>[{e['id']}] {e['sender']}</b>\n"
        block += f"<b>Subject:</b> {e['subject']}\n"
        block += f"<b>Cat:</b> {cat} | {draft_status}\n"
        
        attachments = e.get("attachments", [])
        if attachments:
            block += f"<b>Attachments:</b> {', '.join(attachments)}\n"
            
        block += f"<b>Summary:</b> {e.get('summary', '')}"
        
        # Limit to 4096 chars for Telegram limits
        if len("\n\n".join(lines) + "\n\n" + block) > 4000:
            lines.append("\n<i>...truncated due to length...</i>")
            break
            
        lines.append(block)

    return "\n\n".join(lines)
