# Ask Atin: backend

A small Flask API behind the **Ask Atin** chatbot on my portfolio. Visitors type questions about me on a GitHub Pages page; this backend forwards them to the OpenAI API with a system prompt built from [`persona.md`](persona.md) and returns the answer as JSON.

- **Live backend:** https://ask-atin-backend.onrender.com  <!-- TODO: replace with the real Render URL -->
- **Frontend:** https://atinm12.github.io/ask-atin.html  <!-- TODO: confirm path once added to the portfolio -->
- **Stack:** Python 3.10+, Flask, flask-cors, OpenAI Python SDK, gunicorn, Render (free tier)

## Endpoints

All responses are JSON. Every error has the shape `{"error": "human-readable message"}`.

### `GET /health`

Liveness check. The frontend calls it on page load to wake the Render instance (free instances sleep after ~15 minutes idle).

- **Params:** none
- **Returns:** `200 {"status": "ok"}`

### `POST /chat`

Sends a visitor's question (and optionally the conversation so far) to OpenAI's Chat Completions API.

**Request body** (`Content-Type: application/json`):

| Field | Type | Required | Notes |
|---|---|---|---|
| `message` | string | yes | 1-1000 characters after trimming whitespace |
| `history` | array of `{role, content}` | no | `role` is `"user"` or `"assistant"`; `content` is a string |

History handling: invalid items (wrong role, missing or empty content, non-objects) are dropped, only the **last 10 valid items** are kept, and leading `assistant` items are removed so the conversation always starts with a user turn.

**Success:** `200 {"reply": "..."}`

**Errors:**

| Status | When |
|---|---|
| 400 | Body isn't JSON, `message` is missing, empty, or not a string, or longer than 1000 characters |
| 404 / 405 | Unknown path, or wrong HTTP method (e.g. `GET /chat`) |
| 413 | Request body over 64 KB |
| 429 | More than 15 `/chat` requests from one IP in 60 seconds (in-memory limiter) |
| 500 | Server is missing `OPENAI_API_KEY`, or an unexpected server error |
| 502 | OpenAI API returned an error (e.g. bad key, account out of credits, invalid request) or an empty reply |
| 503 | OpenAI API is unreachable, timed out, rate-limited, or overloaded |

Example:

```bash
curl -X POST https://ask-atin-backend.onrender.com/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What projects has Atin built?", "history": []}'
```

Model: `OPENAI_MODEL` env var (default `gpt-4.1-mini`), `max_completion_tokens` 400. The system prompt (fixed guardrails plus `persona.md`) is sent as the first `system` message.

## How the frontend talks to the backend

The frontend is one self-contained file, [`ask-atin.html`](ask-atin.html), hosted on GitHub Pages.

1. A `const API_BASE` at the top of the script holds the backend URL.
2. On load it calls `fetch(API_BASE + "/health")` and shows **Server online** or **Waking up the server...**.
3. When the visitor sends a message, it first rejects empty or too-long input locally (no request is sent), then does `fetch(API_BASE + "/chat", {method: "POST", body: JSON.stringify({message, history})})` with a 90-second `AbortController` timeout.
4. On success it shows `reply` in a chat bubble and appends both turns to its in-memory `history` array, which is sent with the next request so follow-up questions have context.
5. On failure it shows a red error bubble: the backend's `error` message, a "couldn't reach the server" message for network failures, or a timeout message.
6. Model output is inserted with `textContent` (never `innerHTML`), so a reply can't inject HTML or scripts.

Because the page (`https://atinm12.github.io`) and the API (`onrender.com`) are on different origins, the backend enables **CORS** via flask-cors, only for the origins in `ALLOWED_ORIGINS`. Browsers on other sites get no `Access-Control-Allow-Origin` header and are blocked.

## Run locally

Requires Python 3.10+.

```bash
git clone https://github.com/atinm12/ask-atin-backend.git
cd ask-atin-backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env        # then edit .env and paste your real key
python app.py               # serves on http://127.0.0.1:5001
```

> Port 5001 is used because macOS's AirPlay Receiver occupies port 5000. Override with `PORT=...`.

Environment variables (put them in `.env` locally; `python-dotenv` loads it):

| Variable | Required | Default |
|---|---|---|
| `OPENAI_API_KEY` | yes | none |
| `OPENAI_MODEL` | no | `gpt-4.1-mini` |
| `ALLOWED_ORIGINS` | no | `https://atinm12.github.io,http://localhost:5500,http://127.0.0.1:5500` (comma-separated) |
| `PORT` | no | `5001` locally (Render sets its own) |

Test the frontend against the local backend:

1. In `ask-atin.html`, temporarily set `const API_BASE = "http://127.0.0.1:5001";`
2. In a second terminal, from the folder containing `ask-atin.html`: `python -m http.server 5500`
3. Open http://localhost:5500/ask-atin.html
4. Set `API_BASE` back to the Render URL before committing.

Run the tests (the OpenAI client is mocked, so no key or network is needed):

```bash
pytest -q
```

## Deployment (Render)

`render.yaml` describes the service. Build command: `pip install -r requirements.txt`. Start command: `gunicorn app:app`. `gunicorn.conf.py` is picked up automatically: it binds to Render's `$PORT`, uses one worker (the rate limiter lives in memory), and sets a 120 s timeout so slow OpenAI calls aren't killed mid-request.

## How secrets are handled

- The OpenAI API key exists **only** in environment variables: in `.env` locally and in Render's **Environment** settings in production.
- `.env` is listed in `.gitignore` and has never been committed. Only `.env.example`, which has a placeholder value, is in the repo.
- The frontend never sees the key. It only talks to this backend, which adds the key server-side when calling OpenAI.
- The key is never logged. Error logs record only the exception type or HTTP status.
- Before each commit I run `git status` to confirm `.env` isn't staged, and I grep the staged files for `sk-` key patterns.
- Abuse limits: CORS allow-list, 1000-character message cap, 64 KB body cap, 10-item history cap, 400 `max_tokens`, and a per-IP rate limit of 15 requests per minute.

## Files

| File | Purpose |
|---|---|
| `app.py` | Flask app: routes, validation, rate limiting, CORS, OpenAI call |
| `persona.md` | Facts about me, used as the system prompt |
| `test_app.py` | pytest suite (OpenAI client mocked) |
| `ask-atin.html` | Frontend; a copy lives in my GitHub Pages repo |
| `requirements.txt` / `requirements-dev.txt` | Runtime and test dependencies |
| `render.yaml`, `gunicorn.conf.py` | Deployment config |
| `.env.example` | Template for local env vars |
| `prompt_log.md` | AI tools and prompts used to build this |
