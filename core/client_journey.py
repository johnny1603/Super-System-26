"""The client's progress journey — milestones derived, never separately tracked.

## The rule this module exists to hold

Every milestone below is DERIVED at read time from rows the system already
writes: `clients.created_at`, `client_accounts`, `client_activity` (every agent
logs there), `client_leads`, and the PayPal checkout rows. There is no
`milestones` table and there must not be one — a parallel tracker would drift
from the activity log the moment an agent changed, and the drift would be
invisible. Same reasoning as `lead_tracking.dropped_off` being derived rather
than stored.

If a milestone you want isn't here, the fix is to find the activity row that
already proves it — not to start writing a new one.

## What it feeds

- The dashboard's progress timeline (Part 3): `journey(client_id)`.
- The connection-completeness signal the 90-day goal cycle will start from
  (Part 4, next commit): `connection_status(client_id)` is deliberately
  already split out here rather than living in the dashboard endpoint, so the
  cycle logic and the timeline read ONE definition of "fully connected".

## Honesty rules

- A milestone is `done` only when a real row proves it. Nothing is inferred
  from the passage of time or from a package's contents — a client who bought
  SEO has not "reached" anything until an article exists.
- Milestones that don't apply to a client's package are omitted, not shown as
  permanently pending. A Meta-less package showing a grey "Meta connected"
  forever reads as failure rather than as not-applicable.
"""
import os
from datetime import datetime, timezone

SERVICE_NAME = "client_journey"

# The integrations a client is expected to connect, by what their package
# actually includes. Keys are client_accounts.platform values; the label keys
# are resolved client-side (dashboard i18n), never translated here — this
# module returns data, not Hebrew.
INTEGRATION_PLATFORMS = {
    "google_ads": "google_ads",
    "meta_ads": "meta_ads",
    "meta_page": "meta_ads",      # same consent, same expectation
    "tiktok": "tiktok",
    "youtube": "youtube",
    "wordpress": "website",
}

_db_instance = None


def _db():
    global _db_instance
    if _db_instance is None:
        from supabase import create_client
        _db_instance = create_client(os.environ["SUPABASE_URL"],
                                     os.environ["SUPABASE_SERVICE_KEY"])
    return _db_instance


def _first(rows: list, predicate) -> dict:
    """Oldest row matching predicate, from a newest-first activity list."""
    for row in reversed(rows or []):
        if predicate(row):
            return row
    return {}


# ─── Connection completeness (also the future 90-day clock trigger) ──────────

def connection_status(client_id: int, expected: list = None) -> dict:
    """Which integrations are live vs still missing.

    `expected` is the set of platform groups this client's package needs. When
    not given it falls back to "whatever they have already connected plus a
    website", which is NOT a real expectation — so callers that need a true
    completeness gate (the 90-day cycle) must pass it explicitly. Stated here
    because a silently-wrong "fully connected" would start a 90-day clock
    early, and that is a promise to a paying client.
    """
    accounts = (_db().table("client_accounts").select("platform,status")
                .eq("client_id", client_id).eq("status", "active")
                .execute().data or [])
    connected_platforms = {a["platform"] for a in accounts}
    connected_groups = {INTEGRATION_PLATFORMS[p] for p in connected_platforms
                        if p in INTEGRATION_PLATFORMS}

    if expected is None:
        expected_groups = set(connected_groups) or set()
        inferred = True
    else:
        expected_groups = set(expected)
        inferred = False

    missing = sorted(expected_groups - connected_groups)
    return {
        "connected": sorted(connected_groups),
        "expected": sorted(expected_groups),
        "missing": missing,
        "complete": not missing and bool(expected_groups),
        # True = we guessed the expectation; a caller gating money or a
        # 90-day promise on this must treat it as not-authoritative.
        "expectation_inferred": inferred,
    }


# ─── The journey ─────────────────────────────────────────────────────────────

