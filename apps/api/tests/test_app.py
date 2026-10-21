from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pricing import calculate_token_cost


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_health_and_public_config(client: TestClient):
    health = client.get("/health/ready")
    config = client.get("/api/v1/public/config")

    assert health.status_code == 200
    assert health.json()["knowledge"] == "ready"
    assert config.status_code == 200
    assert config.json()["demo"] is True


def test_embedded_session_uses_cross_site_cookie(client: TestClient):
    client.cookies.clear()
    response = client.get("/api/v1/session?embed=1")

    assert response.status_code == 200
    assert "samesite=none" in response.headers["set-cookie"].casefold()


def test_openai_token_pricing():
    assert calculate_token_cost("gpt-6-luna", 1_000_000, 1_000_000, 100_000) == pytest.approx(0.591)
    assert calculate_token_cost("text-embedding-3-small", 1_000_000) == pytest.approx(0.02)
    assert calculate_token_cost("unknown-model", 1000, 1000) is None


def test_session_chat_and_history(client: TestClient):
    session = client.get("/api/v1/session")
    conversation_id = session.json()["conversation_id"]
    client_message_id = str(uuid.uuid4())

    accepted = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers={"Idempotency-Key": client_message_id},
        json={"question": "unused", "text": "Кто может участвовать?", "client_message_id": client_message_id},
    )
    status = client.get(f"/api/v1/chat-requests/{accepted.json()['request_id']}")
    history = client.get(f"/api/v1/conversations/{conversation_id}/messages")

    assert accepted.status_code == 202
    assert status.status_code == 200
    assert status.json()["status"] == "completed"
    assert status.json()["assistant_message"]["sources"]
    assert len(history.json()["items"]) >= 2


@pytest.mark.parametrize(
    ("question", "expected_fragment", "expects_sources"),
    [
        ("Может ли подать заявку самозанятый?", "договору гпх", True),
        ("Как ты работаешь?", "баз", False),
    ],
)
def test_chat_answers_known_and_help_questions(
    client: TestClient,
    question: str,
    expected_fragment: str,
    expects_sources: bool,
):
    session = client.get("/api/v1/session")
    conversation_id = session.json()["conversation_id"]
    client_message_id = str(uuid.uuid4())
    accepted = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers={"Idempotency-Key": client_message_id},
        json={"question": question, "text": question, "client_message_id": client_message_id},
    )
    status = client.get(f"/api/v1/chat-requests/{accepted.json()['request_id']}")

    assert status.status_code == 200
    assert status.json()["status"] == "completed"
    assistant = status.json()["assistant_message"]
    assert assistant["result_status"] == "answered"
    assert expected_fragment in assistant["text"].casefold()
    assert bool(assistant["sources"]) is expects_sources


def test_admin_login_usage_and_faqs(client: TestClient):
    login = client.post(
        "/api/v1/admin/auth/login",
        json={"username": "admin", "password": "konkursadmin"},
    )
    csrf = login.json()["csrf_token"]
    usage = client.get("/api/v1/admin/usage/summary")
    faqs = client.get("/api/v1/admin/faqs")

    assert login.status_code == 200
    assert usage.status_code == 200
    assert usage.json()["unknown_count"] == 0
    assert float(usage.json()["known_cost"]) >= 0
    assert "embedding_cost" in usage.json()
    assert faqs.status_code == 200
    assert len(faqs.json()["items"]) >= 3

    created = client.post(
        "/api/v1/admin/faqs",
        headers={"X-CSRF-Token": csrf},
        json={
            "question": "Тестовый вопрос?",
            "answer": "Тестовый ответ для проверки редактора.",
            "category": "Тест",
            "season": "2025-2026",
            "featured": False,
        },
    )
    assert created.status_code == 201


def test_layered_knowledge_and_editable_escalation(client: TestClient):
    login = client.post(
        "/api/v1/admin/auth/login",
        json={"username": "admin", "password": "konkursadmin"},
    )
    csrf = login.json()["csrf_token"]

    qa = client.get("/api/v1/admin/knowledge?layer=qa")
    general = client.get("/api/v1/admin/knowledge?layer=general")
    general_text = client.get("/api/v1/admin/knowledge/general-text")
    escalation = client.get("/api/v1/admin/knowledge/settings/escalation")

    assert qa.status_code == 200
    assert qa.json()["count"] > 0
    assert qa.json()["items"][0]["title"] == "Привет, как дела"
    assert qa.json()["items"][1]["title"].startswith("О чем конкурс")
    assert general.status_code == 200
    assert general.json()["count"] > 0
    assert general_text.status_code == 200
    assert len(general_text.json()["value"]) > 100
    assert "creativemuseum@futuretime.ru" in escalation.json()["value"]

    created_qa = client.post(
        "/api/v1/admin/knowledge",
        headers={"X-CSRF-Token": csrf},
        json={
            "title": "Временный вопрос",
            "content": "Временный ответ для проверки удаления.",
            "layer": "qa",
            "section": "Тест",
            "season": "2025-2026",
        },
    )
    assert created_qa.status_code == 201
    deleted_qa = client.delete(
        f"/api/v1/admin/knowledge/{created_qa.json()['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert deleted_qa.status_code == 200
    assert deleted_qa.json()["deleted"] is True

    saved_general = client.put(
        "/api/v1/admin/knowledge/general-text",
        headers={"X-CSRF-Token": csrf},
        json={
            "value": general_text.json()["value"],
            "expected_revision": general_text.json()["revision"],
        },
    )
    assert saved_general.status_code == 200
    assert saved_general.json()["revision"] == general_text.json()["revision"] + 1
    assert saved_general.json()["chunk_count"] > 0

    saved = client.put(
        "/api/v1/admin/knowledge/settings/escalation",
        headers={"X-CSRF-Token": csrf},
        json={
            "value": escalation.json()["value"],
            "expected_revision": escalation.json()["revision"],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["revision"] == escalation.json()["revision"] + 1
