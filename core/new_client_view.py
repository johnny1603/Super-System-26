"""Admin "view as a brand-new client" (2026-11-16, docs/HOME-FOCUS.md).

The owner opens a client's dashboard (the existing read-only "enter as
client", core/admin_view) in a second mode, `new_client`: the page shows
exactly what a client sees on day one, with nothing connected, no
characterisation, no goals, no ideas, no cards and an empty chat. It uses the
REAL package, so the required connections are that client's own.

Nothing is written or deleted. The real data is untouched: each read endpoint
the home page uses passes its answer through one of the functions below, and
only when the admin_view cookie carries mode `new_client` (bound to the admin
session, 2 hours at most, audited on start like the normal view). It is never
a client session, and a client can never reach this mode.

Why it exists: the owner's test account (client 27) is internal and fully set
up, so it can never show the onboarding focus a new client gets.
"""
MODE = "new_client"


def status(real: dict) -> dict:
    """client_journey.connection_status as if nothing were connected."""
    real = dict(real or {})
    steps = [dict(s, done=False, ready=False) for s in real.get("steps") or []]
    return {**real, "steps": steps, "connected": [], "missing": [s["key"] for s in steps],
            "not_ready": [], "notices": [], "next_step": steps[0]["key"] if steps else None,
            "done_count": 0, "complete": False}


def onboarding(real_status: dict) -> dict:
    """home_focus.onboarding for day one: the three tasks, none done."""
    fresh = status(real_status)
    if not fresh.get("resolved"):
        return {"known": False, "done": True}
    return {"known": True, "done": False, "characterised": False, "goals_agreed": False,
            "connections": {"complete": False, "done": 0, "total": fresh.get("total_count", 0),
                            "next": fresh.get("next_step") or ""}}


def home_focus(data: dict, real_status: dict) -> dict:
    return {**(data or {}), "resume": {}, "goals": {}, "onboarding": onboarding(real_status)}


def dashboard(data: dict) -> dict:
    """/api/dashboard: no connections, no activity, the tour not taken."""
    return {**(data or {}), "connections": [], "google_ads_customer_id": "", "google_ads_account_name": "",
            "gtm_needs_reconnect": False, "google_ads_needs_reconnect": False,
            "website_provision_status": None, "activity": [], "activity_month_count": 0,
            "tour_completed": False, "simulated_new_client": True}


def journey(real: dict) -> dict:
    """Only the sign-up milestone is reached."""
    milestones = [dict(m, done=m.get("key") == "journey_signup", at=m.get("at") if m.get("key") == "journey_signup" else "",
                       detail="") for m in (real or {}).get("milestones") or []]
    return {**(real or {}), "milestones": milestones, "done_count": sum(1 for m in milestones if m["done"]),
            "connection_status": status((real or {}).get("connection_status") or {})}


def interview(real: dict) -> dict:
    return {**(real or {}), "completed": False, "ready": False, "competitors_captured": 0}


EMPTY_LIST_ENDPOINTS = frozenset({"client_suggestions", "client_open_actions", "client_chat_history"})
