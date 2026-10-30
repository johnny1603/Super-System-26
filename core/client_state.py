"""client_state — ONE read-only snapshot of a client's account, for every agent.

Why: each agent used to see only its own slice. The website persona had no
Tag Manager read at all, so asked "is my tracking working?" it said it could
not see measurement numbers — while the Performance page card, one screen
away, knew the container, the site match and the token health. This module is
the shared answer to "what is true about this client right now", built ONLY
from the readers that already exist:

  channels ....... performance.channel_statuses (THE status resolver)
  tracking ....... gtm_connection.connection_view + website_agent.gtm_missing_scopes
                   + the website_tracking card (lead event live in the PUBLISHED version)
  connections .... client_journey.connection_status + client_agent.get_accounts
                   (rows WITHOUT secrets) + google_ads_agent.connection_info
  campaigns ...... google_ads_agent / meta_ads_agent.get_campaign_performance
  budget ......... budget_agent.get_forecast (stated) + the paid cards' 30-day spend
  leads .......... the leads card (client_leads) + crm_agent.get_status
  approvals ...... engagement_agent.get_suggestions + open agent_actions cards
  website ........ website_agent.get_site_overview
  landing_pages .. landing_page_agent.dashboard_payload
  media .......... media_agent.get_monthly_usage
  media_folder ... drive_mirror.list_folder (what is in their Drive media folder)
  package ........ client_journey.required_connections
  site_security .. php_exposure.site_security_fact (PHP2Shell: the STORED last
                   check per WordPress site — no request to the site here)
  outreach ....... outreach_tracker.for_agent
  creative_tools . vendor_tools.statuses (each tool on its own, no key, no last4)
                   + avatar_video.providers_view
  voice .......... voice_recordings.for_agent (the Drive voice folder listing)

Rules:
- Read-only. Nothing here writes to a platform; no new platform call exists
  here — every one below is a reader the dashboard already makes, behind its
  own cache, and each section is cached again here (SECTION_TTL) so a chat
  turn costs nothing extra while the snapshot is fresh.
- Failures are never cached and never reach a model as text: a section whose
  read raised (or came back with "error") is left out and named in
  `_failed`. support_agent maps those names to data topics and decides the
  fixed "can't pull that up" line in code (HANDOFF-chat-data-availability.md);
  the Control Room turns them into explicit data_gaps.
- A dead or half-set-up connection is NOT a failure — it is a fact the read
  returned (needs_reconnect, missing permissions, mismatch) and agents state it
  and offer the fix (core/agent_actions).
- Never a token, never a secret: get_accounts strips secrets, connection_view
  carries none, and nothing here reads a credential column.
"""
import json
import time
from datetime import datetime, timezone

SERVICE_NAME = "client_state"

SECTION_TTL = {"channels": 300, "website": 300, "media_folder": 300, "website_traffic": 600}
DEFAULT_TTL = 120

# section -> the data_topic a client question about it is labelled with
SECTION_TOPIC = {
    "channels": "account", "connections": "account", "budget": "account",
    "approvals": "account", "media": "account", "media_folder": "account", "package": "account",
    "tracking": "tracking", "leads": "leads",
    "google_campaigns": "google_ads", "meta_campaigns": "meta_ads",
    "website": "website", "landing_pages": "website", "site_security": "website",
    # Its own topic: an unreadable outreach record (migration not run yet)
    # must not turn every "account" question into the can't-read line
    "outreach": "outreach",
    "creative_tools": "account",
    # Its own topic: an unreadable Drive voice folder must not turn every
    # "account" question into the can't-read line
    "voice": "voice",
    "website_traffic": "website",
}

_cache = {}   # (client_id, section) -> (time, value)


