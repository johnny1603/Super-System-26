"""Lead lifecycle + traffic-source attribution for the sales chat.

Before this existed a `leads` row was only created when a proposal finished
building, which made three things structurally impossible: knowing where a
prospect came from, knowing when they first made contact, and knowing about
anyone who left mid-chat. A lead row is now opened the moment someone lands on
the sales chat and updated as the conversation progresses.

Two deliberate design choices worth not undoing:

1. **`lead_id` is a random token, not the table's sequential `id`.** The
   chat is public and unauthenticated, so the browser holds this key and posts
   messages against it. A guessable key would let anyone append to (or read)
   a stranger's transcript.

2. **`dropped_off` is DERIVED at read time, never stored.** A stored status
   would need a scheduled job to flip it, and would then be wrong the moment a
   prospect came back the next day. Deriving it from `last_activity_at` means
   returning prospects silently go back to `in_progress` with no job at all.

Honesty rule, inherited from admin_service: never invent attribution. An
unknown source is reported as `direct` with `confidence='none'`, not guessed
at from weak signals.
"""
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

# A sales chat idle this long is over. Short on purpose: this drives the CRM's
# "who do I follow up with" signal, and a day-old threshold makes it useless.
DROP_OFF_HOURS = 6

# The chat is unauthenticated, so per-lead growth has to be bounded by
# something. A real conversation is well under 100 messages.
MAX_MESSAGES_PER_LEAD = 400
MAX_MESSAGE_CHARS = 8000

# What actually lives in leads.status. `dropped_off` is deliberately absent —
# see the module docstring.
STORED_STATUSES = ("in_progress", "converted", "declined")

# Click-id parameter -> the platform whose ad set it. Presence of one of these
# is the strongest signal available: it means a real paid click happened,
# whatever the utm_* params claim (and they can be wrong — anyone can type
# utm_source=google into a URL).
CLICK_ID_PLATFORMS = {
    "gclid": "google_ads",
    "gbraid": "google_ads",   # iOS, app-to-web
    "wbraid": "google_ads",   # iOS, web-to-web
    "fbclid": "meta",
    "ttclid": "tiktok",
    "msclkid": "microsoft_ads",
}

UTM_SOURCE_PLATFORMS = {
    "google": "google_ads", "googleads": "google_ads", "google_ads": "google_ads",
    "adwords": "google_ads",
    "facebook": "meta", "fb": "meta", "instagram": "meta", "ig": "meta", "meta": "meta",
    "tiktok": "tiktok", "tiktokads": "tiktok", "tiktok_ads": "tiktok",
}

PAID_MEDIUMS = {"cpc", "ppc", "paid", "paidsocial", "paid_social", "cpm",
                "display", "retargeting", "remarketing"}

SEARCH_ENGINE_HOSTS = ("google.", "bing.", "duckduckgo.", "yahoo.", "ecosia.",
                       "yandex.", "search.brave.")

# Our own hosts — a referrer from these is an internal hop (marketing site ->
# app), not a third-party referral.
OWN_HOSTS = ("uallak.com", "instawp.xyz", "run.app", "localhost")

UTM_FIELDS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")

# ─── Outbound tagging: what OUR ad agents stamp on the landing URLs they ship ─
#
# The read side of attribution is only ever as good as the write side. A click
# id tells us "this was a Google click"; only these templates tell us WHICH
# campaign and WHICH ad — because fbclid/ttclid are not resolvable to a
# campaign through any API, and gclid resolution needs a separate report call.
# Putting the answer in the URL at click time sidesteps all of that.
#
# The braces are expanded by the ad platform at click time, never by us:
# {campaignid}/{creative}/{keyword} are Google ValueTrack; {{campaign.name}},
# {{ad.name}}, {{site_source_name}} are Meta's dynamic URL parameters.
#
# IMPORTANT: these apply from campaign CREATION onward. Campaigns that already
# exist are not retrofitted — that would mean mutating live client campaigns,
# which is a human decision, not something a tracking change should do quietly.
GOOGLE_ADS_FINAL_URL_SUFFIX = (
    "utm_source=google&utm_medium=cpc"
    "&utm_campaign={campaignid}&utm_content={creative}&utm_term={keyword}"
)

# site_source_name resolves to fb / ig / an / msg, so an Instagram placement
# reads as Instagram rather than being flattened into "facebook".
META_URL_TAGS = (
    "utm_source={{site_source_name}}&utm_medium=paid_social"
    "&utm_campaign={{campaign.name}}&utm_content={{ad.name}}&utm_term={{adset.name}}"
)

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


def _host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except Exception:
        return ""


def _clean(value, limit: int = 300) -> str:
    """Everything here arrives from a URL a stranger controls — truncate hard
    and keep it a string."""
    return str(value or "").strip()[:limit]


