"""Short automatic retry for transient Supabase connection errors.

## Why here

There is no single shared DB helper — about twenty modules each build their
own Supabase client through a local `_db()`. Every one of them talks to
Supabase over `httpx`, so the retry lives at that one choke point:
`httpx.Client.send`, wrapped ONCE (`install()`, called at startup by
core/api_server.py) and active ONLY for requests to the SUPABASE_URL host.
Every other HTTP call in the app (Google, Meta, PayPal, Anthropic…) is
untouched.

## What is retried

Up to RETRIES (2) more attempts, after RETRY_DELAYS (0.2 s, 0.6 s), for the
"the pooled connection died" family seen in production
(control_room.list_overview -> admin_service.list_clients_admin):
httpx.RemoteProtocolError (incl. HTTP/2 ConnectionTerminated / "Server
disconnected without sending a response"), ReadError, WriteError, plus
ConnectError / ConnectTimeout / PoolTimeout.

## What is NOT retried (on purpose)

A POST (PostgREST insert / upsert / rpc) is retried only when the request
provably never left (ConnectError, ConnectTimeout, PoolTimeout). After a
RemoteProtocolError a POST may already have been applied, and retrying it
could insert a row twice — that error is raised as before. GET / HEAD and
PATCH / DELETE (same filter, same effect when repeated) are retried for the
whole family. HTTP error *responses* (4xx/5xx) are never retried — they are
answers, not dropped connections.
"""
import os
import time
from urllib.parse import urlsplit

import httpx

RETRIES = 2
RETRY_DELAYS = (0.2, 0.6)

_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
_DROPPED = (httpx.RemoteProtocolError, httpx.ReadError, httpx.WriteError)
_REPEATABLE = {"GET", "HEAD", "OPTIONS", "PATCH", "DELETE"}

_installed = False

# Checks run once per Supabase request, before the first attempt (never
# retried); a hook refuses a request by raising. core/deletion_guard
# registers the "client is being deleted" write guard here.
PRE_SEND_HOOKS = []


def _supabase_host() -> str:
    try:
        return (urlsplit(os.environ.get("SUPABASE_URL", "")).hostname or "").lower()
    except Exception:
        return ""


def _retryable(method: str, error: Exception) -> bool:
    if isinstance(error, _NOT_SENT):
        return True
    return isinstance(error, _DROPPED) and method.upper() in _REPEATABLE


def install():
    """Wrap httpx.Client.send once. Safe to call more than once."""
    global _installed
    if _installed:
        return
    original_send = httpx.Client.send

    def send(self, request, *args, **kwargs):
        host = _supabase_host()
        if not host or (request.url.host or "").lower() != host:
            return original_send(self, request, *args, **kwargs)
        for hook in PRE_SEND_HOOKS:
            hook(request)
        for attempt in range(RETRIES + 1):
            try:
                return original_send(self, request, *args, **kwargs)
            except Exception as e:
                if attempt >= RETRIES or not _retryable(request.method, e):
                    raise
                # Path only — never the query string (it can carry filter values)
                print(f"[db_retry] {type(e).__name__} on {request.method} {request.url.path} — "
                      f"retry {attempt + 1}/{RETRIES}")
                time.sleep(RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)])

    httpx.Client.send = send
    _installed = True
