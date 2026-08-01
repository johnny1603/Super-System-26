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

# ─── The connection checklist, DERIVED FROM THE PURCHASED PACKAGE ────────────
# Replaces the earlier "whatever they've already connected" guess, which could
# have started a 90-day promise early (the `expectation_inferred` flag that
# shipped flagged as a known hole — this closes it).
#
# The authority is `recommended_services` on the package the client actually
# checked out with. The proposal prompt defines it as "ONLY the ONGOING managed
# services of the package (the things the monthly fee is computed from)", and
# enforces that every monthly_breakdown platform-management line has its
# platform present there and vice versa. So it is the one field that is BOTH
# machine-readable and contractually tied to what the client is paying for.
#
# ORDERED, and the order is the product: the dashboard walks the client through
# one step at a time. Website first because SEO, landing pages and tracking all
# sit on top of it; then the paid platforms (where the money starts moving);
# then organic/content; then the client-paid generation keys.
CONNECTION_STEPS = [
    # key            client_accounts platforms that PROVE it   required when...
    {"key": "website",     "platforms": ("wordpress",),
     "services": ("website", "seo", "organic")},
    {"key": "google_ads",  "platforms": ("google_ads",),
     "services": ("google",)},
    {"key": "meta",        "platforms": ("meta_ads", "meta_page"),
     "services": ("meta", "facebook", "instagram")},
    {"key": "tiktok",      "platforms": ("tiktok",),
     "services": ("tiktok",)},
    {"key": "youtube",     "platforms": ("youtube",),
     "services": ("youtube",)},
    {"key": "media",       "platforms": ("higgsfield",),
     "services": ("media", "content", "video", "avatar")},
    {"key": "avatar",      "platforms": ("heygen",),
     "services": ("avatar",)},
]

# Legacy alias kept so nothing that imported it breaks; the checklist above is
# the real source now.
INTEGRATION_PLATFORMS = {
    platform: step["key"] for step in CONNECTION_STEPS for platform in step["platforms"]
}


def _chosen_package(client_id: int) -> dict:
    """The package the client actually checked out with.

    Same lookup `website_agent._package_includes_hosting` already uses, and
    for the same reason: the ORIGINAL `subscription_created` row carries the
    package_id, upgrade rows never do, and a cancellation ends the search.
    Reused deliberately rather than reimplemented — two different answers to
    "which package did they buy" is exactly the bug worth not having."""
    package_id = None
    for row in (_db().table("client_activity").select("action_type,details")
                .eq("client_id", client_id).eq("agent_name", "paypal_service")
                .order("created_at", desc=True).limit(50).execute().data or []):
        if row.get("action_type") == "subscription_cancelled":
            break
        details = row.get("details") or {}
        if row.get("action_type") == "subscription_created" and not details.get("upgrade"):
            package_id = details.get("package_id")
            break
    if not package_id:
        return {}
    try:
        from agents.budget_agent import _lead_row
        lead, _source = _lead_row(client_id)
    except Exception as e:
        print(f"[{SERVICE_NAME}] package lookup failed for client {client_id}: {e}")
        return {}
    packages = (lead.get("proposal") or {}).get("packages") or []
    return next((p for p in packages if p.get("id") == package_id), {})


def required_connections(client_id: int) -> dict:
    """The ordered checklist THIS client must complete, from THEIR package.

    Returns {"steps": [...], "resolved": bool, "services": [...]}.

    `resolved` False means we could not read the purchased package (no
    checkout row, no matching lead, package_id absent from the stored
    proposal). Callers gating the 90-day clock MUST refuse to start it in that
    case — fail closed, exactly like the website self-provision entitlement
    check. An unresolved package is a reason to ask a human, never a reason to
    assume the client owes nothing."""
    package = _chosen_package(client_id)
    services = [str(s).strip().lower() for s in (package.get("recommended_services") or [])]

    # A new-site build is priced as a monthly_breakdown line rather than a
    # recommended_service, so it needs its own check — otherwise a client whose
    # package builds them a site wouldn't be asked to connect one.
    breakdown_text = " ".join(str(k) for k in (package.get("monthly_breakdown") or {}))
    builds_site = "אחסון" in breakdown_text

    steps = []
    for step in CONNECTION_STEPS:
        needed = builds_site and step["key"] == "website"
        if not needed:
            needed = any(any(token in service for token in step["services"])
                         for service in services)
        if needed:
            steps.append({"key": step["key"], "platforms": list(step["platforms"])})

    return {"steps": steps, "services": services, "resolved": bool(package)}

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