def classify_source(utm: dict, click_ids: dict, referrer: str) -> dict:
    """Decide which platform sent this visitor, and say how confidently.

    Returns `platform` (google_ads | meta | tiktok | microsoft_ads | organic |
    referral | direct), `detail` (a human-readable reason) and `confidence`:

      - `click_id` — a real paid click, the platform stamped its own id
      - `utm`      — a tracked link says so, and we're trusting it
      - `referrer` — inferred from where the browser came from
      - `none`     — nothing to go on; reported as `direct`, not guessed
    """
    source = (utm.get("utm_source") or "").strip().lower()
    medium = (utm.get("utm_medium") or "").strip().lower()

    for param, platform in CLICK_ID_PLATFORMS.items():
        if click_ids.get(param):
            return {"platform": platform, "confidence": "click_id",
                    "detail": f"{param} present on the landing URL"}

    if source in UTM_SOURCE_PLATFORMS and medium in PAID_MEDIUMS:
        return {"platform": UTM_SOURCE_PLATFORMS[source], "confidence": "utm",
                "detail": f"utm_source={source}, utm_medium={medium} (no click id)"}

    if source or medium:
        # A tracked link that isn't a recognised paid combination — a
        # newsletter, a partner link, an organic social post we tagged.
        return {"platform": "referral", "confidence": "utm",
                "detail": f"tagged link: utm_source={source or '—'}, utm_medium={medium or '—'}"}

    host = _host_of(referrer)
    if host and not any(host == own or host.endswith("." + own) or own in host
                        for own in OWN_HOSTS):
        if any(engine in host for engine in SEARCH_ENGINE_HOSTS):
            return {"platform": "organic", "confidence": "referrer",
                    "detail": f"search referrer: {host}"}
        return {"platform": "referral", "confidence": "referrer",
                "detail": f"referrer: {host}"}

    return {"platform": "direct", "confidence": "none",
            "detail": "no campaign parameters and no external referrer"}


def start_lead(attribution: dict, language: str = "he") -> dict:
    """Open a lead the moment someone lands on the sales chat.

    `attribution` is whatever the browser scraped off its own URL — untrusted
    by definition, so every field is truncated and nothing is executed.
    """
    utm = {field: _clean(attribution.get(field)) for field in UTM_FIELDS}
    click_ids = {param: _clean(attribution.get(param), 500) for param in CLICK_ID_PLATFORMS}
    referrer = _clean(attribution.get("referrer"), 500)
    landing_path = _clean(attribution.get("landing_path"), 500)

    classified = classify_source(utm, click_ids, referrer)
    present_click_ids = {param: value for param, value in click_ids.items() if value}

    lead_id = secrets.token_urlsafe(24)
    row = {
        "lead_id": lead_id,
        "created_at": _now(),
        "first_contact_at": _now(),
        "last_activity_at": _now(),
        "status": "in_progress",
        "language": _clean(language, 10) or "he",
        "client_email": "",
        "client_name": "",
        "source_platform": classified["platform"],
        "source_confidence": classified["confidence"],
        "source_detail": classified["detail"],
        "referrer": referrer,
        "landing_path": landing_path,
        "click_ids": present_click_ids,
        **utm,
    }
    _db().table("leads").insert(row).execute()
    return {"lead_id": lead_id, "source": classified}


def _lead_row(lead_id: str) -> dict:
    rows = (_db().table("leads").select("*").eq("lead_id", lead_id)
            .limit(1).execute().data or [])
    return rows[0] if rows else {}


def record_message(lead_id: str, role: str, content: str) -> dict:
    """Append one sales-chat message. Reachable from a public endpoint, so it
    verifies the lead exists and enforces the per-lead cap rather than trusting
    the caller. Runs once per message in a live chat, so it stays at three
    queries, not four."""
    if role not in ("user", "assistant"):
        return {"success": False, "error": "role must be user or assistant"}
    content = str(content or "").strip()[:MAX_MESSAGE_CHARS]
    if not content:
        return {"success": False, "error": "empty message"}
    # Guard before the update below: a null/empty key would become an
    # unfiltered `is.null` match and stamp last_activity_at across the table.
    if not lead_id or not isinstance(lead_id, str):
        return {"success": False, "error": "missing lead id"}

    # One query doing double duty: bumping last_activity_at (the sole input to
    # the dropped_off derivation) also proves the lead exists, because an
    # unknown lead_id updates zero rows.
    touched = (_db().table("leads").update({"last_activity_at": _now()})
               .eq("lead_id", lead_id).execute().data or [])
    if not touched:
        return {"success": False, "error": "unknown lead"}

    existing = (_db().table("lead_messages").select("id", count="exact")
                .eq("lead_id", lead_id).limit(1).execute())
    if (existing.count or 0) >= MAX_MESSAGES_PER_LEAD:
        return {"success": False, "error": "message cap reached"}

    _db().table("lead_messages").insert({
        "lead_id": lead_id, "role": role, "content": content, "created_at": _now(),
    }).execute()
    return {"success": True}


def attach_proposal(lead_id: str, fields: dict) -> bool:
    """Fold the built proposal into the lead row the chat already opened.
    Returns False when there's no such lead — the caller then falls back to
    inserting a standalone row, so a proposal is never lost to a tracking
    hiccup."""
    if not lead_id or not _lead_row(lead_id):
        return False
    _db().table("leads").update({**fields, "last_activity_at": _now()}).eq(
        "lead_id", lead_id).execute()
    return True


