"""Leads a CLIENT receives — their own customers, not uallak's prospects.

## The distinction that matters

`core/lead_tracking.py` owns the `leads` table: uallak's OWN acquisition
funnel, one row per prospect who landed on our sales chat. This module owns
`client_leads`: people who contacted one of our CLIENTS. Same word, opposite
direction. CLAUDE.md calls mixing them up "a frequent and expensive
misreading" — the two modules deliberately share no code and no table.

## Where the rows come from

`capture_lead()` is the single write path, and `POST /api/leads/capture/{token}`
is its public front door: a form on the client's own website posts here with a
long-lived signed token that identifies the client. The token is HMAC-signed
with a derived secret (core/session.create_lead_capture_token) so it carries
the client_id itself — no lookup table, and a tampered token verifies as
nothing.

That endpoint is public and unauthenticated by necessity (it is embedded in a
public web page), which is the same exposure `/api/lead/start` already has.
Two bounded defences, since there is no rate limiting anywhere in this
codebase: every field is length-capped on the way in, and a client cannot
exceed MAX_LEADS_PER_DAY rows per day — past that the request still returns
200 (a form must never show an error to a real customer) but the row is
dropped and an alert fires once.

## Statuses

new -> contacted -> qualified -> won | lost. Stored, not derived: unlike
`leads.dropped_off`, these reflect a human decision the client made about a
real person, so there is nothing to compute them from.
"""
import os
import re
from datetime import datetime, timedelta, timezone

from core.agent_base import agent_alert, log_step

AGENT_NAME = "client_leads"

STATUSES = ("new", "contacted", "qualified", "won", "lost")

# Coarse channels the UI filters on. Anything else is stored verbatim and
# groups under "other" in the filter — an unknown source must never be
# rewritten into a known one (same honesty rule as lead_tracking's `direct`).
SOURCES = ("website_form", "whatsapp", "phone", "meta_lead_form", "manual", "unknown")

MAX_NAME_CHARS = 120
MAX_PHONE_CHARS = 40
MAX_EMAIL_CHARS = 200
MAX_MESSAGE_CHARS = 4000
MAX_SOURCE_DETAIL_CHARS = 500
MAX_NOTE_CHARS = 4000

# Per-client, per-day ceiling on captured rows. Generous for a real SMB and
# still a hard stop on a scripted flood through the public capture endpoint.
MAX_LEADS_PER_DAY = 500

_db_instance = None


def _db():
    global _db_instance
    if _db_instance is None:
        from supabase import create_client
        _db_instance = create_client(os.environ["SUPABASE_URL"],
                                     os.environ["SUPABASE_SERVICE_KEY"])
    return _db_instance


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clip(value, limit: int) -> str:
    return (str(value or "").strip())[:limit]


# ─── Phone / WhatsApp ─────────────────────────────────────────────────────────

def whatsapp_link(phone: str) -> str:
    """Israeli-first normalization to a wa.me link, mirroring
    whatsapp_service._chat_id and the admin dashboard's waLink() so all three
    agree on what '050-123-4567' means. Empty string when there is no usable
    number — callers must hide the button rather than render a dead link."""
    digits = re.sub(r"\D", "", phone or "")
    if not digits:
        return ""
    if digits.startswith("0"):
        digits = "972" + digits[1:]
    return f"https://wa.me/{digits}"


# ─── Write path ───────────────────────────────────────────────────────────────

def _insert_lead(client_id: int, base_row: dict, attribution: dict):
    """Insert with attribution, falling back to without.

    The attribution columns arrive in a migration
    (2026-08-02-client-leads-attribution.sql). If it hasn't been run, PostgREST
    rejects the whole insert for unknown columns — and losing a real customer
    enquiry because a migration is pending is not an acceptable trade. So the
    fallback drops attribution and keeps the LEAD, alerting once so the gap is
    visible rather than silent.

    The retry deliberately does not inspect the error message: PostgREST's
    schema-cache wording is not a contract, and any insert failure that a
    smaller row can survive is one worth surviving."""
    try:
        return _db().table("client_leads").insert({**base_row, **attribution}).execute()
    except Exception as e:
        result = _db().table("client_leads").insert(base_row).execute()
        agent_alert(AGENT_NAME, [
            f"client {client_id}: lead STORED but WITHOUT attribution ({e}). "
            f"Run migrations/2026-08-02-client-leads-attribution.sql — until then "
            f"organic-vs-paid is unknown for every lead captured."])
        return result


def _captured_today(client_id: int) -> int:
    since = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    rows = (_db().table("client_leads").select("id", count="exact")
            .eq("client_id", client_id).gte("created_at", since)
            .limit(1).execute())
    return rows.count or 0


def _attribution_fields(payload: dict) -> dict:
    """Where this visitor actually came from, classified.

    Reuses `core/lead_tracking` — its UTM/click-id constants AND its
    `classify_source()` verdict — rather than reimplementing attribution for
    the other direction of travel. The two TABLES never join (that boundary is
    absolute), but the two must agree on what "a paid Google click" means, and
    one classifier is how that stays true.

    Every value here came off a URL a stranger controls: truncated hard,
    never executed, never trusted beyond being a string."""
    from core import lead_tracking

    utm = {field: lead_tracking._clean(payload.get(field))
           for field in lead_tracking.UTM_FIELDS}
    click_ids = {param: lead_tracking._clean(payload.get(param), 500)
                 for param in lead_tracking.CLICK_ID_PLATFORMS}
    referrer = lead_tracking._clean(payload.get("referrer"), 500)
    landing_path = lead_tracking._clean(payload.get("landing_path"), 500)

    classified = lead_tracking.classify_source(utm, click_ids, referrer)
    return {
        **utm,
        # Only the click ids actually present — an empty map is honest, a map
        # of empty strings is noise.
        "click_ids": {param: value for param, value in click_ids.items() if value},
        "referrer": referrer,
        "landing_path": landing_path,
        "source_platform": classified["platform"],
        "source_confidence": classified["confidence"],
    }


