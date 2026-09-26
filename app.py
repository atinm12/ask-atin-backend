"""Ask Atin: a small Flask backend that answers questions about Atin via the Claude API."""

import logging
import os
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()  # reads .env locally; on Render the env vars come from the dashboard

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("ask-atin")

# ---- Configuration ---------------------------------------------------------

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
MAX_TOKENS = 400
MAX_MESSAGE_CHARS = 1000
MAX_HISTORY_ITEMS = 10
MAX_HISTORY_CONTENT_CHARS = 4000  # a stored assistant reply can be longer than a user message
RATE_LIMIT_REQUESTS = 15
RATE_LIMIT_WINDOW = 60  # seconds

DEFAULT_ORIGINS = "https://atinm12.github.io,http://localhost:5500,http://127.0.0.1:5500"
ALLOWED_ORIGINS = [
    o.strip() for o in os.getenv("ALLOWED_ORIGINS", DEFAULT_ORIGINS).split(",") if o.strip()
]

BASE_DIR = Path(__file__).resolve().parent
GUARDRAILS = (
    "You are \"Ask Atin\", a chatbot on Atin's portfolio website. Visitors ask you about Atin. "
    "Answer only from the profile below. If the profile doesn't cover something, say you don't "
    "know and suggest contacting Atin directly; never make up facts. Politely decline requests "
    "unrelated to Atin (homework, code generation, general trivia, etc.). Keep answers short "
    "(2-4 sentences), friendly, and in plain text without Markdown."
)


def load_system_prompt() -> str:
    persona_path = BASE_DIR / "persona.md"
    try:
        persona = persona_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        log.warning("persona.md not found; the bot will know nothing about Atin")
        persona = "(No profile provided.)"
    return f"{GUARDRAILS}\n\n## Profile\n\n{persona}"


SYSTEM_PROMPT = load_system_prompt()

# ---- App setup -------------------------------------------------------------

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024  # reject huge bodies before parsing them
# Render sits behind one proxy; trust its X-Forwarded-For so rate limiting sees the real client IP.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)
CORS(app, origins=ALLOWED_ORIGINS)

_client = None


def get_client():
    """Build the Anthropic client lazily so the app still boots (and /health works) without a key."""
    global _client
    if _client is None:
        # Timeouts stay well under the frontend's 90 s abort and gunicorn's worker timeout.
        _client = anthropic.Anthropic(
            api_key=os.environ["ANTHROPIC_API_KEY"], timeout=30.0, max_retries=1
        )
    return _client


# ---- Rate limiting (in memory, per IP, sliding window) ---------------------

_hits = defaultdict(deque)
_hits_lock = threading.Lock()


def rate_limited(ip: str) -> bool:
    now = time.monotonic()
    with _hits_lock:
        q = _hits[ip]
        while q and now - q[0] >= RATE_LIMIT_WINDOW:
            q.popleft()
        if len(q) >= RATE_LIMIT_REQUESTS:
            return True
        q.append(now)
        # Drop idle IPs occasionally so the dict can't grow forever.
        if len(_hits) > 10_000:
            for key in [k for k, v in _hits.items() if not v]:
                del _hits[key]
        return False


# ---- Helpers ---------------------------------------------------------------


def error(message: str, status: int):
    return jsonify({"error": message}), status


def clean_history(raw) -> list:
    """Keep only well-formed turns, the last MAX_HISTORY_ITEMS of them, starting with a user turn."""
    if not isinstance(raw, list):
        return []
    valid = [
        {"role": item["role"], "content": item["content"].strip()[:MAX_HISTORY_CONTENT_CHARS]}
        for item in raw
        if isinstance(item, dict)
        and item.get("role") in ("user", "assistant")
        and isinstance(item.get("content"), str)
        and item["content"].strip()
    ]
    trimmed = valid[-MAX_HISTORY_ITEMS:]
    while trimmed and trimmed[0]["role"] != "user":
        trimmed.pop(0)
    return trimmed


# ---- Routes ----------------------------------------------------------------


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/chat")
def chat():
    if rate_limited(request.remote_addr or "unknown"):
        return error("Too many requests. Please wait a minute and try again.", 429)

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error("Request body must be JSON like {\"message\": \"...\"}.", 400)

    message = data.get("message")
    if not isinstance(message, str) or not message.strip():
        return error("Please enter a message.", 400)
    message = message.strip()
    if len(message) > MAX_MESSAGE_CHARS:
        return error(f"Message is too long (max {MAX_MESSAGE_CHARS} characters).", 400)

    if not os.getenv("ANTHROPIC_API_KEY"):
        log.error("ANTHROPIC_API_KEY is not set")
        return error("The server is missing its API key. Please try again later.", 500)

    messages = clean_history(data.get("history")) + [{"role": "user", "content": message}]

    try:
        response = get_client().messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
    except (anthropic.APIConnectionError, anthropic.RateLimitError) as e:
        # APITimeoutError is a subclass of APIConnectionError.
        log.warning("Anthropic unavailable: %s", type(e).__name__)
        return error("The AI service is busy or unreachable. Please try again shortly.", 503)
    except anthropic.APIStatusError as e:
        log.error("Anthropic API error %s", e.status_code)
        if e.status_code in (503, 529):
            return error("The AI service is overloaded. Please try again shortly.", 503)
        return error("The AI service returned an error. Please try again later.", 502)

    reply = "".join(block.text for block in response.content if block.type == "text").strip()
    if not reply:
        return error("The AI service returned an empty reply. Please try again.", 502)
    return jsonify({"reply": reply})


# ---- Error handlers: every error is JSON -----------------------------------


@app.errorhandler(HTTPException)
def handle_http_error(e):
    messages = {
        404: "Not found. Available endpoints: GET /health, POST /chat.",
        405: "Method not allowed for this endpoint.",
        413: "Request body is too large.",
    }
    return error(messages.get(e.code, e.description or e.name), e.code)


@app.errorhandler(Exception)
def handle_unexpected_error(e):
    log.exception("Unhandled error")
    return error("Something went wrong on the server.", 500)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5001")), debug=False)