def mark_converted(lead_id: str, client_id: int, client_email: str, client_name: str) -> bool:
    """The exact lead->client link, replacing the newest-contactless-lead guess
    the checkout used to make."""
    if not lead_id or not _lead_row(lead_id):
        return False
    _db().table("leads").update({
        "status": "converted",
        "client_id": client_id,
        "client_email": client_email,
        "client_name": client_name,
        "converted_at": _now(),
        "last_activity_at": _now(),
    }).eq("lead_id", lead_id).execute()
    return True


def set_status(lead_id: str, status: str) -> dict:
    if status not in STORED_STATUSES:
        return {"success": False, "error": f"status must be one of {STORED_STATUSES}"}
    if not _lead_row(lead_id):
        return {"success": False, "error": "unknown lead"}
    _db().table("leads").update({"status": status}).eq("lead_id", lead_id).execute()
    return {"success": True, "status": status}


def _derive_status(row: dict) -> str:
    """Stored status, except that an untouched in_progress lead reads as
    dropped_off once it goes quiet. Never persisted — see module docstring."""
    stored = row.get("status") or "in_progress"
    if stored != "in_progress":
        return stored
    last = row.get("last_activity_at") or row.get("created_at")
    if not last:
        return stored
    try:
        seen = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
    except ValueError:
        return stored
    if datetime.now(timezone.utc) - seen > timedelta(hours=DROP_OFF_HOURS):
        return "dropped_off"
    return stored


def _summarize(row: dict) -> dict:
    """One CRM table row — only the fields the list view renders."""
    proposal = row.get("proposal") or {}
    return {
        "lead_id": row.get("lead_id"),
        "id": row.get("id"),
        "client_id": row.get("client_id"),
        "client_name": row.get("client_name") or "",
        "client_email": row.get("client_email") or "",
        "status": _derive_status(row),
        "language": row.get("language") or "",
        "first_contact_at": row.get("first_contact_at") or row.get("created_at"),
        "last_activity_at": row.get("last_activity_at"),
        "converted_at": row.get("converted_at"),
        "source_platform": row.get("source_platform") or "unknown",
        "source_confidence": row.get("source_confidence") or "none",
        "source_detail": row.get("source_detail") or "",
        "utm_source": row.get("utm_source") or "",
        "utm_medium": row.get("utm_medium") or "",
        "utm_campaign": row.get("utm_campaign") or "",
        "utm_content": row.get("utm_content") or "",
        "has_proposal": bool(proposal),
        "setup_fee": row.get("setup_fee") or 0,
        "monthly_fee": row.get("monthly_fee") or 0,
    }


def _is_upgrade_row(row: dict) -> bool:
    """support_agent files existing-client upgrade requests into `leads` too.
    They are not prospects and must not pad the CRM's counts."""
    return bool((row.get("answers") or {}).get("_upgrade_request"))


def list_leads(limit: int = 500) -> dict:
    """Every prospect, newest first, plus the counts the CRM header shows.

    `select("*")` pulls the full `answers`/`proposal` jsonb for every row just
    to compute `has_proposal` and filter upgrade rows, which is wasteful and
    will need narrowing (a PostgREST `answers->>_upgrade_request` projection)
    once lead volume is real. Left as-is deliberately: that syntax cannot be
    verified from this dev machine, and shipping an untestable query to solve a
    problem that doesn't exist yet is the worse trade.
    """
    rows = (_db().table("leads").select("*")
            .order("created_at", desc=True).limit(limit).execute().data or [])
    leads = [_summarize(row) for row in rows if not _is_upgrade_row(row)]

    by_status, by_source = {}, {}
    for lead in leads:
        by_status[lead["status"]] = by_status.get(lead["status"], 0) + 1
        by_source[lead["source_platform"]] = by_source.get(lead["source_platform"], 0) + 1
    return {
        "leads": leads,
        "counts": {"total": len(leads), "by_status": by_status, "by_source": by_source},
        "drop_off_hours": DROP_OFF_HOURS,
    }


def get_lead(lead_id: str) -> dict:
    """Full detail for the drawer: the summary, the structured answers, the
    proposal, and the transcript if one was captured."""
    row = _lead_row(lead_id)
    if not row:
        return {}
    messages = (_db().table("lead_messages")
                .select("role, content, created_at")
                .eq("lead_id", lead_id)
                .order("created_at", desc=False)
                .limit(MAX_MESSAGES_PER_LEAD)
                .execute().data or [])
    return {
        **_summarize(row),
        "answers": row.get("answers") or {},
        "proposal": row.get("proposal") or {},
        "referrer": row.get("referrer") or "",
        "landing_path": row.get("landing_path") or "",
        "click_ids": row.get("click_ids") or {},
        "messages": messages,
        # Leads that predate this module have no transcript and never will —
        # say so rather than rendering a convincing empty conversation.
        "transcript_available": bool(messages),
    }