class _ReadFailed(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def forget(client_id: int):
    for key in [k for k in _cache if k[0] == client_id]:
        _cache.pop(key, None)


def _warn(message: str, **fields):
    from core.redaction import redact
    print(json.dumps({"severity": "WARNING", "message": redact(message), "service": SERVICE_NAME,
                      **fields}, ensure_ascii=False, default=str), flush=True)


def _ok(value: dict) -> dict:
    """A reader that answered with an error dict is a failed read."""
    if isinstance(value, dict) and value.get("error"):
        raise _ReadFailed(str(value["error"])[:200])
    return value


# ─── Sections (each compact: what an agent needs to answer, nothing more) ────

def _channels(client_id: int) -> dict:
    from core import performance
    statuses = performance.channel_statuses(client_id)
    by_channel = {}
    for channel, card in (statuses.get("channels") or {}).items():
        compact = {"status": card.get("status")}
        for key in ("reason", "note", "key_numbers", "period_days"):
            if card.get(key) is not None:
                compact[key] = card[key]
        when = card.get("data_pulled_at") or card.get("last_checked")
        if when:
            compact["as_of"] = when
        by_channel[channel] = compact
    return {"package_readable": bool(statuses.get("plan_resolved")), "by_channel": by_channel}


_TOKEN_HEALTH = {"ok": "ok", "token_dead": "expired_needs_reconnect",
                 "container_unavailable": "lost_access_to_container",
                 "access_pending": "waiting_on_uallak_setup", "unavailable": "could_not_check_now"}


def _tracking(client_id: int, channels: dict) -> dict:
    from core import gtm_connection
    from agents.website_agent import gtm_missing_scopes
    view = gtm_connection.connection_view(client_id)
    if not view.get("connected"):
        return {"connected": False}
    health = _TOKEN_HEALTH.get((view.get("token") or {}).get("state", "ok"), "could_not_check_now")
    missing = bool(gtm_missing_scopes(client_id))
    card = ((channels or {}).get("by_channel") or {}).get("website_tracking") or {}
    lead_live = {"active": True, "connected_no_data": False}.get(card.get("status"))
    container = view.get("container") or {}
    site = view.get("site") or {}
    return {
        "connected": True,
        "container_name": container.get("name", ""),
        "container_id": container.get("public_id", ""),
        # what the client's homepage loads: found (with its id) | none (no
        # Tag Manager snippet) | no_site (no website connected) | unknown
        "site_snippet": {"status": site.get("status"), "container_id": site.get("gtm_id")},
        "matches_site": view.get("match"),    # match | mismatch | no_gtm_on_site | no_site | unknown
        "connection_health": health,
        "missing_permissions": missing,
        "needs_reconnect": missing or health in ("expired_needs_reconnect", "lost_access_to_container"),
        # True: a lead event is live in the PUBLISHED version; False: not set
        # up yet; None: could not be confirmed on this read
        "lead_event_live": lead_live,
        "checked_at": view.get("checked_at"),
    }


def _connections(client_id: int) -> dict:
    from agents.client_agent import get_accounts
    from core import client_journey
    status = client_journey.connection_status(client_id)
    accounts = get_accounts(client_id)   # never carries secrets
    out = {
        "package_readable": bool(status.get("resolved")),
        "required": status.get("expected") or [],
        "connected": status.get("connected") or [],
        "missing": status.get("missing") or [],
        "active_platforms": sorted({a.get("platform") for a in accounts if a.get("status") == "active"}),
        "waiting_platforms": sorted({a.get("platform") for a in accounts
                                     if a.get("status") not in ("active", None, "")}),
    }
    try:
        from agents.google_ads_agent import connection_info
        info = connection_info(client_id)
        if info:
            out["google_ads_account"] = {"id": info.get("display_id", ""), "name": info.get("name", ""),
                                         "status": info.get("status", "")}
            from agents.google_ads_agent import grant_needs_reconnect
            out["google_ads_account"]["needs_reconnect"] = bool(grant_needs_reconnect(client_id))
    except Exception as e:
        _warn(f"client {client_id}: google ads connection_info failed: {type(e).__name__}",
              client_id=client_id)
    return out


def _campaigns(perf: dict) -> dict:
    if isinstance(perf, dict) and perf.get("needs_reconnect"):
        # A dead grant is a FACT the agents state (and offer the reconnect
        # card for), not a failed read behind the "can't pull that up" line
        return {"connected": True, "needs_reconnect": True}
    perf = _ok(perf)
    if not perf.get("connected"):
        return {"connected": False}
    campaigns = perf.get("campaigns") or []
    keep = ("name", "status", "impressions", "clicks", "cost", "conversions")
    by_status = {}
    for c in campaigns:
        by_status[c.get("status", "")] = by_status.get(c.get("status", ""), 0) + 1
    return {"connected": True, "window": "last_30_days", "count_by_status": by_status,
            "campaigns": [{k: c.get(k) for k in keep if k in c} for c in campaigns[:15]]}


def _google_campaigns(client_id: int) -> dict:
    from agents.google_ads_agent import get_campaign_performance
    return _campaigns(get_campaign_performance(client_id))


def _meta_campaigns(client_id: int) -> dict:
    from agents.meta_ads_agent import get_campaign_performance
    return _campaigns(get_campaign_performance(client_id))


def _budget(client_id: int, channels: dict) -> dict:
    from agents.budget_agent import get_forecast
    forecast = get_forecast(client_id)
    cards = (channels or {}).get("by_channel") or {}
    spend = {ch: (cards.get(ch) or {}).get("key_numbers", {}).get("spend")
             for ch in ("google_ads", "meta")
             if (cards.get(ch) or {}).get("status") == "active"}
    return {"stated_monthly_budget_ils": forecast.get("stated_marketing_budget_ils")
            if forecast.get("available") else None,
            "spend_last_30_days_ils": spend}


def _leads(client_id: int, channels: dict) -> dict:
    card = ((channels or {}).get("by_channel") or {}).get("leads") or {}
    out = {"status": card.get("status"), "counts": card.get("key_numbers") or {}}
    try:
        from agents.crm_agent import get_status
        crm = get_status(client_id)
        out["crm"] = {"connected": bool(crm.get("connected")), "vendor": crm.get("vendor_label", "")}
    except Exception as e:
        _warn(f"client {client_id}: crm status failed: {type(e).__name__}", client_id=client_id)
    return out


def _approvals(client_id: int) -> dict:
    from agents.engagement_agent import get_suggestions
    from core import agent_actions
    pending = get_suggestions(client_id, status="pending", limit=10)
    return {"waiting_for_client": [{"suggestion_id": s.get("id"), "title": s.get("title", ""),
                                    "kind": s.get("kind", "")} for s in pending],
            "open_action_cards": [{"id": c["id"], "action": c["action"], "status": c["status"]}
                                  for c in agent_actions.open_cards(client_id, actor="admin")]}


def _website(client_id: int) -> dict:
    from agents.website_agent import get_site_overview
    overview = _ok(get_site_overview(client_id))
    if not overview.get("connected"):
        return {"connected": False}
    posts = overview.get("recent_posts") or []
    return {"connected": True, "site_url": overview.get("site_url", ""),
            "site_name": overview.get("site_name", ""), "seo_plugin": overview.get("seo_plugin"),
            "recent_posts": [{"title": p.get("title", ""), "status": p.get("status")} for p in posts[:5]],
            "drafts": sum(1 for p in posts if p.get("status") == "draft")}


def _landing_pages(client_id: int) -> dict:
    from agents.landing_page_agent import dashboard_payload
    payload = dashboard_payload(client_id)
    pages = payload.get("pages") or []
    return {"used": payload.get("used", len(pages)), "included": payload.get("included"),
            "domain_mode": (payload.get("domain") or {}).get("mode"),
            "pages": [{"title": p.get("title", ""), "status": p.get("status"), "leads": p.get("leads", 0)}
                      for p in pages]}


def _media(client_id: int) -> dict:
    from agents.media_agent import get_monthly_usage
    return get_monthly_usage(client_id)


MEDIA_SUBFOLDERS_READ = 8
MEDIA_VIDEOS_LISTED = 10


def _media_folder(client_id: int) -> dict:
    """What is IN the client's Drive media folder (the Media tab's own
    listing, drive_mirror — cached there too): counts by kind, the newest
    videos (with the Drive id an upload proposal must name — never shown to
    the client — and whether each fits the upload size cap), and today's
    YouTube uploads against the per-client cap. Without it, "can you upload a
    video from my media?" had no fact to answer from (2026-10-22)."""
    from core import drive_mirror
    from core.video_limits import TIKTOK_INBOX_MAX_MB, VIDEO_UPLOAD_MAX_MB, fits, fits_tiktok
    root = drive_mirror.list_folder(client_id)
    if not root.get("exists"):
        return {"exists": False}
    _ok(root)
    files = list(root.get("files") or [])
    folders = root.get("folders") or []
    for folder in folders[:MEDIA_SUBFOLDERS_READ]:
        page = drive_mirror.list_folder(client_id, folder["id"])
        if not page.get("error"):
            files += page.get("files") or []
    counts = {}
    for f in files:
        counts[f.get("kind", "file")] = counts.get(f.get("kind", "file"), 0) + 1
    videos = sorted((f for f in files if f.get("kind") == "video"),
                    key=lambda f: f.get("modified_time") or "", reverse=True)
    out = {"exists": True, "counts": counts,
           "videos": [{"drive_file_id": v.get("id", ""), "name": v.get("name", ""),
                       "size_mb": round(v["size"] / 1048576, 1) if v.get("size") else None,
                       "uploadable": fits(v.get("size")),
                       "tiktok_uploadable": fits_tiktok(v.get("size"))}
                      for v in videos[:MEDIA_VIDEOS_LISTED]],
           "upload_max_mb": VIDEO_UPLOAD_MAX_MB,
           "tiktok_upload_max_mb": TIKTOK_INBOX_MAX_MB,
           "complete": len(folders) <= MEDIA_SUBFOLDERS_READ and not root.get("next_page_token")}
    try:
        from agents.youtube_content_agent import DAILY_UPLOADS_PER_CLIENT, uploads_today
        out["youtube_uploads_today"] = uploads_today(client_id)
        out["youtube_daily_upload_cap"] = DAILY_UPLOADS_PER_CLIENT
    except Exception as e:
        _warn(f"client {client_id}: uploads_today failed: {type(e).__name__}", client_id=client_id)
    return out


def _package(client_id: int) -> dict:
    from core.client_journey import required_connections
    checklist = required_connections(client_id)
    return {"readable": bool(checklist.get("resolved")), "services": checklist.get("services") or []}


def _site_security(client_id: int) -> dict:
    """PHP2Shell status of the client's WordPress site(s) as last checked —
    patched / vulnerable / unknown / not_checked. verified_safe is always
    False: the check reads a header, which is the host's claim, not proof."""
    from core import php_exposure
    return php_exposure.site_security_fact(client_id)


# ─── The snapshot ────────────────────────────────────────────────────────────

def _section(client_id: int, name: str, fetch, refresh: bool):
    hit = _cache.get((client_id, name))
    ttl = SECTION_TTL.get(name, DEFAULT_TTL)
    if hit and not refresh and hit[0] > time.time() - ttl:
        return hit[1], True
    try:
        value = fetch()
    except Exception as e:
        _warn(f"client {client_id}: section {name} failed: {type(e).__name__}: {str(e)[:200]}",
              client_id=client_id, section=name)
        return None, False
    _cache[(client_id, name)] = (time.time(), value)
    return value, True


def snapshot(client_id: int, refresh: bool = False) -> dict:
    """Never raises. Keys starting with "_" are for code only (for_model
    strips them): `_failed` lists the sections that could not be read."""
    state, failed = {"as_of": _now()}, []

    def take(name, fetch):
        value, ok = _section(client_id, name, fetch, refresh)
        if ok:
            state[name] = value
        else:
            failed.append(name)
        return value

    channels = take("channels", lambda: _channels(client_id))
    take("connections", lambda: _connections(client_id))
    take("tracking", lambda: _tracking(client_id, channels))
    take("google_campaigns", lambda: _google_campaigns(client_id))
    take("meta_campaigns", lambda: _meta_campaigns(client_id))
    if channels is not None:
        take("budget", lambda: _budget(client_id, channels))
        take("leads", lambda: _leads(client_id, channels))
    else:
        failed += ["budget", "leads"]
    take("approvals", lambda: _approvals(client_id))
    take("website", lambda: _website(client_id))
    take("landing_pages", lambda: _landing_pages(client_id))
    take("media", lambda: _media(client_id))
    take("media_folder", lambda: _media_folder(client_id))
    take("package", lambda: _package(client_id))
    take("site_security", lambda: _site_security(client_id))
    take("outreach", lambda: _outreach(client_id))
    take("creative_tools", lambda: _creative_tools(client_id))
    take("voice", lambda: _voice(client_id))
    from core.round2_flags import analytics_enabled
    if analytics_enabled():
        take("website_traffic", lambda: _website_traffic(client_id))
    state["_failed"] = failed
    return state


def _website_traffic(client_id: int) -> dict:
    """Visitors from the client's GA4 property (round 2, ANALYTICS_ENABLED) —
    figures AND trends, the trends computed in core/site_traffic, never by a
    model. A failed read raises so the section lands in _failed (a data
    topic), never as failure text in the prompt."""
    from core import site_traffic
    t = site_traffic.traffic(client_id)
    if t.get("error"):
        raise RuntimeError(f"traffic read failed: {t.get('reason')}")
    return site_traffic.for_agent(t)


def _creative_tools(client_id: int) -> dict:
    """Each creative tool on its own (core/vendor_tools): status, consent and
    the video provider choice. Never a key, not even its last 4 characters,
    and never the vendor's name: an agent repeats what it reads, so `label`
    here is the client-facing name (vendor_tools.client_label)."""
    from core import avatar_video, vendor_tools
    tools = {tool: {k: v for k, v in view.items() if k not in ("last4", "client_label")}
             | {"label": view.get("client_label") or ""}
             for tool, view in vendor_tools.statuses(client_id).items()}
    return {"tools": tools, "video_providers": avatar_video.providers_view(client_id)}


def _voice(client_id: int) -> dict:
    """The client's voice recordings (core/voice_recordings) — so an agent
    never answers "I can't see your recordings". Raises when the folder is
    unreadable: a data gap, never a fake 0."""
    from core import voice_recordings
    return voice_recordings.for_agent(client_id)


def _outreach(client_id: int) -> dict:
    """The Gmail agent's outreach (core/outreach_tracker.for_agent): counts,
    drafts waiting, and the businesses a follow-up may be PROPOSED for. Only
    for clients with the Gmail agent package."""
    from core import gmail_agent_plan
    if not gmail_agent_plan.entitled(client_id):
        return {"in_plan": False}
    from core import outreach_tracker
    return {"in_plan": True, **outreach_tracker.for_agent(client_id)}


def for_model(state: dict) -> dict:
    return {k: v for k, v in (state or {}).items() if not k.startswith("_")}


def failed_topics(state: dict) -> set:
    return {SECTION_TOPIC[s] for s in (state or {}).get("_failed", []) if s in SECTION_TOPIC}


def failed_sections(state: dict) -> list:
    return list((state or {}).get("_failed", []))
