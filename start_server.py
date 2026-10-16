import subprocess
import time
import requests
import sys
import os

# Ensure pyngrok is installed
try:
    from pyngrok import ngrok
except ImportError:
    print("pyngrok not installed. Please install it first.")
    sys.exit(1)

import config
import logger_util

if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

def main():
    print("Starting Flask webhook server in the background...")
    flask_process = subprocess.Popen([sys.executable, "webhook.py"])

    print("Waiting for Flask to boot...")
    time.sleep(3)

    print("Opening ngrok tunnel on port 5000...")
    try:
        ngrok_token = os.getenv("NGROK_AUTHTOKEN")
        if not ngrok_token:
            print("ERROR: NGROK_AUTHTOKEN not set in .env")
            flask_process.terminate()
            sys.exit(1)
        ngrok.set_auth_token(ngrok_token)
        tunnel = ngrok.connect(5000)
        public_url = tunnel.public_url
        print(f"🔗 Ngrok Tunnel URL: {public_url}")
    except Exception as e:
        print(f"❌ Failed to start ngrok: {e}")
        flask_process.terminate()
        sys.exit(1)

    webhook_url = f"{public_url}/webhook"
    print(f"📡 Setting Telegram Webhook to: {webhook_url}")

    telegram_api_url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/setWebhook"
    payload = {
        "url": webhook_url,
        "secret_token": config.WEBHOOK_SECRET_TOKEN
    }
    response = requests.post(telegram_api_url, data=payload)
    
    if response.status_code == 200 and response.json().get('ok'):
        print("✅ Webhook set successfully with secret_token security!")
        print("📱 YOU CAN NOW OPEN TELEGRAM AND SEND A MESSAGE TO YOUR BOT! 📱")
    else:
        print(f"❌ Failed to set webhook: {response.text}")

    try:
        # Keep the main thread alive so ngrok and flask stay up
        flask_process.wait()
    except KeyboardInterrupt:
        print("🛑 Shutting down...")
        flask_process.terminate()
        ngrok.kill()

if __name__ == "__main__":
    main()
