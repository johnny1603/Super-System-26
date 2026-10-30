"""Refuses NEW rows for a client whose deletion has started.

## Why at the HTTP layer

About thirty call sites insert or update rows by client_id (activity, chat,
costs, suggestions, feedback, landing pages, leads, CRM sync…), spread over
twenty modules with their own `_db()`. Guarding each one by hand would miss
the next one somebody adds. Every one of them reaches Supabase through
`httpx.Client.send`, which core/db_retry already wraps for the Supabase host —
this module registers a pre-send check there (`install()`), so ONE rule covers
every writer, present and future:

  POST / PATCH whose JSON body carries `client_id` = a deleting client   refused
  PATCH filtered by `client_id=eq.N` / `in.(…N…)`                       refused
  POST / PATCH of app_settings "client_settings:<N>" (and the per-client
    token-health rows "gtm_token:<N>", "gads_token:<N>", "tiktok_grant:<N>",
    the PHP2Shell record "php_exposure:<N>", and "creative_tools:<N>")   refused
  PATCH of clients id=N, unless it only sets status='deleting'          refused
  DELETE / GET / HEAD                                                    allowed

A refused write raises ClientDeleting (the client id only, no other data).

## Which clients

`client_deletion` marks a client in two places when its deletion starts:
this process's set (exact, immediate) and its app_settings progress row
`client_deletion:<id>` (`deleting: true`, later `deleted: true` — a permanent
id-only tombstone, so a job still in flight when the clients row goes cannot
write an orphan row afterwards). Other Cloud Run instances read those rows,
cached for MARK_TTL seconds; the deletion's verification pass removes and
reports anything written inside that window. If that read fails the cache
keeps its last value — a database hiccup never blocks every write.
"""
import json
import threading
import time

MARK_TTL = 5
PROGRESS_PREFIX = "client_deletion:"
# app_settings keys that belong to ONE client ("<prefix><client_id>") —
# the same rows client_deletion._settings_keys clears
CLIENT_SETTING_PREFIXES = ("client_settings:", "gtm_token:", "gads_token:", "tiktok_grant:",
                           "php_exposure:", "creative_tools:")

_local = set()
_cache = {"at": 0.0, "ids": frozenset()}
_refreshing = threading.Lock()
_installed = False


class ClientDeleting(RuntimeError):
    pass


def mark(client_ids):
    _local.update(int(i) for i in client_ids)


def _remote_ids() -> frozenset:
    now = time.monotonic()
    if now - _cache["at"] < MARK_TTL or not _refreshing.acquire(blocking=False):
        return _cache["ids"]
    try:
        from agents.client_agent import _db
        rows = (_db().table("app_settings").select("key,value").like("key", f"{PROGRESS_PREFIX}%")
                .limit(10000).execute().data or [])
        ids = set()
        for r in rows:
            value = r.get("value") if isinstance(r.get("value"), dict) else {}
            if value.get("deleting") or value.get("deleted"):
                try:
                    ids.add(int(str(r["key"]).split(":", 1)[1]))
                except (TypeError, ValueError):
                    pass
        _cache["ids"] = frozenset(ids)
    except Exception as e:
        print(f"[deletion_guard] marker refresh failed ({type(e).__name__}) — using the last known list")
    finally:
        _cache["at"] = time.monotonic()
        _refreshing.release()
    return _cache["ids"]


def deleting_ids() -> set:
    return _local | _remote_ids()


def is_deleting(client_id) -> bool:
    try:
        return int(client_id) in deleting_ids()
    except (TypeError, ValueError):
        return False


def _filter_ids(value: str) -> set:
    """client ids named by a PostgREST filter: eq.N or in.(N,M)."""
    value = (value or "").strip()
    if value.startswith("eq."):
        parts = [value[3:]]
    elif value.startswith("in.(") and value.endswith(")"):
        parts = value[4:-1].split(",")
    else:
        return set()
    out = set()
    for p in parts:
        try:
            out.add(int(p.strip().strip('"')))
        except ValueError:
            pass
    return out


def check(request):
    """db_retry pre-send hook. Raises ClientDeleting for a refused write."""
    method = request.method.upper()
    if method not in ("POST", "PATCH"):
        return
    path = request.url.path or ""
    if not path.startswith("/rest/v1/") or path.startswith("/rest/v1/rpc/"):
        return
    ids = deleting_ids()
    if not ids:
        return
    table = path[len("/rest/v1/"):].strip("/")
    params = request.url.params
    try:
        body = json.loads(request.content or b"null")
    except Exception:
        body = None
    rows = body if isinstance(body, list) else [body] if isinstance(body, dict) else []

    hit = None
    for row in rows:
        try:
            if row.get("client_id") is not None and int(row["client_id"]) in ids:
                hit = int(row["client_id"])
        except (TypeError, ValueError, AttributeError):
            pass
        key = str(row.get("key") or "") if isinstance(row, dict) else ""
        if table == "app_settings" and key.startswith(CLIENT_SETTING_PREFIXES):
            try:
                if int(key.split(":", 1)[1]) in ids:
                    hit = int(key.split(":", 1)[1])
            except ValueError:
                pass
    if method == "PATCH":
        for cid in _filter_ids(params.get("client_id", "")) & ids:
            hit = cid
        if table == "clients":
            named = _filter_ids(params.get("id", "")) & ids
            only_mark = rows and all(r == {"status": "deleting"} for r in rows)
            if named and not only_mark:
                hit = next(iter(named))
        if table == "app_settings":
            key = params.get("key", "")
            if key.startswith(tuple("eq." + p for p in CLIENT_SETTING_PREFIXES)):
                try:
                    if int(key.split(":", 1)[1]) in ids:
                        hit = int(key.split(":", 1)[1])
                except ValueError:
                    pass
    if hit is not None:
        print(f"[deletion_guard] refused {method} {table} for client {hit} — deletion in progress")
        raise ClientDeleting(f"client {hit} is being deleted — write refused")


def install():
    """Register the pre-send check with core/db_retry. Safe to call twice."""
    global _installed
    if _installed:
        return
    from core import db_retry
    db_retry.PRE_SEND_HOOKS.append(check)
    _installed = True
