import time
import requests
import logging
import sys

import config
import db
import logger_util
from webhook import app, handle_telegram_incoming_message, handle_telegram_callback

import socket

logger = logging.getLogger(__name__)

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
else:
    sys.stdout.reconfigure(line_buffering=True)

_instance_socket = None

def acquire_instance_lock(port=54322):
    global _instance_socket
    _instance_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        _instance_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        _instance_socket.bind(("127.0.0.1", port))
        return True
    except socket.error:
        print("⚠️ Another instance of MailPilot Telegram Poller is already active. Exiting to avoid 409 Conflict.")
        sys.exit(0)

# Force IPv4 resolution to prevent Windows IPv6 timeout/aborts with Telegram
try:
    _orig_getaddrinfo = socket.getaddrinfo
    def _getaddrinfo_ipv4(host, port, family=0, type=0, proto=0, flags=0):
        return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
    socket.getaddrinfo = _getaddrinfo_ipv4
except Exception:
    pass

def run_polling():
    acquire_instance_lock()
    if not config.TELEGRAM_BOT_TOKEN:
        print("❌ TELEGRAM_BOT_TOKEN missing in .env")
        return

    print("🤖 Starting MailPilot in Telegram Polling Mode (Local Dev / Testing)...")
    print("📡 Clearing any active webhook on Telegram...")
    
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(max_retries=requests.adapters.Retry(total=3, backoff_factor=1, status_forcelist=[502, 503, 504]))
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    try:
        r = session.post(f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/deleteWebhook", timeout=15)
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
            response = session.get(url, params=params, timeout=30)
            
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
                        msg_desc = u["message"].get("text") or ("[Voice Note]" if "voice" in u["message"] else "[Media]")
                        logger.info(f"Received message from chat {u['message'].get('chat', {}).get('id')}: {msg_desc}")
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