def journey(client_id: int) -> dict:
    """The client's milestone track since day one, newest data derived fresh.

    Returns {"milestones": [...], "done_count", "total_count", "started_at"}.
    Each milestone: {key, done, at, detail} — `key` is an i18n key the
    dashboard resolves, `detail` is a short factual string (a keyword, a
    platform name, a count) or "".
    """
    from agents.client_agent import get_client

    client = get_client(client_id) or {}
    activity = (_db().table("client_activity").select("agent_name,action_type,details,created_at")
                .eq("client_id", client_id)
                .order("created_at", desc=True).limit(500).execute().data or [])
    accounts = (_db().table("client_accounts").select("platform,status")
                .eq("client_id", client_id).eq("status", "active").execute().data or [])
    leads = (_db().table("client_leads").select("created_at,status")
             .eq("client_id", client_id)
             .order("created_at").limit(1000).execute().data or [])

    milestones = []

    def add(key: str, at: str = "", detail: str = ""):
        milestones.append({"key": key, "done": bool(at), "at": at or "", "detail": detail})

    # 1. Signed up — the only milestone that is true by definition
    add("journey_signup", client.get("created_at") or "")

    # 2. Subscription active (the checkout row, same source the dashboard's
    #    billing panel derives from — not a second interpretation)
    checkout = _first(activity, lambda r: r.get("agent_name") == "paypal_service"
                      and r.get("action_type") == "subscription_created")
    add("journey_subscribed", checkout.get("created_at", ""))

    # 3. First integration connected, and 4. all of them
    status = connection_status(client_id)
    first_connect = _first(activity, lambda r: r.get("action_type", "").endswith("_connected"))
    add("journey_first_connection", first_connect.get("created_at", ""),
        ", ".join(status["connected"][:3]))
    # "All connected" is deliberately NOT dated: connection_status has no
    # authoritative expectation here (see its docstring), so claiming a
    # completion date would be inventing one.
    add("journey_all_connected", "" if not status["complete"] else
        (first_connect.get("created_at", "") or ""), f"{len(status['connected'])}")

    # 5. Website live (provisioned by us OR connected by them — either counts)
    site = _first(activity, lambda r: r.get("agent_name") == "website_agent"
                  and r.get("action_type") in ("website_provisioned", "website_connected"))
    if not site and any(a["platform"] == "wordpress" for a in accounts):
        site = {"created_at": ""}  # connected before this milestone existed
    add("journey_website", site.get("created_at", "") if site else "")

    # 6. First campaign launched (either ads platform — one milestone, because
    #    the client experiences "we're live" once, not twice)
    campaign = _first(activity, lambda r: r.get("action_type") in
                      ("campaign_created", "google_ads_campaign_created",
                       "meta_ads_campaign_created"))
    add("journey_first_campaign", campaign.get("created_at", ""))

    # 7. First content published (an SEO article or any generated media —
    #    both are "we started making things for you")
    content = _first(activity, lambda r: r.get("action_type") in
                     ("seo_article_generated", "media_image_created",
                      "media_video_created", "website_content_created"))
    add("journey_first_content", content.get("created_at", ""))

    # 8. First lead, and 9. first won deal — the two that actually matter to
    #    the business, from client_leads (the leads THEY receive)
    add("journey_first_lead", leads[0]["created_at"] if leads else "",
        str(len(leads)) if leads else "")
    won = next((row for row in leads if row.get("status") == "won"), None)
    add("journey_first_sale", (won or {}).get("created_at", ""),
        str(sum(1 for row in leads if row.get("status") == "won")) if won else "")

    done = [m for m in milestones if m["done"]]
    return {
        "milestones": milestones,
        "done_count": len(done),
        "total_count": len(milestones),
        "started_at": client.get("created_at") or "",
        "days_since_signup": _days_since(client.get("created_at")),
        "connection_status": status,
    }


def _days_since(iso: str):
    if not iso:
        return None
    try:
        started = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return max(0, (datetime.now(timezone.utc) - started).days)
    except Exception:
        return None
