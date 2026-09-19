"""API contract tests, against a TestClient with the engine stubbed offline."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

API_KEY = "test-key-not-a-secret"
AUTH = {"X-API-Key": API_KEY}


@pytest.fixture
def client(store, llm, monkeypatch):
    """Boot the app without the start-up indexing path."""
    import api_app
    from src.rag.engine import RAGEngine

    monkeypatch.setattr(api_app, "build_knowledge_base", lambda **kwargs: store)
    monkeypatch.setattr(api_app, "_start_background_indexing", lambda s: None)
    monkeypatch.setattr(api_app, "RAGEngine", lambda **kwargs: RAGEngine(store=store, llm=llm))

    with TestClient(api_app.app) as test_client:
        yield test_client


class TestPublicRoutes:
    def test_root(self, client):
        assert client.get("/").status_code == 200

    def test_health_reports_domain_count(self, client):
        body = client.get("/health").json()
        assert body["status"] == "healthy"
        assert body["domains"] == 2
        assert body["demo_mode"] is True

    def test_health_needs_no_auth(self, client):
        assert client.get("/health").status_code == 200


class TestAuthentication:
    def test_a_protected_get_rejects_a_missing_key(self, client):
        assert client.get("/domains").status_code == 401

    @pytest.mark.parametrize("path", ["/query", "/v1/chat/completions"])
    def test_a_protected_post_rejects_a_missing_key(self, client, path):
        assert client.post(path, json={"question": "x", "messages": []}).status_code == 401

    def test_a_wrong_key_is_rejected(self, client):
        assert client.get("/domains", headers={"X-API-Key": "wrong"}).status_code == 401

    def test_the_right_key_is_accepted(self, client):
        assert client.get("/domains", headers=AUTH).status_code == 200


class TestNativeApi:
    def test_domains(self, client):
        body = client.get("/domains", headers=AUTH).json()
        assert body["count"] == 2
        assert set(body["domains"]) == {"order_service", "payment_service"}

    def test_query_returns_an_answer_and_citations(self, client):
        body = client.post(
            "/query",
            headers=AUTH,
            json={"question": "How long is inventory reserved?", "domains": ["order_service"]},
        ).json()
        assert "thirty minutes" in body["answer"]
        assert body["citations"]
        assert body["response_time"] >= 0

    def test_an_empty_question_is_rejected(self, client):
        assert client.post("/query", headers=AUTH, json={"question": ""}).status_code == 422

    def test_an_oversized_question_is_rejected(self, client):
        response = client.post("/query", headers=AUTH, json={"question": "x" * 5000})
        assert response.status_code == 422

    def test_streaming_emits_ndjson_and_terminates(self, client):
        response = client.post(
            "/query/stream",
            headers=AUTH,
            json={"question": "inventory reservation", "domains": ["order_service"]},
        )
        assert response.status_code == 200
        lines = [json.loads(line) for line in response.text.strip().splitlines() if line]
        assert lines[-1]["done"] is True
        assert "".join(line.get("chunk", "") for line in lines).strip()

    def test_streaming_rejects_an_unknown_domain(self, client):
        response = client.post("/query/stream", headers=AUTH, json={"question": "x", "domains": ["nope"]})
        assert response.status_code == 400
        assert "nope" in response.json()["detail"]

    def test_repository_ingestion_is_disabled_by_default(self, client):
        response = client.post("/ingest/repository", headers=AUTH, json={"repo_path": "/etc"})
        assert response.status_code == 403
        assert "REPO_INGEST_ROOT" in response.json()["detail"]


class TestOpenAICompatibility:
    def test_models(self, client):
        body = client.get("/v1/models").json()
        assert body["object"] == "list"
        assert body["data"][0]["id"]

    def test_chat_completion_shape(self, client):
        body = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"messages": [{"role": "user", "content": "How long is inventory reserved?"}]},
        ).json()
        assert body["object"] == "chat.completion"
        assert body["choices"][0]["message"]["role"] == "assistant"
        assert body["choices"][0]["finish_reason"] == "stop"
        assert body["usage"]["total_tokens"] > 0

    def test_the_domain_prefix_scopes_the_query(self, client):
        body = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"messages": [{"role": "user", "content": "[DOMAIN:payment_service] what is a capture?"}]},
        ).json()
        assert body["choices"][0]["message"]["content"]

    def test_a_request_with_no_user_message_is_rejected(self, client):
        response = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"messages": [{"role": "system", "content": "hello"}]},
        )
        assert response.status_code == 400

    def test_streaming_emits_sse_and_terminates(self, client):
        response = client.post(
            "/v1/chat/completions",
            headers=AUTH,
            json={"messages": [{"role": "user", "content": "inventory"}], "stream": True},
        )
        assert response.status_code == 200
        assert response.text.rstrip().endswith("data: [DONE]")
        assert '"chat.completion.chunk"' in response.text


class TestSecurityHeaders:
    def test_headers_are_set(self, client):
        headers = client.get("/health").headers
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["Referrer-Policy"] == "no-referrer"
