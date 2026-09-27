# Prompt log

## AI tools and models

| Tool | Model | Used for |
|---|---|---|
| Claude Code (Claude desktop app, Code tab) | Claude Opus 5.5 (`claude-opus-5-5`) | Writing the backend, frontend, tests, README, and deploy config; running tests; browser-testing the frontend; switching the backend from Anthropic to OpenAI |
| OpenAI API (at runtime) | `gpt-4.1-mini` (configurable via `OPENAI_MODEL`) | Answers visitors' questions in the deployed chatbot |

## Key prompts

### 1. Main build prompt (2026-09-26)

> I'm building "Ask Atin," a chatbot on my GitHub Pages portfolio (atinm12.github.io) for a web dev class assignment due Sun 9/27 at 8 PM. A visitor asks questions about me, and a Flask backend on Render sends them to the Claude API with a system prompt describing me. Build it, test it, and get it ready to deploy. Work step by step and check in with me at the STOP points.
>
> **Assignment requirements (graded):** Backend in its own public GitHub repo (ask-atin-backend), deployed on Render's free tier. At least one endpoint that accepts data and returns JSON. API key only in an environment variable; it must never be committed and never appear in frontend code. Frontend (HTML/CSS/JS) on GitHub Pages that calls the backend with fetch(), shows responses, and handles errors (backend down, bad input). README covering endpoints, frontend-backend communication, local setup (including env vars), and secrets handling. prompt_log.md with the AI tools, models, and key prompts. Functionality and clear frontend-backend communication matter more than UI polish.
>
> **Existing files:** review app.py, persona.md, README.md, prompt_log.md, ask-atin.html against the specs and fix gaps rather than rewriting. If persona.md is missing, ask me 5 short questions and write it from my answers. Never invent facts about me.
>
> **Backend spec (Flask, Python 3.10+):** POST /chat with {message (1-1000 chars), history?: [{role, content}]} → {reply}; forward only the last 10 valid history items, starting with a user turn. GET /health → {status: "ok"}. Every error is JSON {error} with the right status: 400, 404/405, 429 (15 requests per 60 seconds per IP, in memory), 500 (missing key), 502/503 (Anthropic errors); never crash. Model from ANTHROPIC_MODEL (default claude-haiku-4-5-20251001), max_tokens 400, system prompt from persona.md. flask-cors restricted to ALLOWED_ORIGINS (default: https://atinm12.github.io, http://localhost:5500, http://127.0.0.1:5500). python-dotenv; commit .env.example; .env gitignored. requirements.txt with gunicorn; start command gunicorn app:app; add render.yaml.
>
> **Frontend spec (ask-atin.html, one file):** const API_BASE with a comment on switching to localhost; chat bubbles; textarea (Enter sends, Shift+Enter newline); 3-4 suggested-question chips; "server online / waking up" status; sends {message, history} and appends both turns on success; error bubble for empty input (caught before any request), backend error, network failure, and a 90 s AbortController timeout; model output via textContent, never innerHTML; light and dark mode; "back to portfolio" link.
>
> **Testing:** pytest with Flask's test client and a mocked Anthropic client (health, empty, non-JSON, too long, 404, 405, missing key, success, history cleaning, CORS allowed vs. blocked, rate limit); run locally and curl /health, a valid /chat, and an empty /chat; one real /chat call if a key is in .env; serve the frontend with `python -m http.server 5500` and confirm a round-trip, plus the error bubble when the backend is stopped. Report exactly what passed and what couldn't be tested.
>
> **Security rules:** never print, log, or commit the key; before any commit run `git status`, make sure .env isn't staged, and grep staged files for "sk-ant"; before pushing, confirm API_BASE points to Render, not localhost.
>
> **STOP points:** ask before creating or pushing the GitHub repo, before adding the page to the portfolio repo (ask where it is first), and give a Render checklist instead of doing anything on Render.
>
> **When finished:** update prompt_log.md, and list what's left (Render deploy, README placeholder URLs, 2-minute demo video, Google form).

### 2. Persona source

Claude Code offered to ask 5 short questions about my background. My reply:

> Uh, yes, you create a repo and push it. And for the other part, for stop one, uh, for all the information, you can go to my portfolio website, which is on my GitHub. Does that work?

Claude Code read `index.html` from the `atinm12/atinm12.github.io` repo and wrote `persona.md` using only facts stated on the site. Anything the site doesn't cover (class year, GPA, etc.) is explicitly marked as unknown so the bot says "I don't know" instead of guessing.

### 3. Switching from Claude to OpenAI

> I did an open AI API key. Is that fine instead of Claude?

> change the code so that it works with OpenAI.

Nothing in the graded requirements names a provider, so the backend now uses the `openai` SDK (Chat Completions) instead of `anthropic`. The API contract (`/chat`, `/health`, error shapes) and the frontend didn't change.

### 4. Deploy and portfolio

> push. make sure to hide api keys that keep to be hidden

> when i ask a question, it says its missing the key.

> are all requirements met: (pasted the assignment's requirements)

- Claude Code copied `ask-atin.html` into my portfolio repo, added an "Ask Atin" card to the Projects section, and pushed only after scanning both repos (and their full git history) for API keys.
- I deployed on Render myself. The first chat failed with "missing its API key" because `OPENAI_API_KEY` wasn't set in Render's Environment tab; setting it and redeploying fixed it.
- Verified live on 2026-09-27: `/health` returned 200, a real `/chat` returned a correct answer, and an empty `/chat` returned a JSON 400.

## Main decisions made during the build

- **Guardrails in code, facts in `persona.md`.** `app.py` wraps `persona.md` with fixed instructions: answer only from the profile, say "I don't know" instead of guessing, decline off-topic requests, keep answers short and in plain text. `persona.md` stays pure facts that are easy to edit.
- **The API client is created lazily** (`get_client()`), so the app boots and `/health` works even without a key, and tests can swap in a mock with one line.
- **Error mapping:** connection errors, timeouts, and OpenAI 429/503 return **503** ("busy, try again"). OpenAI's 429 with code `insufficient_quota` (account out of credits) returns **502**, since retrying won't help. Any other OpenAI status error, or an empty reply, returns **502**. A catch-all handler turns any unexpected exception into a JSON 500, so the server never returns an HTML error page.
- **Rate limiter:** a sliding window (a deque of timestamps per IP) guarded by a lock, applied to `/chat` only so `/health` wake-up pings don't use up the quota. `ProxyFix(x_for=1)` makes Flask see the real client IP behind Render's proxy.
- **Extra abuse limits** beyond the spec: 64 KB request body cap (413), 4000 characters per history item.
- **`gunicorn.conf.py`** keeps the start command exactly `gunicorn app:app` while setting 1 worker (the in-memory rate limiter must be shared), 4 threads, a 120 s timeout, and binding to Render's `$PORT`. The OpenAI client uses a 30 s timeout with 1 retry, which stays under both gunicorn's timeout and the frontend's 90 s abort.
- **Local port 5001, not 5000:** on macOS, AirPlay Receiver listens on port 5000 and answered with a 403 after Flask stopped. That hid the "backend down" case during testing.
- **Frontend:** failed sends put the text back in the textarea so it can be resent; `fetch` `TypeError` is treated as a network failure and `AbortError` as a timeout; the status line updates to "unreachable" or "online" after each request.
- **Testing without a key:** the browser round-trip was run against the real `app.py` with only `get_client` stubbed, and the frontend was served from a scratch copy with `API_BASE` set to localhost, so the committed file keeps pointing at Render.
- **Anthropic to OpenAI switch:** the key and model env vars are now `OPENAI_API_KEY` and `OPENAI_MODEL`, and the system prompt goes in as a `system` message. The call uses `max_completion_tokens=400` (OpenAI's current parameter name). The default model is `gpt-4.1-mini`, not a GPT-5 mini: GPT-5 models are reasoning models, and hidden reasoning tokens can use up a 400-token budget and leave an empty answer.
