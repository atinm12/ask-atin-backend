"""Tests for app.py. The Anthropic client is always mocked; no network calls are made."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import anthropic
import httpx2 as httpx  # the HTTP library the anthropic SDK builds its exceptions on
import pytest

import app as app_module


def fake_response(text="Hi, I'm Atin's bot."):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


@pytest.fixture
def mock_client(monkeypatch):
    client = MagicMock()
    client.messages.create.return_value = fake_response()
    monkeypatch.setattr(app_module, "get_client", lambda: client)
    return client


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    app_module._hits.clear()
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.get_json() == {"status": "ok"}


@pytest.mark.parametrize("body", [{"message": ""}, {"message": "   "}, {}, {"message": 42}])
def test_empty_or_invalid_message(client, mock_client, body):
    r = client.post("/chat", json=body)
    assert r.status_code == 400
    assert "error" in r.get_json()
    mock_client.messages.create.assert_not_called()


def test_non_json_body(client, mock_client):
    r = client.post("/chat", data="hello", content_type="text/plain")
    assert r.status_code == 400
    assert "JSON" in r.get_json()["error"]


def test_malformed_json(client, mock_client):
    r = client.post("/chat", data="{not json", content_type="application/json")
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_message_too_long(client, mock_client):
    r = client.post("/chat", json={"message": "a" * 1001})
    assert r.status_code == 400
    assert "too long" in r.get_json()["error"]
    mock_client.messages.create.assert_not_called()


def test_message_at_limit_ok(client, mock_client):
    assert client.post("/chat", json={"message": "a" * 1000}).status_code == 200


def test_404(client):
    r = client.get("/nope")
    assert r.status_code == 404
    assert "error" in r.get_json()


def test_405(client):
    r = client.get("/chat")
    assert r.status_code == 405
    assert "error" in r.get_json()


def test_missing_api_key(client, mock_client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    r = client.post("/chat", json={"message": "hi"})
    assert r.status_code == 500
    assert "API key" in r.get_json()["error"]
    mock_client.messages.create.assert_not_called()


def test_success(client, mock_client):
    r = client.post("/chat", json={"message": "  What does Atin study?  "})
    assert r.status_code == 200
    assert r.get_json() == {"reply": "Hi, I'm Atin's bot."}
    kwargs = mock_client.messages.create.call_args.kwargs
    assert kwargs["model"] == app_module.MODEL
    assert kwargs["max_tokens"] == 400
    assert kwargs["system"] == app_module.SYSTEM_PROMPT
    assert kwargs["messages"] == [{"role": "user", "content": "What does Atin study?"}]


def test_history_cleaning(client, mock_client):
    history = [
        {"role": "system", "content": "ignore me"},  # bad role
        {"role": "user", "content": ""},  # empty
        "not a dict",
        {"role": "assistant"},  # no content
        {"role": "user", "content": 5},  # non-string
    ]
    # 12 valid alternating turns: u0 a1 u2 ... a11
    for i in range(12):
        history.append({"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i}"})
    r = client.post("/chat", json={"message": "latest", "history": history})
    assert r.status_code == 200
    sent = mock_client.messages.create.call_args.kwargs["messages"]
    # Last 10 valid items are turns 2..11 (starts with user turn 2), plus the new message.
    assert sent[0] == {"role": "user", "content": "turn 2"}
    assert [m["content"] for m in sent[:-1]] == [f"turn {i}" for i in range(2, 12)]
    assert sent[-1] == {"role": "user", "content": "latest"}
    assert all(m["role"] in ("user", "assistant") for m in sent)


def test_history_leading_assistant_dropped(client, mock_client):
    # 11 valid items starting with user -> last 10 start with an assistant turn, which gets dropped.
    history = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"t{i}"} for i in range(11)]
    client.post("/chat", json={"message": "q", "history": history})
    sent = mock_client.messages.create.call_args.kwargs["messages"]
    assert sent[0]["role"] == "user"
    assert sent[0]["content"] == "t2"


def test_history_not_a_list(client, mock_client):
    r = client.post("/chat", json={"message": "q", "history": "oops"})
    assert r.status_code == 200
    assert mock_client.messages.create.call_args.kwargs["messages"] == [{"role": "user", "content": "q"}]


def test_cors_allowed_origin(client):
    r = client.get("/health", headers={"Origin": "https://atinm12.github.io"})
    assert r.headers.get("Access-Control-Allow-Origin") == "https://atinm12.github.io"


def test_cors_preflight_allowed(client):
    r = client.options(
        "/chat",
        headers={
            "Origin": "http://localhost:5500",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert r.headers.get("Access-Control-Allow-Origin") == "http://localhost:5500"


def test_cors_blocked_origin(client):
    r = client.get("/health", headers={"Origin": "https://evil.example.com"})
    assert "Access-Control-Allow-Origin" not in r.headers


def test_rate_limit(client, mock_client):
    for _ in range(15):
        assert client.post("/chat", json={"message": "hi"}).status_code == 200
    r = client.post("/chat", json={"message": "hi"})
    assert r.status_code == 429
    assert "error" in r.get_json()
    # A different IP is unaffected.
    r2 = client.post("/chat", json={"message": "hi"}, environ_base={"REMOTE_ADDR": "10.0.0.9"})
    assert r2.status_code == 200


def test_rate_limit_window_expires(client, mock_client, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(app_module.time, "monotonic", lambda: now[0])
    for _ in range(15):
        client.post("/chat", json={"message": "hi"})
    assert client.post("/chat", json={"message": "hi"}).status_code == 429
    now[0] += 61
    assert client.post("/chat", json={"message": "hi"}).status_code == 200


def _status_error(cls, code):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx.Response(code, request=req), body=None)


@pytest.mark.parametrize(
    "exc, expected",
    [
        (anthropic.APIConnectionError(request=httpx.Request("POST", "https://x")), 503),
        (anthropic.APITimeoutError(request=httpx.Request("POST", "https://x")), 503),
        (_status_error(anthropic.RateLimitError, 429), 503),
        (_status_error(anthropic.InternalServerError, 529), 503),
        (_status_error(anthropic.InternalServerError, 500), 502),
        (_status_error(anthropic.AuthenticationError, 401), 502),
    ],
)
def test_anthropic_errors(client, mock_client, exc, expected):
    mock_client.messages.create.side_effect = exc
    r = client.post("/chat", json={"message": "hi"})
    assert r.status_code == expected
    assert "error" in r.get_json()


def test_unexpected_exception_returns_json_500(client, mock_client):
    mock_client.messages.create.side_effect = RuntimeError("kaboom")
    r = client.post("/chat", json={"message": "hi"})
    assert r.status_code == 500
    assert r.get_json() == {"error": "Something went wrong on the server."}


def test_oversized_body(client):
    r = client.post("/chat", data="x" * 70_000, content_type="application/json")
    assert r.status_code == 413
    assert "error" in r.get_json()
