"""The admin Control Room ("חדר בקרה") — one client at a time, from the
operator's side: what the agents did, where the money goes, what needs
Johnny, and a private line to that client's agent personas.

Almost everything here is REUSE, deliberately:
  - client list ............ admin_service.list_clients_admin
  - account/cost numbers ... admin_service.get_client_admin
  - money picture .......... budget_agent.get_financial_picture (paid side)
                             + its forecast (fixed organic package price)
  - alerts ................. the `alerts` table (agent_alert / alert())
  - feedback ............... engagement_agent.list_feedback
  - connection gaps ........ client_journey.connection_status
  - persona data ........... support_agent.PERSONAS[..]["reads"]

Two genuinely new things:

1. ALERT ↔ CLIENT matching. `alerts` has no client_id column — every agent
   writes "client {id}: …" into the issue text (house convention), so that is
   what we match on. A wording that breaks the convention simply won't show
   on the client's page; it still shows on the global alerts tab.

2. ADMIN ↔ AGENT CHAT. Johnny asks a persona about this client and gets an
   answer grounded in data fetched for THIS question, right now. Stored in
   `admin_agent_chat` — never in client_communications / client_activity,
   both of which reach the client (their chat history and their data export).
   No WhatsApp path touches it. Read-only by construction, exactly like the
   client-facing personas: the grounding below calls getters only, and the
   model's output is read for "reply" and "proposed_action" — the latter is
   only ever STORED as a proposal by core/agent_actions; Johnny's click on its
   card is what runs it, re-checked server-side.

How grounding works (why the agent doesn't guess): before the LLM call we
build ONE data block per source, each stamped with when it was fetched, and
every failed read becomes an explicit entry in `data_gaps` instead of a
silently missing field. The prompt forbids any number not present in the
block and tells the agent to name a gap rather than fill it.
"""
import json
import re
from datetime import datetime, timedelta, timezone

from core.agent_base import log_step, timed_step

AGENT_NAME = "control_room"

STALLED_DAYS = 7          # an active client with no agent activity this long is "stalled"
ACTIVITY_LIMIT = 40
CHAT_HISTORY_TURNS = 12
ALERT_SCAN_LIMIT = 500

# Agents whose work IS the organic side (content, SEO, site, social organic) —
# organic has no spend line, so its honest measure is work done.
ORGANIC_AGENTS = ("seo_agent", "website_agent", "media_agent", "meta_content_agent",
                  "youtube_content_agent", "tiktok_agent", "landing_page_agent",
                  "engagement_agent")