def capture_lead(client_id: int, payload: dict, source: str = "website_form") -> dict:
    """THE write path. Returns {"stored": bool, "id": int|None, "reason": str}.

    Never raises for bad input: a customer filling a form on the client's site
    must not see an error because a field was too long or the daily cap was
    hit. Genuinely empty submissions (no name, phone, email or message) are
    the one thing rejected — they are bot noise, not leads."""
    name = _clip(payload.get("name"), MAX_NAME_CHARS)
    phone = _clip(payload.get("phone"), MAX_PHONE_CHARS)
    email = _clip(payload.get("email"), MAX_EMAIL_CHARS)
    message = _clip(payload.get("message"), MAX_MESSAGE_CHARS)

    if not (name or phone or email or message):
        return {"stored": False, "id": None, "reason": "empty"}

    if source not in SOURCES:
        source = "unknown"

    base_row = {
        "client_id": client_id,
        "name": name, "phone": phone, "email": email, "message": message,
        "source": source,
        "source_detail": _clip(payload.get("source_detail"), MAX_SOURCE_DETAIL_CHARS),
        "status": "new",
    }

    try:
        if _captured_today(client_id) >= MAX_LEADS_PER_DAY:
            agent_alert(AGENT_NAME, [
                f"client {client_id}: lead capture hit the daily cap "
                f"({MAX_LEADS_PER_DAY}) - further submissions today are being "
                f"DROPPED. Check for a form loop or a scripted flood."])
            return {"stored": False, "id": None, "reason": "daily_cap"}

        row = _insert_lead(client_id, base_row, _attribution_fields(payload))
    except Exception as e:
        # A failed capture loses a real customer enquiry - loud, not silent.
        agent_alert(AGENT_NAME,
                    [f"client {client_id}: lead capture FAILED ({source}): {e}"])
        return {"stored": False, "id": None, "reason": "error"}

    lead_id = (row.data or [{}])[0].get("id")
    log_step(AGENT_NAME, "capture", f"client {client_id}: lead {lead_id} via {source}")
    return {"stored": True, "id": lead_id, "reason": ""}


# ─── Read path ────────────────────────────────────────────────────────────────

def list_leads(client_id: int, status: str = "", source: str = "",
               query: str = "", days: int = 0, limit: int = 300) -> list:
    """This client's leads, newest first. Every filter is optional; the
    client_id equality is not — it is what makes this endpoint safe to expose
    to a session rather than an admin key."""
    q = (_db().table("client_leads").select("*")
         .eq("client_id", client_id)
         .order("created_at", desc=True)
         .limit(max(1, min(limit, 1000))))

    if status in STATUSES:
        q = q.eq("status", status)
    if source in SOURCES:
        q = q.eq("source", source)
    if days and days > 0:
        q = q.gte("created_at",
                  (datetime.now(timezone.utc) - timedelta(days=days)).isoformat())

    rows = q.execute().data or []

    # Free-text search is applied in Python rather than as a PostgREST `or`
    # filter: the term is user input, `or=` takes a comma-separated expression
    # string, and building that string from user input is an injection shape
    # worth avoiding for a list that is at most `limit` rows.
    term = (query or "").strip().lower()
    if term:
        rows = [r for r in rows if term in " ".join([
            r.get("name") or "", r.get("phone") or "", r.get("email") or "",
            r.get("message") or "", r.get("notes") or "",
            r.get("source_detail") or "",
        ]).lower()]

    for row in rows:
        row["whatsapp_link"] = whatsapp_link(row.get("phone"))
    return rows


def lead_stats(client_id: int) -> dict:
    """Counts per status plus a 30-day total, for the section header. Reads the
    same rows the list does rather than a second aggregate query — at SMB
    volume one read is cheaper than two round trips."""
    rows = (_db().table("client_leads").select("status,created_at")
            .eq("client_id", client_id).limit(5000).execute().data or [])
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    counts = {s: 0 for s in STATUSES}
    for row in rows:
        if row.get("status") in counts:
            counts[row["status"]] += 1
    return {
        "total": len(rows),
        "last_30_days": sum(1 for r in rows if (r.get("created_at") or "") >= since),
        "by_status": counts,
    }


# ─── Mutations (always scoped to the owning client) ───────────────────────────

def _update_own(client_id: int, lead_id: int, changes: dict) -> dict:
    """Every update filters on client_id AND id. A client can therefore never
    touch another client's lead even with a guessed id — the row simply does
    not match and .data comes back empty."""
    changes["updated_at"] = _now()
    result = (_db().table("client_leads").update(changes)
              .eq("id", lead_id).eq("client_id", client_id).execute())
    return (result.data or [{}])[0]


def set_status(client_id: int, lead_id: int, status: str) -> dict:
    if status not in STATUSES:
        raise ValueError(f"unknown status: {status}")
    return _update_own(client_id, lead_id, {"status": status})


def set_note(client_id: int, lead_id: int, note: str) -> dict:
    return _update_own(client_id, lead_id, {"notes": _clip(note, MAX_NOTE_CHARS)})


def delete_lead(client_id: int, lead_id: int) -> bool:
    result = (_db().table("client_leads").delete()
              .eq("id", lead_id).eq("client_id", client_id).execute())
    return bool(result.data)
