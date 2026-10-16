import os
import pytest
from webhook import app
import config

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client

def test_webhook_rejects_missing_secret_token(client):
    response = client.post("/webhook", json={"message": {"text": "hello"}})
    assert response.status_code == 403
    assert response.get_json() == {"error": "Forbidden"}

def test_webhook_rejects_invalid_secret_token(client):
    headers = {"X-Telegram-Bot-Api-Secret-Token": "wrong_secret_token"}
    response = client.post("/webhook", json={"message": {"text": "hello"}}, headers=headers)
    assert response.status_code == 403
    assert response.get_json() == {"error": "Forbidden"}

def test_webhook_accepts_valid_secret_token(client):
    headers = {"X-Telegram-Bot-Api-Secret-Token": config.WEBHOOK_SECRET_TOKEN}
    response = client.post("/webhook", json={}, headers=headers)
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}

def test_webhook_ignores_unauthorized_chat_id(client):
    headers = {"X-Telegram-Bot-Api-Secret-Token": config.WEBHOOK_SECRET_TOKEN}
    payload = {
        "message": {
            "chat": {"id": "999999999_fake_chat_id"},
            "text": "digest"
        }
    }
    response = client.post("/webhook", json=payload, headers=headers)
    assert response.status_code == 200
    assert response.get_json() == {"status": "ignored"}

def test_healthz_endpoint(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    data = response.get_json()
    assert data["ok"] is True
    assert data["db"] is True