ADMIN_PERSONA_GENERAL = {
    "name": "תמונה כוללת",
    "domain": "the whole account — every platform, the money, the alerts and the client relationship",
    "voice": "An account director briefing the agency owner: concise, prioritised, no fluff.",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


# ─── Alerts tied to a client ─────────────────────────────────────────────────

def _client_pattern(client_id: int):
    # "client 12", "client #12", "client_id=12", "לקוח 12" — and NOT "client 120"
    return re.compile(rf"(?:client(?:_id)?\s*[#=:]?\s*|לקוח\s*#?){int(client_id)}(?!\d)", re.I)


def alerts_for_client(client_id: int, alerts: list = None, status: str = None) -> list:
    from core import admin_service
    rows = alerts if alerts is not None else admin_service.list_alerts(limit=ALERT_SCAN_LIMIT)
    pattern = _client_pattern(client_id)
    matched = [a for a in rows if any(pattern.search(str(i)) for i in (a.get("issues") or []))]
    return [a for a in matched if a.get("status") == status] if status else matched


# ─── Landing list ────────────────────────────────────────────────────────────

def _health(client: dict, open_alerts: int, unreviewed: int) -> str:
    if open_alerts or unreviewed:
        return "attention"
    if client.get("status") == "active":
        last = _parse(client.get("last_activity"))
        if not last or last < datetime.now(timezone.utc) - timedelta(days=STALLED_DAYS):
            return "stalled"
        return "ok"
    return "inactive"


def list_overview() -> list:
    """Every client with a quick-glance status. One alerts read and one
    feedback read for the whole list — never a query per client."""
    from core import admin_service
    clients = admin_service.list_clients_admin()
    try:
        open_alerts = admin_service.list_alerts(status="open", limit=ALERT_SCAN_LIMIT)
    except Exception as e:
        print(f"[{AGENT_NAME}] alerts unavailable for the list: {e}")
        open_alerts = []
    try:
        from agents.engagement_agent import list_feedback
        unreviewed = list_feedback(limit=500, only_unreviewed=True)
    except Exception as e:
        print(f"[{AGENT_NAME}] feedback unavailable for the list: {e}")
        unreviewed = []

    rows = []
    for c in clients:
        alert_count = len(alerts_for_client(c["id"], open_alerts))
        feedback_count = sum(1 for f in unreviewed if f.get("client_id") == c["id"])
        rows.append({
            "id": c["id"], "name": c.get("name", ""), "email": c.get("email", ""),
            "package": c.get("package", ""), "status": c.get("status", ""),
            "billing_exempt": c.get("billing_exempt", False),
            "platforms": c.get("platforms", []), "last_activity": c.get("last_activity"),
            "open_alerts": alert_count, "unreviewed_feedback": feedback_count,
            "health": _health(c, alert_count, feedback_count),
        })
    # Attention first, then stalled — the list is a triage queue
    order = {"attention": 0, "stalled": 1, "ok": 2, "inactive": 3}
    rows.sort(key=lambda r: (order.get(r["health"], 9), -(r["open_alerts"] + r["unreviewed_feedback"])))
    return rows


# ─── One client's view ───────────────────────────────────────────────────────

def _trim_activity(rows: list) -> list:
    out = []
    for a in rows:
        details = json.dumps(a.get("details") or {}, ensure_ascii=False, default=str)
        out.append({"agent_name": a.get("agent_name"), "action_type": a.get("action_type"),
                    "created_at": a.get("created_at"), "details": details[:300]})
    return out


def client_view(client_id: int) -> dict:
    """Everything except the money picture (live ad-API calls — its own lazy
    endpoint, budget_split). {} when the client doesn't exist."""
    from agents.client_agent import get_activity
    from core import admin_service

    admin = admin_service.get_client_admin(client_id)
    if not admin:
        return {}
    client = admin["client"]
    activity = get_activity(client_id, limit=ACTIVITY_LIMIT)

    from agents.engagement_agent import list_feedback
    from agents.seo_agent import get_pending_strategy
    from core import client_journey

    # Each section degrades on its own — one broken read must not blank the page
    gaps = []

    def section(name, fn, fallback):
        try:
            return fn()
        except Exception as e:
            gaps.append(f"{name}: {e}")
            return fallback

    client_alerts = section("alerts", lambda: alerts_for_client(client_id), [])
    feedback = section("feedback", lambda: list_feedback(limit=50, client_id=client_id), [])
    connections = section("connections", lambda: client_journey.connection_status(client_id), None)
    seo_pending = section("seo strategy", lambda: bool(
        (get_pending_strategy(client_id) or {}).get("awaiting_approval")), False)

    def google_ads_account():
        # The STORED row (any status) plus who chose it — DB reads, no Google
        # call. The live list of reachable accounts loads only when the admin
        # asks to change it.
        from agents.google_ads_agent import connection_info
        info = connection_info(client_id)
        if not info:
            return {}
        return {"connected": info["status"] == "active" and bool(info["customer_id"]),
                "status": info["status"], "customer_id": info["display_id"],
                "name": info.get("name", ""), "chosen_by": info.get("chosen_by", ""),
                "chosen_at": info.get("chosen_at", "")}
    google_ads = section("google ads account", google_ads_account, {})

    def gtm():
        # Same view the client's card shows (core/gtm_connection) + when the
        # stored grant last worked. Never a token.
        from core import gtm_connection
        view = gtm_connection.connection_view(client_id)
        if not view.get("connected"):
            return {}
        return {**view, "health": gtm_connection.health(client_id)}
    gtm_view = section("tag manager", gtm, {})

    def open_actions():
        from core import agent_actions
        return agent_actions.open_cards(client_id, actor="admin")
    actions = section("agent actions", open_actions, [])

    open_alerts = [a for a in client_alerts if a.get("status") == "open"]
    unreviewed = [f for f in feedback if not f.get("reviewed")]
    last = _parse(activity[0]["created_at"]) if activity else None
    stalled = (client.get("status") == "active"
               and (not last or last < datetime.now(timezone.utc) - timedelta(days=STALLED_DAYS)))

    # "Needs your attention" — every item names where it came from and what to do
    attention = [{"kind": "alert", "id": a["id"], "text": " / ".join(map(str, a.get("issues") or [])),
                  "source": a.get("source", ""), "at": a.get("created_at")} for a in open_alerts]
    attention += [{"kind": "feedback", "id": f["id"],
                   "text": (f"דירוג {f['rating']}/5 — " if f.get("rating") else "") + (f.get("message") or ""),
                   "source": f.get("source", ""), "at": f.get("created_at")} for f in unreviewed]
    if connections and connections.get("missing"):
        attention.append({"kind": "connections", "text": "חיבורים חסרים: " + ", ".join(connections["missing"]),
                          "source": "client_journey", "at": None})
    if seo_pending:
        attention.append({"kind": "seo_strategy", "text": "אסטרטגיית SEO ממתינה לאישור שלך",
                          "source": "seo_agent", "at": None})
    attention += [{"kind": "action_request", "id": a["id"],
                   "text": f"הלקוח ביקש בצ'אט: {a['title']} — לאישור שלך בכרטיס הפעולה",
                   "source": "agent_actions", "at": a.get("created_at")}
                  for a in actions if a.get("status") == "awaiting_staff"]
    if stalled:
        attention.append({"kind": "stalled",
                          "text": f"אין פעילות סוכנים {STALLED_DAYS}+ ימים" if last else "אין פעילות סוכנים בכלל",
                          "source": "client_activity", "at": activity[0]["created_at"] if activity else None})

    return {
        "client": client,
        "monthly_fee": admin.get("monthly_fee"), "cost_month": admin.get("cost_month"),
        "margin_pct": admin.get("margin_pct"), "next_billing": admin.get("next_billing"),
        "activity": _trim_activity(activity),
        "alerts": client_alerts[:30],
        "feedback": feedback,
        "connections": connections,
        "google_ads": google_ads,
        "gtm": gtm_view,
        "actions": actions,
        "attention": attention,
        "stalled": stalled,
        "data_gaps": gaps,
        "personas": admin_personas(),
    }


def budget_split(client_id: int) -> dict:
    """Organic vs paid, each side honest about what it can actually show.

    PAID has real money: the client's stated monthly ad budget (onboarding
    answers, parsed) against live 30-day spend per platform.
    ORGANIC has no spend line anywhere in the system — it is our management
    work, not an ad account. So it shows the fixed organic package price
    plus the measurable proxy: agent work done in the last 30 days."""
    from agents.budget_agent import get_financial_picture
    from agents.client_agent import get_activity
    from agents.onboarding_agent import ORGANIC_MONTHLY_PRICE_NIS

    picture = get_financial_picture(client_id)
    if not picture.get("available"):
        return {"available": False}
    forecast = picture.get("forecast") or {}
    ad_spend = picture.get("ad_spend") or {}

    def platform(block: dict) -> dict:
        if not block.get("connected"):
            return {"connected": False}
        if block.get("error"):
            return {"connected": True, "error": str(block["error"])[:200]}
        totals = block.get("totals") or {}
        return {"connected": True, "cost_30d_ils": totals.get("cost", 0),
                "conversions_30d": totals.get("conversions"), "clicks_30d": totals.get("clicks")}

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    organic_work = {}
    for a in get_activity(client_id, limit=300):
        when = _parse(a.get("created_at"))
        if a.get("agent_name") in ORGANIC_AGENTS and when and when >= cutoff:
            organic_work[a["agent_name"]] = organic_work.get(a["agent_name"], 0) + 1

    return {
        "available": True,
        "generated_at": picture.get("generated_at"),
        "paid": {
            "stated_monthly_budget_ils": forecast.get("stated_marketing_budget_ils"),
            "stated_text": forecast.get("stated_marketing_budget_text", ""),
            "actual_30d_total_ils": ad_spend.get("total_ils", 0),
            "google_ads": platform(ad_spend.get("google_ads") or {}),
            "meta_ads": platform(ad_spend.get("meta_ads") or {}),
            "vs_budget": picture.get("ad_spend_vs_forecast"),
            "cross_platform": picture.get("cross_platform_comparison"),
        },
        "organic": {
            "package_monthly_price_ils": ORGANIC_MONTHLY_PRICE_NIS,
            "work_last_30d": organic_work,
            "note": "Organic has no spend line in the system — this is the fixed package price plus work done.",
        },
        "ours": picture.get("our_revenue_and_cost"),
        "forecast_source": forecast.get("source", ""),
    }


# ─── Admin ↔ agent chat ──────────────────────────────────────────────────────

def admin_personas() -> list:
    from agents.support_agent import PERSONAS
    return ([{"id": "general", "name": ADMIN_PERSONA_GENERAL["name"]}]
            + [{"id": pid, "name": p["name"]} for pid, p in PERSONAS.items()])


def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def chat_history(client_id: int, persona: str, limit: int = 100) -> dict:
    """{"available": bool, "messages": [...]} — available False until the
    migration has run; the chat still works then, it just doesn't persist."""
    try:
        rows = (_db().table("admin_agent_chat").select("role,content,created_at")
                .eq("client_id", client_id).eq("persona", persona)
                .order("created_at", desc=True).limit(limit).execute().data or [])
        return {"available": True, "messages": list(reversed(rows))}
    except Exception as e:
        print(f"[{AGENT_NAME}] admin_agent_chat unavailable: {e}")
        return {"available": False, "messages": []}


def _store(client_id: int, persona: str, role: str, content: str) -> bool:
    from core.redaction import redact
    try:
        # Internal chat too: likely keys/passwords are stored as [REDACTED]
        _db().table("admin_agent_chat").insert({"client_id": client_id, "persona": persona,
                                                "role": role, "content": redact(content)}).execute()
        return True
    except Exception as e:
        print(f"[{AGENT_NAME}] could not store admin chat turn: {e}")
        return False


def _grounding(client_id: int, persona_id: str) -> dict:
    """The data block for ONE admin question, fetched now. Each source is
    tried on its own; a failure becomes a named gap, never a silent hole the
    model could fill with a guess."""
    from agents.client_agent import get_activity
    from agents.engagement_agent import list_feedback
    from agents.support_agent import PERSONAS, _latest_lead
    from core import admin_service, client_journey

    data, gaps = {"data_fetched_at": _now()}, []

    def attempt(name, fn):
        try:
            data[name] = fn()
        except Exception as e:
            gaps.append(f"{name}: {type(e).__name__}: {str(e)[:160]}")

    admin = admin_service.get_client_admin(client_id)
    client = admin.get("client") or {}
    data["client"] = {k: client.get(k) for k in ("id", "name", "business_name", "package", "status",
                                                 "created_at", "billing_exempt", "language")}
    data["our_numbers"] = {"monthly_fee_ils": admin.get("monthly_fee"),
                           "internal_cost_month_ils": admin.get("cost_month"),
                           "cost_by_category": admin.get("cost_by_category"),
                           "margin_pct": admin.get("margin_pct")}
    attempt("proposal", lambda: (_latest_lead(client_id).get("proposal") or {}))
    attempt("recent_activity", lambda: _trim_activity(get_activity(client_id, limit=30)))
    attempt("open_alerts", lambda: [{"issues": a.get("issues"), "source": a.get("source"),
                                     "at": a.get("created_at")}
                                    for a in alerts_for_client(client_id, status="open")[:15]])
    attempt("connections", lambda: client_journey.connection_status(client_id))
    attempt("unreviewed_feedback", lambda: [
        {"rating": f.get("rating"), "message": (f.get("message") or "")[:400], "at": f.get("created_at")}
        for f in list_feedback(limit=10, only_unreviewed=True, client_id=client_id)])

    persona_ids = list(PERSONAS) if persona_id == "general" else [persona_id]
    for pid in persona_ids:
        try:
            for key, value in (PERSONAS[pid]["reads"](client_id) or {}).items():
                data[key] = value
                if isinstance(value, dict) and value.get("error"):
                    gaps.append(f"{key}: read returned an error — {str(value['error'])[:160]}")
        except Exception as e:
            gaps.append(f"{pid} platform reads: {type(e).__name__}: {str(e)[:160]}")
    if persona_id in ("general", "google", "meta"):
        attempt("money", lambda: budget_split(client_id))

    # The same snapshot the client's agents answer from (core/client_state),
    # plus what may be proposed from here (core/agent_actions). `_state` is
    # code-only: ask_agent pops it before the model sees DATA.
    try:
        from core import agent_actions, client_state
        state = client_state.snapshot(client_id)
        data["account_state"] = client_state.for_model(state)
        gaps.extend(f"account_state.{s}: read failed just now" for s in client_state.failed_sections(state))
        data["available_actions"] = agent_actions.available(client_id, "admin_chat", state, client=client)
        data["_state"] = state
    except Exception as e:
        gaps.append(f"account_state: {type(e).__name__}: {str(e)[:160]}")
        data["available_actions"] = []

    data["data_gaps"] = gaps
    return data


ADMIN_AGENT_SYSTEM = """You are {name}, uallak's {domain} specialist — but right now you are NOT
talking to the client. You are talking to Johnny, uallak's owner and operator, inside his private
admin Control Room, about ONE client. The client never sees this conversation.

So speak to him as a colleague: direct and technical. Use the real metric names, campaign/
ad-set/page names, statuses, error texts and numbers from the data. Lead with what matters
(problems, risks, money), then the detail. Recommend concrete next actions for HIM. No sales
warmth, no reassurance padding. Voice: {voice}

GROUNDING — THE ONE HARD RULE: every fact and number you state must be in DATA below, which was
fetched for this question at data_fetched_at. Never estimate, extrapolate or "typically" a number
for this client. If the answer needs something DATA doesn't have, say exactly what is missing
(check data_gaps — those reads FAILED just now, name them as failures, not as zeros). If a field
is absent because a platform isn't connected, say it's not connected. Quote the time window a
number covers (most ad numbers are the last 30 days).

ACCOUNT STATE: DATA.account_state is the same snapshot the client's own agents answer from —
connections and their health, website tracking (container, match with the site snippet, missing
permissions, lead event live in the published version), campaigns, spend vs stated budget,
leads/CRM, pending approvals, site, landing pages, media usage. Answer from it exactly.

ACTIONS — YOU PROPOSE, JOHNNY CLICKS. You run nothing yourself. DATA.available_actions lists what
can be run for this client from here (name, what it does, params). When Johnny asks for one of
them ("create the test campaign", "upload the test video", "set up conversion tracking"), fill
"proposed_action": "name" copied exactly from available_actions, "params" only the params listed
for it (values from DATA — never invented; leave out what you don't have), "summary" one line of
what will happen. The Control Room then shows a confirmation card; only his click runs it, and the
server re-checks every guard (internal test client, PAUSED, Private, container match, expired
connection, already live). In "reply", say what the card will do and anything in DATA that will
make it refuse (e.g. the site loads a different container). If what he asks is not in
available_actions, say so and why from DATA (not an internal test client, not connected, already
live) — never pretend it ran, never invent a way around it. Pausing campaigns, budgets and
messaging the client are not actions here: tell him where he does those.

Reply in Hebrew unless Johnny writes in another language. Plain text, short paragraphs or short
bullet lines, no markdown headers.

Return JSON only: {{"reply": "text", "proposed_action": {{"name": "", "params": {{}}, "summary": ""}}}}"""


def ask_agent(client_id: int, persona_id: str, message: str) -> dict:
    """One admin question to one persona about one client. Never raises; on
    failure Johnny gets the actual reason (he is the operator — unlike the
    client-facing chats, hiding the error here would help no one)."""
    from agents.support_agent import PERSONAS
    from core.claude_json import safe_claude_json_call

    persona_id = (persona_id or "general").strip()
    if persona_id != "general" and persona_id not in PERSONAS:
        return {"success": False, "code": "ERR_UNKNOWN_PERSONA"}
    message = (message or "").strip()[:4000]
    if not message:
        return {"success": False, "code": "ERR_EMPTY_MESSAGE"}

    persona = ADMIN_PERSONA_GENERAL if persona_id == "general" else PERSONAS[persona_id]
    history = chat_history(client_id, persona_id, limit=CHAT_HISTORY_TURNS * 2)
    stored = _store(client_id, persona_id, "admin", message)

    log_step(AGENT_NAME, "ask_agent", f"client {client_id} persona={persona_id}")
    try:
        grounding = timed_step(AGENT_NAME, "grounding", lambda: _grounding(client_id, persona_id))
        state = grounding.pop("_state", None)
        system = ADMIN_AGENT_SYSTEM.format(name=persona["name"], domain=persona["domain"],
                                           voice=persona["voice"])
        payload = {"conversation_so_far": [{"from": "johnny" if m["role"] == "admin" else "you",
                                            "text": m["content"][:1500]}
                                           for m in history["messages"][-CHAT_HISTORY_TURNS:]],
                   "johnny_asks": message,
                   "DATA": grounding}
        if persona_id != "general":
            payload["your_data_notes"] = PERSONAS[persona_id].get("data_notes", "")
        result = timed_step(AGENT_NAME, "llm", lambda: safe_claude_json_call(
            system, json.dumps(payload, ensure_ascii=False, default=str),
            max_tokens=1200, client_id=client_id, cost_category="claude_admin_chat"))
        reply = (result.get("reply") or "").strip()
        if not reply:
            raise RuntimeError("model returned an empty reply")
    except Exception as e:
        log_step(AGENT_NAME, "ask_agent", f"client {client_id}: failed — {type(e).__name__}: {e}")
        reply = f"לא הצלחתי לענות הפעם ({type(e).__name__}: {str(e)[:200]}). אפשר לשאול שוב."
        _store(client_id, persona_id, "agent", reply)
        return {"success": True, "reply": reply, "degraded": True, "persisted": stored,
                "data_gaps": []}

    # A proposed action becomes a stored proposal + card; nothing runs until
    # Johnny clicks it (core/agent_actions re-checks every guard then)
    action, refused = None, ""
    raw = result.get("proposed_action")
    if isinstance(raw, dict) and str(raw.get("name") or "").strip():
        from core import agent_actions
        proposal = agent_actions.propose(client_id, "admin_chat", persona_id, raw, state=state)
        action = proposal.get("card")
        if not action:
            refused = proposal.get("error") or "unknown"
            reply += f"\n\n(לא נוצר כרטיס פעולה ל-{str(raw.get('name'))[:60]}: {refused})"

    _store(client_id, persona_id, "agent", reply)
    return {"success": True, "reply": reply, "persisted": stored, "action": action,
            "data_gaps": grounding.get("data_gaps", []),
            "data_fetched_at": grounding.get("data_fetched_at")}
