import time
import requests
import logging
import sys

import config
import db
import logger_util
from webhook import app, handle_telegram_incoming_message, handle_telegram_callback

logger = logging.getLogger(__name__)

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

def run_polling():
    if not config.TELEGRAM_BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN missing in .env")
        return

    print("🤖 Starting MailPilot in Telegram Polling Mode (Local Dev / Testing)...")
    print("📡 Clearing any active webhook on Telegram...")
    try:
        r = requests.post(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/deleteWebhook", timeout=10)
        print(f"Webhook status: {r.json().get('description', 'Cleared')}")
    except Exception as e:
        print(f"Warning clearing webhook: {e}")

    offset = 0
    print("✅ MailPilot is now actively polling for Telegram messages!")
    print("📱 Send a message (e.g. 'hi', 'digest', '/policy') to your bot in Telegram.\n")
    
    while True:
        try:
            url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {"offset": offset, "timeout": 15}
            response = requests.get(url, params=params, timeout=25)
            
            if response.status_code != 200:
                logger.warning(f"Telegram getUpdates returned HTTP {response.status_code}: {response.text}")
                time.sleep(2)
                continue
                
            updates = response.json().get("result", [])
            for u in updates:
                offset = max(offset, u["update_id"] + 1)
                
                with app.app_context():
                    if "callback_query" in u:
                        logger.info(f"Received callback_query: {u['callback_query'].get('data')}")
                        handle_telegram_callback(u["callback_query"])
                    elif "message" in u:
                        logger.info(f"Received message from chat {u['message'].get('chat', {}).get('id')}: {u['message'].get('text')}")
                        handle_telegram_incoming_message(u["message"])
                    
        except requests.exceptions.Timeout:
            continue
        except requests.exceptions.RequestException as e:
            logger.warning(f"Polling connection warning: {e}")
            time.sleep(3)
        except (KeyboardInterrupt, SystemExit):
            print("\n🛑 Polling stopped.")
            break
        except Exception as e:
            logger.error(f"Unexpected error in polling: {e}", exc_info=True)
            time.sleep(2)

if __name__ == "__main__":
    db.init_db()
    run_polling()
