# Loaded automatically by `gunicorn app:app`.
import os

bind = f"0.0.0.0:{os.getenv('PORT', '10000')}"
workers = 1  # the rate limiter is in memory, so keep a single process
threads = 4
timeout = 120  # longer than the Anthropic client's worst case (30 s x 2 attempts)