def connection_status(client_id: int) -> dict:
    """Which of THIS client's required integrations are live vs still missing,
    as an ORDERED walk-through — the dashboard shows one `next_step` at a time
    rather than a flat grid of everything.

    The expectation now comes from the purchased package
    (`required_connections`), not from a guess. When the package cannot be
    resolved, `complete` is False and `resolved` is False: the 90-day clock
    must not start on an unknown obligation.
    """
    accounts = (_db().table("client_accounts").select("platform,status")
                .eq("client_id", client_id).eq("status", "active")
                .execute().data or [])
    connected_platforms = {a["platform"] for a in accounts}

    checklist = required_connections(client_id)
    steps = []
    for step in checklist["steps"]:
        # ANY of a step's platforms proves it — one Meta consent may return the
        # ad account, the Page, or both, and either is "Meta is connected".
        done = any(platform in connected_platforms for platform in step["platforms"])
        steps.append({"key": step["key"], "platforms": step["platforms"], "done": done})

    pending = [s for s in steps if not s["done"]]
    done_steps = [s for s in steps if s["done"]]
    return {
        "steps": steps,
        "connected": [s["key"] for s in done_steps],
        "expected": [s["key"] for s in steps],
        "missing": [s["key"] for s in pending],
        # The single next thing to ask for. None when nothing is left.
        "next_step": pending[0]["key"] if pending else None,
        "done_count": len(done_steps),
        "total_count": len(steps),
        "complete": checklist["resolved"] and not pending and bool(steps),
        # False = the purchased package could not be read; callers gating the
        # 90-day clock must fail closed on this (see required_connections).
        "resolved": checklist["resolved"],
    }


# ─── The 90-day clock start (recorded once, when the last step lands) ────────

CONNECTIONS_COMPLETE_ACTION = "connections_completed"


def connections_completed_at(client_id: int) -> str:
    """When this client finished ALL their required connections — the exact
    moment the 90-day cycle starts from. Empty string = not yet.

    Recorded as a row rather than recomputed, because the cycle needs a fixed
    start date: a client who later disconnects a platform must not have their
    90-day clock silently reset, and a package edited afterwards must not move
    a date the client was already told."""
    rows = (_db().table("client_activity").select("created_at")
            .eq("client_id", client_id).eq("agent_name", SERVICE_NAME)
            .eq("action_type", CONNECTIONS_COMPLETE_ACTION)
            .order("created_at").limit(1).execute().data or [])
    return rows[0].get("created_at", "") if rows else ""


def note_connections_complete(client_id: int) -> dict:
    """Idempotently stamp the completion moment. Safe to call on every status
    read — it writes at most once, ever, and only when the checklist really is
    resolved AND complete (never on the unresolved-package path)."""
    existing = connections_completed_at(client_id)
    if existing:
        return {"newly_completed": False, "at": existing}
    status = connection_status(client_id)
    if not (status["resolved"] and status["complete"]):
        return {"newly_completed": False, "at": ""}

    from agents.client_agent import log_activity
    log_activity(client_id, SERVICE_NAME, CONNECTIONS_COMPLETE_ACTION,
                 {"steps": status["expected"]}, {})
    return {"newly_completed": True, "at": connections_completed_at(client_id)}


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
