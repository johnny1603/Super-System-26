import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import (HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse,
                               StreamingResponse)
from pydantic import BaseModel

# Before ANY Supabase client exists: short retry for dropped Supabase
# connections (httpx RemoteProtocolError / ConnectionTerminated), every module
from core import db_retry
db_retry.install()
# ...and, on that same choke point, no new rows for a client being deleted
from core import deletion_guard
deletion_guard.install()

from supabase import create_client as _supabase_create_client, Client

from agents.keys_agent import inject_all_keys, validate_keys
inject_all_keys()
validate_keys()

from agents.onboarding_agent import run_full_onboarding
from agents.master_agent import alert
from agents.monitor_agent import run_deep_scan
from agents.architect_agent import (
    is_agent_active, create_new_agent, suspend_agent, propose_agent_deletion
)
from agents.client_agent import (
    create_client, get_client, get_client_by_email, list_clients, update_client_status, update_client_package,
    complete_onboarding,
    add_account, get_accounts, upsert_account, remove_accounts,
    assign_agent, get_client_agents, update_agent_status,
    log_activity, get_activity,
    log_communication, get_communications,
    create_login_code, get_active_login_code, increment_login_code_attempts, mark_login_code_used,
    public_client,
)
from core.email_service import (
    send_client_report, send_admin_alert, send_payment_confirmation, send_login_code,
    send_account_closed, send_account_transferred,
)
from core.paypal_service import (
    create_subscription, verify_webhook_signature, get_subscription_status,
    get_plan, create_plan, revise_subscription_plan, list_subscription_transactions,
    create_invoice, cancel_subscription,
)
from core.session import (
    create_session_token, verify_session_token,
    create_oauth_state_token, verify_oauth_state_token,
    create_admin_session_token, verify_admin_session_token,
    create_lead_capture_token, verify_lead_capture_token,
    session_token_version, create_google_login_state, verify_google_login_state,
    create_google_link_token, verify_google_link_token,
    create_wp_connect_state, verify_wp_connect_state,
    create_admin_view_token, verify_admin_view_token, admin_session_fingerprint,
)
from core import admin_view
from core import client_auth
from core import google_ads_service
from core import meta_service
from core import tiktok_service
from core import admin_service
from core import drive_service
from core import lead_tracking
from core import client_leads as client_leads_service
from core import export_service

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    db: Client = _supabase_create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_KEY"],
    )
except Exception as _e:
    print(f"FATAL: Supabase failed to initialize — {_e}")
    raise

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# StaticFiles sends Last-Modified + ETag but no Cache-Control, so browsers
# apply HEURISTIC freshness (10% of the file's age) and reuse an old page for
# hours without asking us — after a deploy the admin kept running a /admin/
# that predated the Control Room demo buttons. "no-cache" = revalidate on
# every load; the ETag makes that a cheap 304 when nothing changed.
@app.middleware("http")
async def _revalidate_pages(request: Request, call_next):
    response = await call_next(request)
    if ("cache-control" not in response.headers
            and (response.headers.get("content-type", "").startswith("text/html")
                 or request.url.path.startswith("/assets/"))):
        response.headers["Cache-Control"] = "no-cache"
    return response

app.mount("/chat", StaticFiles(directory=os.path.join(BASE_DIR, "dashboard", "onboarding"), html=True), name="chat")
app.mount("/terms", StaticFiles(directory=os.path.join(BASE_DIR, "dashboard", "terms"), html=True), name="terms")
# Public, no login: the privacy policy URL on the Google OAuth consent screen
# (English at /privacy/, Hebrew at /privacy/he/). Scope disclosures must match
# docs/GOOGLE-SCOPES.md.
app.mount("/privacy", StaticFiles(directory=os.path.join(BASE_DIR, "dashboard", "privacy"), html=True), name="privacy")
app.mount("/dashboard", StaticFiles(directory=os.path.join(BASE_DIR, "dashboard", "client"), html=True), name="client_dashboard")
app.mount("/login", StaticFiles(directory=os.path.join(BASE_DIR, "dashboard", "login"), html=True), name="login")
app.mount("/admin", StaticFiles(directory=os.path.join(BASE_DIR, "dashboard", "admin"), html=True), name="admin")
app.mount("/profile", StaticFiles(directory=os.path.join(BASE_DIR, "dashboard", "profile"), html=True), name="profile")
# Shared static assets (i18n engine etc.) used across the per-page mounts
app.mount("/assets", StaticFiles(directory=os.path.join(BASE_DIR, "dashboard", "assets")), name="assets")

# Starlette's Mount("/login") only matches paths UNDER the mount ("/login/...").
# The bare "/login" falls through every mount and API route to the root "/"
# landing mount (registered last), which looks up a "login" file that doesn't
# exist and 404s - before the router's redirect_slashes fallback ever gets a
# chance. Same family as the earlier /admin and /terms trailing-slash 404s;
# fixed here for every mounted page. /login matters most - it's the exact link
# in the payment-confirmation and welcome emails.
def _bare_path_redirect(page: str):
    async def _redirect(request: Request):
        query = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(url=f"{page}/{query}")
    return _redirect

for _page in ("/chat", "/terms", "/privacy", "/dashboard", "/login", "/admin", "/profile"):
    app.get(_page, include_in_schema=False)(_bare_path_redirect(_page))

def _require_admin_key(request: Request):
    """Guards internal/admin endpoints - everything that isn't part of the public
    sales chat, login, PayPal callback flow, or a client's own session-gated
    dashboard/chat. Fails closed if ADMIN_KEY isn't set."""
    admin_key = os.environ.get("ADMIN_KEY", "")
    if not admin_key or request.headers.get("X-Admin-Key") != admin_key:
        raise HTTPException(status_code=401, detail="Not authorized")

_admin_only = [Depends(_require_admin_key)]

class OnboardingRequest(BaseModel):
    answers: dict
    client_email: str = ""
    client_name: str = ""
    language: str = "he"  # sales-chat page's active uallakI18n language - no client row
                          # exists yet at proposal time, so this rides along explicitly
    lead_id: str = ""     # the lead the chat opened on arrival (core/lead_tracking); empty
                          # when tracking never started, which must not lose the proposal

def _sales_cost_owner(lead_id: str):
    """Pre-signup model cost belongs to the chat session: its lead id, or an
    anonymous session id when tracking never started (core/cost_tracker; the
    lead's costs move to the client on conversion)."""
    from core.cost_tracker import cost_owner
    import uuid as _uuid
    return cost_owner(lead_id=(lead_id or "").strip() or f"anon-{_uuid.uuid4().hex[:12]}")

@app.post("/api/onboarding")
def onboarding(req: OnboardingRequest):
    # The answers are stored on the lead row and sent to the models: a key or
    # password typed into any answer is [REDACTED] first (core/redaction)
    from core.redaction import redact_obj
    req.answers = redact_obj(req.answers)
    try:
        with _sales_cost_owner(req.lead_id):
            result = run_full_onboarding(req.answers)
        proposal = result["proposal"]
        review = result.get("review", {})
        if is_agent_active("master_agent") and not review.get("approved", True):
            alert("proposal", review.get("issues", []))

        # שמירה ב-DB — נשמור את המסלול הזול ביותר כבסיס להשוואה בטבלה
        packages = proposal.get("packages", [])
        cheapest = min(packages, key=lambda pkg: pkg.get("monthly_management_total", 0)) if packages else {}
        lead_fields = {
            "client_email": req.client_email,
            "client_name": req.client_name,
            "answers": req.answers,
            "proposal": proposal,
            "approved": bool(proposal.get("approved")),
            "setup_fee": cheapest.get("setup_fee_total", 0),
            "monthly_fee": cheapest.get("monthly_management_total", 0),
            "language": req.language,
        }
        # The chat already opened this lead on arrival, carrying its traffic
        # source — fold the proposal into THAT row so attribution stays attached.
        # The insert fallback matters: a blocked/failed /api/lead/start must cost
        # us the attribution, never the proposal itself.
        try:
            attached = lead_tracking.attach_proposal(req.lead_id, lead_fields)
        except Exception as lead_err:
            # Caught, not propagated: without this the fallback below is
            # unreachable on the exact failure it exists for.
            print(f"[lead] attach_proposal failed (non-fatal): {lead_err}")
            attached = False
        if not attached:
            db.table("leads").insert({
                "created_at": datetime.now().isoformat(), **lead_fields,
            }).execute()

        # שליחת מיילים
        send_admin_alert(req.answers, proposal)
        if req.client_email:
            send_client_report(req.client_email, req.client_name, proposal, req.language)

        return {"success": True, "data": result}
    except Exception as e:
        import traceback
        traceback.print_exc()
        # The prospect just answered every question and waited minutes. Before
        # this, nobody was told and the chat sat on a dead "something went
        # wrong". Now: the team hears about it with the lead id (their answers
        # are on the lead row via /api/lead/message), and the page offers a
        # retry with the same answers (dashboard/onboarding fetchProposal).
        from core import chat_fallback
        chat_fallback.alert_handoff(
            chat_fallback.KIND_TECHNICAL, "onboarding_agent",
            f"proposal build failed for a prospect who finished the sales chat "
            f"({type(e).__name__}: {str(e)[:200]}) — reach out if they don't retry. "
            f"Email: {req.client_email or 'not given yet'}.",
            persona="sales", lead_id=req.lead_id, issue_key="technical:sales:proposal")
        raise HTTPException(status_code=503, detail={"code": "ERR_PROPOSAL_UNAVAILABLE"})

@app.get("/api/leads", dependencies=_admin_only)
async def get_leads():
    result = db.table("leads").select("*").order("created_at", desc=True).execute()
    return {"leads": result.data}

# ─── Lead tracking (public — same tier as the sales chat these come from) ────
# Opening the lead row on ARRIVAL rather than at proposal time is the whole
# point: it is what makes traffic source, first-contact time and drop-off
# knowable at all. Both endpoints swallow their own failures — a tracking
# problem must never take the sales chat down with it.

class LeadStartRequest(BaseModel):
    attribution: dict = {}   # utm_*, click ids, referrer, landing_path — all browser-supplied
    language: str = "he"

@app.post("/api/lead/start")
def lead_start(req: LeadStartRequest):
    try:
        return {"success": True, "data": lead_tracking.start_lead(req.attribution, req.language)}
    except Exception as e:
        print(f"[lead] start failed (non-fatal): {e}")
        return {"success": False, "data": {"lead_id": ""}}

class LeadMessageRequest(BaseModel):
    lead_id: str
    role: str        # user | assistant
    content: str

@app.post("/api/lead/message")
def lead_message(req: LeadMessageRequest):
    try:
        return lead_tracking.record_message(req.lead_id, req.role, req.content)
    except Exception as e:
        print(f"[lead] message failed (non-fatal): {e}")
        return {"success": False}

@app.get("/api/leads/volume-scan", dependencies=_admin_only)
def leads_volume_scan():
    # Daily scheduler job: refresh every active client's lead volume + tier,
    # alert once on a first-time tier crossing, and check uallak's own Supabase
    # footprint. Plain `def` — it walks every client and hits cached ad APIs.
    from core.lead_volume import run_volume_scan
    try:
        return {"success": True, "data": run_volume_scan()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/monitor/scan", dependencies=_admin_only)
def monitor_scan():
    if not is_agent_active("monitor_agent"):
        return {"success": False, "skipped": True, "reason": "monitor_agent is suspended"}
    try:
        report = run_deep_scan()
        return {"success": True, "data": report}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class CreateAgentRequest(BaseModel):
    need_description: str

class SuspendAgentRequest(BaseModel):
    agent_name: str
    reason: str

class ProposeDeleteRequest(BaseModel):
    agent_name: str
    reason: str

@app.post("/api/architect/create", dependencies=_admin_only)
def architect_create(req: CreateAgentRequest):
    try:
        result = create_new_agent(req.need_description)
        return {"success": True, "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/architect/suspend", dependencies=_admin_only)
async def architect_suspend(req: SuspendAgentRequest):
    result = suspend_agent(req.agent_name, req.reason)
    return {"success": True, "data": result}

@app.post("/api/architect/propose-deletion", dependencies=_admin_only)
async def architect_propose_deletion(req: ProposeDeleteRequest):
    result = propose_agent_deletion(req.agent_name, req.reason)
    return {"success": True, "data": result}

# ─── Checkout ─────────────────────────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    client_name: str
    client_email: str = ""
    client_phone: str = ""
    client_address: str = ""
    business_name: str = ""
    business_tax_id: str = ""
    package_id: str = ""
    package_name: str = ""
    setup_fee_total: int = 0
    monthly_management_total: int = 0
    language: str = "he"  # the sales-chat page's active uallakI18n language at checkout time
    lead_id: str = ""     # exact lead->client link, replacing the guess below

@app.post("/api/checkout")
async def checkout(req: CheckoutRequest):
    try:
        client = create_client(req.client_name, req.client_email, req.client_phone, req.package_name,
                                req.client_address, req.business_name, req.business_tax_id,
                                req.language)
        client_id = client["id"]
        update_client_status(client_id, "pending_payment")

        # Link the lead to the client it became. lead_id makes this exact — it is
        # the chat session's own key, so concurrent chats can no longer be
        # confused for each other.
        #
        # The old newest-contactless-lead guess survives ONLY as the fallback for
        # a browser that never got a lead row (blocked request, /api/lead/start
        # failure). It is wrong under concurrency, which is exactly why it is now
        # the second choice rather than the only one.
        try:
            linked = lead_tracking.mark_converted(req.lead_id, client_id,
                                                  req.client_email, req.client_name)
            if not linked:
                recent_lead = (
                    db.table("leads").select("id")
                    .eq("client_email", "")
                    .order("created_at", desc=True)
                    .limit(1)
                    .execute()
                )
                if recent_lead.data:
                    db.table("leads").update({
                        "client_email": req.client_email,
                        "client_name": req.client_name,
                        "client_id": client_id,
                        "status": "converted",
                        "converted_at": datetime.now(timezone.utc).isoformat(),
                    }).eq("id", recent_lead.data[0]["id"]).execute()
        except Exception as backfill_err:
            print(f"[checkout] lead backfill failed (non-fatal): {backfill_err}")

        plan_name = f"uallak ניהול חודשי — {req.package_name}" if req.package_name else "uallak ניהול חודשי"
        subscription = create_subscription(
            client_id=client_id,
            plan_name=plan_name,
            amount=req.monthly_management_total,
            currency="ILS",
            setup_fee=req.setup_fee_total,
        )

        log_activity(client_id, "paypal_service", "subscription_created", {
            "package_id": req.package_id,
            "package_name": req.package_name,
            "setup_fee_total": req.setup_fee_total,
            "monthly_management_total": req.monthly_management_total,
        }, subscription)

        return {"success": True, "data": {
            "client_id": client_id,
            "approve_url": subscription["approve_url"],
            "subscription_id": subscription["subscription_id"],
        }}
    except Exception as e:
        import traceback
        traceback.print_exc()
        # A prospect who chose a package and wants to PAY hit a wall. The page
        # tells them the team will reach out (no automatic retry: every attempt
        # creates a client row) - and this alert is what makes that true.
        from core import chat_fallback
        chat_fallback.alert_handoff(
            chat_fallback.KIND_TECHNICAL, "paypal_service",
            f"CHECKOUT FAILED for a ready-to-pay prospect ({type(e).__name__}: {str(e)[:200]}) — "
            f"contact them: {req.client_name}, {req.client_phone}, {req.client_email}, "
            f"package '{req.package_name}' (₪{req.setup_fee_total} setup + "
            f"₪{req.monthly_management_total}/mo).",
            persona="sales", lead_id=req.lead_id,
            issue_key=f"technical:sales:checkout:{chat_fallback.question_key(req.client_email or req.client_phone)}")
        raise HTTPException(status_code=500, detail={"code": "ERR_CHECKOUT_FAILED"})

def _mark_paid_and_notify(client_id: int, subscription_id: str = None, source: str = "unknown"):
    """Flip a client to active and send the payment confirmation email - idempotent, so it's
    safe to call from both the browser redirect (/api/payment-success, immediate but not
    verifiable) and the PayPal webhook (authoritative once signature verification is set up),
    without sending the client a duplicate email."""
    client = get_client(client_id)
    if not client:
        print(f"[{source}] client {client_id} not found")
        return
    if client.get("status") == "active":
        print(f"[{source}] client {client_id} already active - skipping duplicate notification")
        return
    update_client_status(client_id, "active")
    log_activity(client_id, "paypal_service", "payment_confirmed", {"subscription_id": subscription_id, "source": source}, {})
    if client.get("email"):
        send_payment_confirmation(client["email"], client.get("name", ""), client_id)

    # Setup fee amount/package only live on the subscription_created activity row
    # (see admin_service._fee_map) - there is no dedicated billing table.
    sub_created = next(
        (a for a in get_activity(client_id, limit=50) if a.get("action_type") == "subscription_created"),
        None,
    )
    details = (sub_created or {}).get("details", {})
    setup_fee = details.get("setup_fee_total") or 0
    package_name = details.get("package_name") or client.get("package", "")
    if setup_fee:
        try:
            invoice = create_invoice(
                client_id=client_id,
                amount=setup_fee,
                description=f"uallak — דמי הקמה{f' ({package_name})' if package_name else ''}",
                client_name=client.get("name", ""),
                client_email=client.get("email", ""),
                address=client.get("address", ""),
                business_name=client.get("business_name", ""),
                business_tax_id=client.get("business_tax_id", ""),
            )
            log_activity(client_id, "paypal_service", "invoice_sent", {"setup_fee": setup_fee}, invoice)
        except Exception as invoice_err:
            print(f"[{source}] invoice creation failed for client {client_id} (non-fatal): {invoice_err}")
            log_activity(client_id, "paypal_service", "invoice_failed", {"error": str(invoice_err)}, {})

    print(f"[{source}] client {client_id} marked active, confirmation email sent")

@app.get("/api/payment-success")
def payment_success(client_id: int, subscription_id: str = None):
    try:
        if not subscription_id:
            print(f"[payment_success] client {client_id}: no subscription_id in redirect - not activating")
            return RedirectResponse(url="/chat/?payment=pending")

        sub_status = get_subscription_status(subscription_id)
        if sub_status.get("status") != "ACTIVE":
            print(f"[payment_success] client {client_id}: subscription {subscription_id} "
                  f"status={sub_status.get('status')} - not activating yet")
            return RedirectResponse(url="/chat/?payment=pending")

        _mark_paid_and_notify(client_id, subscription_id, source="payment_success_redirect")
        return RedirectResponse(url="/chat/?payment=success")
    except Exception as e:
        import traceback
        traceback.print_exc()
        return RedirectResponse(url="/chat/?payment=error")

_INVOICE_NUMBER_RE = re.compile(r"^INV-(\d+)-\d+$")

def _client_id_from_invoice_number(invoice_number: str):
    """Recover the client id from our own invoice_number scheme
    (f"INV-{client_id}-{timestamp}", see paypal_service.create_invoice) —
    invoice webhook resources carry no custom_id/custom field the way
    subscription resources do, so the number itself IS the correlation key."""
    match = _INVOICE_NUMBER_RE.match((invoice_number or "").strip())
    return int(match.group(1)) if match else None


def _invoice_reconciliation_state(client_id: int, invoice_number: str) -> str:
    """'paid' | 'cancelled' | 'unresolved' — the newest matching event for
    this invoice_number among this client's own activity. Append-only, same
    idiom as every other status derivation in this codebase (newest-row-
    wins, e.g. _client_subscription_info) — no in-place update of the
    original invoice_sent row; a new event row IS the status update here."""
    for entry in get_activity(client_id, limit=200):
        if entry.get("agent_name") != "paypal_service":
            continue
        if (entry.get("details") or {}).get("invoice_number") != invoice_number:
            continue
        action = entry.get("action_type")
        if action == "invoice_paid":
            return "paid"
        if action == "invoice_cancelled":
            return "cancelled"
    return "unresolved"


@app.post("/api/paypal/webhook")
async def paypal_webhook(request: Request):
    body = await request.body()
    try:
        event = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    webhook_id = os.environ.get("PAYPAL_WEBHOOK_ID", "")
    if webhook_id:
        if not verify_webhook_signature(dict(request.headers), body, webhook_id):
            print("[paypal webhook] signature verification FAILED - rejecting event")
            raise HTTPException(status_code=400, detail="Invalid webhook signature")
    else:
        print("[paypal webhook] PAYPAL_WEBHOOK_ID not set - skipping signature verification "
              "(register the webhook in the PayPal dashboard, then set this env var)")

    event_type = event.get("event_type", "")
    resource = event.get("resource", {})
    print(f"[paypal webhook] received event_type={event_type}")

    ACTIVATION_EVENTS = {
        "BILLING.SUBSCRIPTION.ACTIVATED",
        "BILLING.SUBSCRIPTION.RE-ACTIVATED",
        "PAYMENT.SALE.COMPLETED",
    }
    # A failed charge is a lost-sale moment - SOS ladder, not just a log line
    PAYMENT_FAILURE_EVENTS = {
        "BILLING.SUBSCRIPTION.PAYMENT.FAILED",
        "PAYMENT.SALE.DENIED",
        "BILLING.SUBSCRIPTION.SUSPENDED",  # PayPal suspends after repeated failures
    }
    # The client can also cancel from paypal.com directly, bypassing our
    # closure/transfer flows entirely - if we miss that, we keep treating them
    # as an active paying client (counting them in MRR, running their agents)
    CANCELLATION_EVENTS = {"BILLING.SUBSCRIPTION.CANCELLED"}
    if event_type in ACTIVATION_EVENTS | PAYMENT_FAILURE_EVENTS | CANCELLATION_EVENTS:
        client_id_raw = resource.get("custom_id") or resource.get("custom")
        if client_id_raw:
            try:
                client_id = int(client_id_raw)
                if deletion_guard.is_deleting(client_id):
                    # Admin deletion in progress / done: nothing may re-activate
                    # or re-create anything for this id. Acknowledged, not acted on.
                    print(f"[paypal webhook] client {client_id} is being deleted - {event_type} ignored")
                elif event_type in ACTIVATION_EVENTS:
                    _mark_paid_and_notify(client_id, resource.get("id"), source="paypal_webhook")
                elif event_type in CANCELLATION_EVENTS:
                    client = get_client(client_id)
                    # Our own close/transfer flows also trigger this event, but
                    # they set the status to closed/transferred - if the status
                    # is anything else, the cancellation came from outside the
                    # system. (A webhook that races our own flow mid-request may
                    # slip through and raise a spurious alert - harmless, the
                    # flow overwrites the status right after.)
                    if client and client.get("status") not in ("closed", "transferred", "cancelled"):
                        update_client_status(client_id, "cancelled")
                        log_activity(client_id, "paypal_service", "subscription_cancelled",
                                     {"source": "paypal_webhook"}, {"subscription_id": resource.get("id")})
                        alert("paypal_webhook", [
                            f"client {client_id} subscription {resource.get('id')} was cancelled on "
                            f"PayPal's side (outside the dashboard flows) - follow up with the client"
                        ])
                else:
                    from agents.engagement_agent import notify_payment_failure
                    notify_payment_failure(client_id, event_type)
            except (TypeError, ValueError):
                print(f"[paypal webhook] could not parse client_id from custom_id={client_id_raw}")
        else:
            print(f"[paypal webhook] event {event_type} had no custom_id on resource - skipping")

    # Invoice events (setup fees — original checkout AND the avatar add-on,
    # both created via paypal_service.create_invoice). Invoice resources
    # carry no custom_id the way subscription resources do — the invoice
    # NUMBER (our own "INV-{client_id}-{ts}" scheme) is the correlation key.
    INVOICE_PAID_EVENTS = {"INVOICING.INVOICE.PAID"}
    INVOICE_CANCELLED_EVENTS = {"INVOICING.INVOICE.CANCELLED"}
    if event_type in INVOICE_PAID_EVENTS | INVOICE_CANCELLED_EVENTS:
        # Docs-derived resource shape (never verified against a live invoice
        # webhook) — detail.invoice_number is what we set at creation;
        # top-level fallback in case the payload nests differently in
        # practice, same defensive style as the custom_id/custom fallback above.
        invoice_number = (resource.get("detail") or {}).get("invoice_number") or resource.get("invoice_number", "")
        client_id = _client_id_from_invoice_number(invoice_number)
        if not client_id:
            print(f"[paypal webhook] {event_type}: could not recover client_id from "
                  f"invoice_number={invoice_number!r} - skipping")
        elif deletion_guard.is_deleting(client_id):
            print(f"[paypal webhook] client {client_id} is being deleted - {event_type} ignored")
        elif event_type in INVOICE_PAID_EVENTS:
            paid_amount = (resource.get("amount") or {}).get("value")
            log_activity(client_id, "paypal_service", "invoice_paid",
                         {"invoice_number": invoice_number, "amount": paid_amount},
                         {"paypal_invoice_id": resource.get("id"), "source": "paypal_webhook"})
            print(f"[paypal webhook] client {client_id}: invoice {invoice_number} PAID")
        else:  # cancelled
            log_activity(client_id, "paypal_service", "invoice_cancelled",
                         {"invoice_number": invoice_number}, {"paypal_invoice_id": resource.get("id")})
            alert("paypal_webhook", [
                f"client {client_id}: invoice {invoice_number} was CANCELLED on PayPal's side "
                "before payment - follow up on collecting this fee another way"])

    return {"success": True}

# ─── Invoice aging (unpaid setup-fee follow-up) ──────────────────────────────

INVOICE_UNPAID_ALERT_DAYS = 7

def run_invoice_aging_scan() -> dict:
    """Cron entry point: any invoice_sent row older than
    INVOICE_UNPAID_ALERT_DAYS with no matching invoice_paid/invoice_cancelled
    event yet gets ONE alert to Johnny (flag-once dedup via
    invoice_unpaid_flagged — a standing unpaid invoice doesn't need a fresh
    alert every scan). PayPal has no 'invoice aging' webhook event, so this
    is the only way an unpaid invoice surfaces at all beyond someone
    checking the PayPal dashboard by hand."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=INVOICE_UNPAID_ALERT_DAYS)).isoformat()
    rows = (db.table("client_activity").select("client_id,details,created_at")
            .eq("agent_name", "paypal_service").eq("action_type", "invoice_sent")
            .lte("created_at", cutoff).order("created_at", desc=True).limit(500).execute().data or [])

    summary = {"invoices_checked": 0, "alerts_raised": 0}
    for row in rows:
        client_id = row.get("client_id")
        invoice_number = (row.get("details") or {}).get("invoice_number")
        if not (client_id and invoice_number):
            continue
        summary["invoices_checked"] += 1
        if _invoice_reconciliation_state(client_id, invoice_number) != "unresolved":
            continue
        already_flagged = any(
            (e.get("details") or {}).get("invoice_number") == invoice_number
            for e in get_activity(client_id, limit=200)
            if e.get("agent_name") == "paypal_service" and e.get("action_type") == "invoice_unpaid_flagged")
        if already_flagged:
            continue
        log_activity(client_id, "paypal_service", "invoice_unpaid_flagged",
                     {"invoice_number": invoice_number, "sent_at": row.get("created_at")}, {})
        alert("paypal_webhook", [
            f"client {client_id}: invoice {invoice_number} (sent {row.get('created_at', '')[:10]}) "
            f"is still unpaid after {INVOICE_UNPAID_ALERT_DAYS}+ days - follow up on collecting it"])
        summary["alerts_raised"] += 1
    print(f"[invoice_aging_scan] done - {summary}")
    return summary

@app.get("/api/paypal/invoice-aging-scan", dependencies=_admin_only)
def paypal_invoice_aging_scan():
    try:
        return {"success": True, "data": run_invoice_aging_scan()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class ObjectionRequest(BaseModel):
    text: str
    answers: dict = {}
    empathy_final: dict = {}
    packages: list = []
    lead_id: str = ""     # who to name in a handoff alert (prospects have no client row)
    language: str = "he"  # the sales page's UI language - tie-breaker for the fixed reply

def _prospect_contact(answers: dict) -> str:
    # Whatever the chat has collected so far, so the team can actually reach a
    # prospect whose question was handed off - there is no client row yet
    picked = {k: answers.get(k) for k in ("business_name", "name", "phone", "email") if answers.get(k)}
    return f" Known so far: {picked}." if picked else ""

@app.post("/api/handle-objection")
def handle_objection_endpoint(req: ObjectionRequest):
    # The sales chat's only free-text turn. Never a dead end: an out-of-scope
    # question is answered in the closer's voice AND handed to the team; a
    # failure gets the fixed in-voice handoff and the packages are shown again.
    from core import chat_fallback
    from core.redaction import looks_like_secret, redact_obj
    if looks_like_secret(req.text):
        # A key/password pasted into the sales chat: warm fixed reply, no
        # model call, nothing of it forwarded anywhere
        from core.chat_guard import secret_reply
        return {"success": True, "reply": secret_reply(req.text)}
    req.answers = redact_obj(req.answers)
    try:
        from agents.onboarding_agent import handle_objection, get_api_key
        with _sales_cost_owner(req.lead_id):
            result = handle_objection(req.text, req.packages, req.answers, req.empathy_final, get_api_key())
        reply = (result.get("reply") or "").strip()
        if result.get("out_of_scope"):
            chat_fallback.alert_handoff(
                chat_fallback.KIND_OUT_OF_SCOPE, "onboarding_agent",
                f'prospect asked: "{req.text[:400]}".{_prospect_contact(req.answers)}',
                persona="sales", lead_id=req.lead_id)
            if not reply:
                reply = chat_fallback.out_of_scope_reply(
                    "sales", chat_fallback.detect_language(req.text, req.language))
        if not reply:
            raise RuntimeError("closer returned an empty reply")
        return {"success": True, "reply": reply}
    except Exception as e:
        import traceback
        traceback.print_exc()
        chat_fallback.alert_handoff(
            chat_fallback.KIND_TECHNICAL, "onboarding_agent",
            f'sales-chat objection reply failed ({type(e).__name__}: {str(e)[:200]}). '
            f'Prospect wrote: "{req.text[:300]}".{_prospect_contact(req.answers)}',
            persona="sales", lead_id=req.lead_id,
            issue_key=f"technical:sales:objection:{chat_fallback.question_key(req.text)}")
        # success stays True: this IS a reply the chat should show, not an error
        return {"success": True, "degraded": True,
                "reply": chat_fallback.technical_reply(
                    "sales", chat_fallback.detect_language(req.text, req.language))}

class ReactionRequest(BaseModel):
    question_text: str = ""
    answer_text: str = ""
    lead_id: str = ""   # cost attribution (core/cost_tracker)

@app.post("/api/reaction")
def reaction_endpoint(req: ReactionRequest):
    try:
        from agents.onboarding_agent import get_reaction, get_api_key
        with _sales_cost_owner(req.lead_id):
            reaction = get_reaction(req.question_text, req.answer_text, get_api_key())
        return {"success": True, "reaction": reaction}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"success": False, "reaction": ""}


# ─── Clients ──────────────────────────────────────────────────────────────────

class CreateClientRequest(BaseModel):
    name: str
    email: str = ""
    phone: str = ""
    package: str = ""

class UpdateStatusRequest(BaseModel):
    status: str

class AddAccountRequest(BaseModel):
    platform: str
    account_id: str = ""
    access_token: str = ""
    status: str = "active"

class AssignAgentRequest(BaseModel):
    agent_name: str

class UpdateAgentStatusRequest(BaseModel):
    agent_name: str
    status: str

class LogActivityRequest(BaseModel):
    agent_name: str
    action_type: str
    details: dict = {}
    result: dict = {}

class LogCommunicationRequest(BaseModel):
    direction: str
    channel: str
    content: str

@app.post("/api/clients", dependencies=_admin_only)
async def api_create_client(req: CreateClientRequest):
    try:
        client = create_client(req.name, req.email, req.phone, req.package)
        return {"success": True, "data": public_client(client)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/clients", dependencies=_admin_only)
async def api_list_clients(status: str = None):
    try:
        return {"success": True, "data": [public_client(c) for c in list_clients(status)]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/clients/{client_id}", dependencies=_admin_only)
async def api_get_client(client_id: int):
    client = get_client(client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return {"success": True, "data": public_client(client)}

# ─── Client login (email + one-time code, no passwords) ──────────────────────

class LoginRequestCodeRequest(BaseModel):
    email: str

# Offboarded clients are hard-locked out of the dashboard (business decision,
# 2026-07): their archive lives in Google Drive, not behind a login. Their
# data export was attached to the closure/transfer confirmation email.
OFFBOARDED_STATUSES = ("closed", "transferred")

def _locked_out(client: dict) -> bool:
    """No login and no session: offboarded, or an admin deletion has started
    (status 'deleting', or the deletion marker when that status write failed)."""
    return (not client or client.get("status") in OFFBOARDED_STATUSES + ("deleting",)
            or deletion_guard.is_deleting(client.get("id")))

@app.post("/api/login/request-code")
async def login_request_code(req: LoginRequestCodeRequest):
    try:
        client = get_client_by_email(req.email.strip())
        # Same generic response for an offboarded account as for an unknown
        # email - no code is sent, and nothing is revealed
        if client and _locked_out(client):
            client = None
        if client:
            code = f"{secrets.randbelow(1_000_000):06d}"
            expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
            create_login_code(client["id"], code, expires_at)
            send_login_code(client["email"], client.get("name", ""), code, client["id"])
    except Exception as e:
        import traceback
        traceback.print_exc()
    # Always a generic response - never reveal whether the email exists
    return {"success": True, "message": "אם קיים חשבון עם המייל הזה, שלחנו אליו קוד התחברות"}

class LoginVerifyCodeRequest(BaseModel):
    email: str
    code: str

MAX_LOGIN_CODE_ATTEMPTS = 5

@app.post("/api/login/verify-code")
async def login_verify_code(req: LoginVerifyCodeRequest, response: Response):
    invalid = HTTPException(status_code=401, detail={"code": "ERR_INVALID_CODE"})

    client = get_client_by_email(req.email.strip())
    if _locked_out(client):
        raise invalid

    login_code = get_active_login_code(client["id"])
    if not login_code:
        raise invalid

    if login_code.get("failed_attempts", 0) >= MAX_LOGIN_CODE_ATTEMPTS:
        raise invalid

    expires_at = login_code["expires_at"].replace("Z", "+00:00")
    if datetime.fromisoformat(expires_at) < datetime.now(timezone.utc):
        raise invalid

    if login_code["code"] != req.code.strip():
        new_count = increment_login_code_attempts(login_code["id"], login_code.get("failed_attempts", 0))
        if new_count >= MAX_LOGIN_CODE_ATTEMPTS:
            mark_login_code_used(login_code["id"])  # invalidate after too many wrong guesses
        raise invalid

    mark_login_code_used(login_code["id"])
    client_auth.mark_email_verified(client)
    _set_session_cookie(response, client["id"], client.get("session_version"))
    return {"success": True}

def _set_session_cookie(response: Response, client_id: int, session_version=0):
    response.set_cookie(
        key="session",
        value=create_session_token(client_id, int(session_version or 0)),
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=30 * 24 * 60 * 60,
        path="/",
    )

def _admin_view_client(request: Request):
    """The client an admin is viewing through "enter as client"
    (core/admin_view), or None. Valid only with the admin session that opened it."""
    token = request.cookies.get(admin_view.COOKIE)
    if not token:
        return None
    return verify_admin_view_token(token, request.cookies.get("admin_session"))

def _request_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "")

def _admin_view_audit(request: Request, event: str, client_id: int, detail: str = ""):
    admin_view.audit(event, client_id, admin_session_fingerprint(request.cookies.get("admin_session")),
                     _request_ip(request), detail)

def _require_session(request: Request) -> int:
    # Admin "enter as client" first: READ-ONLY, by endpoint allowlist. It is
    # never a client session — the client's own cookie, password and login
    # codes are untouched (core/admin_view).
    viewed = _admin_view_client(request)
    if viewed is not None:
        endpoint = getattr(request.scope.get("endpoint"), "__name__", "")
        if not admin_view.allowed(endpoint, request.method):
            raise HTTPException(status_code=403, detail={"code": "ERR_ADMIN_VIEW_READ_ONLY"})
        if _locked_out(get_client(viewed)):
            raise HTTPException(status_code=401, detail="Not authenticated")
        return int(viewed)
    token = request.cookies.get("session")
    client_id = verify_session_token(token)
    if not client_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    # Hard lock: a closed/transferred client is out even with a still-valid
    # session cookie on another device (cookies live 30 days; the status check
    # is what actually ends access everywhere). One extra DB read per request -
    # fine at current volume.
    client = get_client(client_id)
    if _locked_out(client):
        raise HTTPException(status_code=401, detail="Not authenticated")
    # A password change/reset bumps session_version - every cookie issued
    # before it stops working (a missing column reads as 0 = no change)
    if session_token_version(token) != int(client.get("session_version") or 0):
        raise HTTPException(status_code=401, detail="Not authenticated")
    return client_id

# ─── Passwords, signup and Google sign-in (core/client_auth) ─────────────────
# "Signup" here is credential setup on the client row checkout ALREADY created
# (name/email/phone were collected there, before PayPal) - never a second door
# that creates accounts. An email that matches no client gets the same generic
# answer as one that does.

def _login_client(email: str) -> dict:
    email = (email or "").strip()
    client = get_client_by_email(email) or get_client_by_email(email.lower())
    if _locked_out(client):
        return {}
    return client

_auth_not_ready_alerted_at = 0.0

def _auth_not_ready(e: Exception):
    # alert() has no dedup and every login attempt would hit this - once an hour
    # per instance is plenty to make the missing migration impossible to miss
    global _auth_not_ready_alerted_at
    print(f"[client_auth] {e}")
    if time.time() - _auth_not_ready_alerted_at > 3600:
        _auth_not_ready_alerted_at = time.time()
        alert("client_auth", [f"client auth unavailable - run migrations/2026-10-13-client-auth.sql: {e}"])
    return HTTPException(status_code=503, detail={"code": "ERR_AUTH_NOT_READY"})

class PasswordLoginRequest(BaseModel):
    email: str
    password: str

@app.post("/api/login/password")
def login_password(req: PasswordLoginRequest, response: Response):
    # Plain def: scrypt is deliberately slow and must not block the event loop
    client = _login_client(req.email)
    try:
        problem = client_auth.verify_password_login(client, req.password) if client else "ERR_INVALID_CREDENTIALS"
    except client_auth.AuthNotReadyError as e:
        raise _auth_not_ready(e)
    if problem:
        time.sleep(0.5)  # cheap brute-force damper on top of the per-client lockout
        raise HTTPException(status_code=401, detail={"code": problem})
    _set_session_cookie(response, client["id"], client.get("session_version"))
    return {"success": True}

class SignupRequestCodeRequest(BaseModel):
    email: str

@app.post("/api/signup/request-code")
def signup_request_code(req: SignupRequestCodeRequest):
    """Step 1 of post-payment setup AND of forgot-password: email a code that
    step 2 trades for a password. Same login_codes table and attempt limits as
    code login, so it opens no new guessing surface."""
    try:
        client = _login_client(req.email)
        if client:
            code = f"{secrets.randbelow(1_000_000):06d}"
            expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
            create_login_code(client["id"], code, expires_at)
            send_login_code(client["email"], client.get("name", ""), code, client["id"], purpose="password")
    except Exception:
        import traceback
        traceback.print_exc()
    return {"success": True}

class SignupCompleteRequest(BaseModel):
    email: str
    code: str
    password: str
    name: str = ""
    phone: str = ""

@app.post("/api/signup/complete")
def signup_complete(req: SignupCompleteRequest, response: Response):
    client = _login_client(req.email)
    if not client:
        raise HTTPException(status_code=401, detail={"code": "ERR_INVALID_CODE"})
    try:
        result = client_auth.set_password_with_code(client, req.code, req.password,
                                                    MAX_LOGIN_CODE_ATTEMPTS, req.name, req.phone)
    except client_auth.AuthNotReadyError as e:
        raise _auth_not_ready(e)
    if not result["success"]:
        status = 401 if result["code"] == "ERR_INVALID_CODE" else 400
        raise HTTPException(status_code=status, detail={"code": result["code"]})
    _set_session_cookie(response, client["id"], result["session_version"])
    return {"success": True}

GOOGLE_LOGIN_NONCE_COOKIE = "g_login_nonce"
# The pending first-link step (core/client_auth.complete_google_link). Same
# path as the nonce cookie, so both reach only the google-login endpoints.
GOOGLE_LINK_COOKIE = "g_link_pending"
GOOGLE_LOGIN_COOKIE_PATH = "/api/oauth/google-login"

def _google_login_cookie(response, name: str, value: str):
    # Lax, not Strict: the callback is a top-level navigation FROM Google,
    # which Strict would strip the cookie from
    response.set_cookie(name, value, max_age=600, httponly=True, secure=True,
                        samesite="lax", path=GOOGLE_LOGIN_COOKIE_PATH)

def _clear_google_login_cookies(response):
    for name in (GOOGLE_LOGIN_NONCE_COOKIE, GOOGLE_LINK_COOKIE):
        response.delete_cookie(name, path=GOOGLE_LOGIN_COOKIE_PATH)

@app.get("/api/oauth/google-login/start")
def google_login_start():
    nonce = secrets.token_urlsafe(24)
    response = RedirectResponse(url=client_auth.google_login_url(create_google_login_state(nonce)))
    _google_login_cookie(response, GOOGLE_LOGIN_NONCE_COOKIE, nonce)
    return response

@app.get("/api/oauth/google-login/callback")
def google_login_callback(request: Request, state: str = "", code: str = "", error: str = ""):
    def fail(reason: str):
        # cancelled | expired | no_account | unavailable — the login page
        # words each one precisely
        response = RedirectResponse(url=f"/login/?google_error={reason}")
        _clear_google_login_cookies(response)
        return response

    if error or not code:
        return fail("cancelled")
    nonce = request.cookies.get(GOOGLE_LOGIN_NONCE_COOKIE, "")
    if not verify_google_login_state(state, nonce):
        return fail("expired")
    def no_account(identity: dict):
        # One deduped admin alert saying WHY: masked email, verified flag,
        # what matched and that client's status — never the full address,
        # a token or a code. A failure here must not change the redirect.
        try:
            line = client_auth.no_account_alert_text(
                client_auth.google_no_account_diagnosis(identity, _locked_out))
            if line:
                alert("client_auth", [line])
        except Exception as e:
            print(f"[google_login] no_account diagnosis failed: {type(e).__name__}")
        return fail("no_account")

    try:
        identity = client_auth.google_identity(code)
        client = client_auth.find_client_for_google(identity)
        if _locked_out(client):
            return no_account(identity)
        outcome = client_auth.google_callback_outcome(client, identity)
        if outcome == "refuse":
            return no_account(identity)
        if outcome == "link_code":
            # First Google sign-in to this account: nothing is linked and no
            # session is set until the emailed code proves the inbox
            pending = client_auth.start_google_link(client, identity)
            response = RedirectResponse(url="/login/?google_link=1")
            _google_login_cookie(response, GOOGLE_LINK_COOKIE, create_google_link_token(
                pending["client_id"], pending["sub"], pending["code_id"], nonce))
            _google_login_cookie(response, GOOGLE_LOGIN_NONCE_COOKIE, nonce)   # 10 more minutes
            return response
        client_auth.mark_email_verified(client)
    except client_auth.AuthNotReadyError as e:
        _auth_not_ready(e)
        return fail("unavailable")
    except Exception as e:
        import traceback
        traceback.print_exc()
        # The exception CLASS tells "migration pending" (AuthNotReadyError,
        # handled above) from a Google/SMTP/DB failure; never the code or a token
        alert("client_auth", [f"Google sign-in failed after consent - {type(e).__name__}: {str(e)[:300]}"])
        return fail("unavailable")
    response = RedirectResponse(url="/dashboard/")
    _clear_google_login_cookies(response)
    _set_session_cookie(response, client["id"], client.get("session_version"))
    return response

class GoogleLinkCodeRequest(BaseModel):
    code: str

@app.post("/api/oauth/google-login/verify-code")
def google_login_verify_code(req: GoogleLinkCodeRequest, request: Request, response: Response):
    """Step 2 of a FIRST Google sign-in: the emailed code links the Google
    identity and signs in. The pending token comes only from its HttpOnly
    cookie (bound to the sign-in nonce cookie); wrong codes count against the
    same login_codes attempt limit as code login."""
    pending = verify_google_link_token(request.cookies.get(GOOGLE_LINK_COOKIE, ""),
                                       request.cookies.get(GOOGLE_LOGIN_NONCE_COOKIE, ""))
    if not pending:
        raise HTTPException(status_code=401, detail={"code": "ERR_LINK_EXPIRED"})
    client = get_client(pending["client_id"])
    if _locked_out(client):
        raise HTTPException(status_code=401, detail={"code": "ERR_LINK_EXPIRED"})
    try:
        result = client_auth.complete_google_link(client, pending["sub"], pending["code_id"],
                                                  req.code, MAX_LOGIN_CODE_ATTEMPTS)
    except client_auth.AuthNotReadyError as e:
        raise _auth_not_ready(e)
    if not result["success"]:
        failed = JSONResponse(status_code=401, content={"detail": {"code": result["code"]}})
        if result["code"] != "ERR_INVALID_CODE":
            # Nothing left to try with this token — start over with Google
            _clear_google_login_cookies(failed)
        return failed
    _clear_google_login_cookies(response)
    _set_session_cookie(response, client["id"], client.get("session_version"))
    return {"success": True, "redirect": "/dashboard/"}

@app.get("/api/client/account")
def client_account(request: Request):
    client = get_client(_require_session(request))
    return {"success": True, "data": {
        "email": client.get("email", ""),
        "has_password": client_auth.has_password(client),
        "google_linked": bool(client.get("google_sub")),
        "email_verified": bool(client.get("email_verified_at")),
        "billing_exempt": bool(client.get("billing_exempt")),
    }}

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

@app.post("/api/client/password")
def client_change_password(req: ChangePasswordRequest, request: Request, response: Response):
    """Change an EXISTING password (needs the current one). A client without a
    password sets their first one through /api/signup/* with their own email -
    the emailed code is the proof there, as everywhere else."""
    client = get_client(_require_session(request))
    try:
        result = client_auth.change_password(client, req.current_password, req.new_password)
    except client_auth.AuthNotReadyError as e:
        raise _auth_not_ready(e)
    if not result["success"]:
        raise HTTPException(status_code=400, detail={"code": result["code"]})
    # Every other device is now logged out; keep THIS one signed in
    _set_session_cookie(response, client["id"], result["session_version"])
    return {"success": True}

@app.get("/api/dashboard")
async def dashboard_data(request: Request):
    client_id = _require_session(request)
    client = get_client(client_id)
    if not client:
        raise HTTPException(status_code=401, detail="Not authenticated")

    accounts = get_accounts(client_id)
    activity = get_activity(client_id, limit=100)

    # Derive monthly_fee + subscription_id from the checkout activity log - there's no
    # dedicated billing table yet, this is what was actually recorded at checkout time
    monthly_fee = None
    subscription_id = None
    for entry in activity:
        if entry.get("agent_name") == "paypal_service" and entry.get("action_type") == "subscription_cancelled":
            break  # closure/transfer cancelled the subscription - nothing live to show
        if entry.get("agent_name") == "paypal_service" and entry.get("action_type") == "subscription_created":
            details = entry.get("details") or {}
            monthly_fee = details.get("monthly_management_total")
            subscription_id = (entry.get("result") or {}).get("subscription_id")
            break  # activity is newest-first, so the first match is the most recent checkout

    next_billing_date = None
    if subscription_id:
        try:
            sub_status = get_subscription_status(subscription_id)
            next_billing_date = sub_status.get("next_billing_time")
        except Exception as e:
            print(f"[dashboard] could not fetch live subscription status: {e}")

    month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    activity_month_count = sum(1 for e in activity if (e.get("created_at") or "") >= month_start)
    tour_completed = any(e.get("action_type") == "welcome_tour_completed" for e in activity)

    # Self-service "create a new site" progress, from activity already fetched
    # above — no extra query. None when a wordpress row is already active.
    website_provision_status = None
    if not any(a.get("platform") == "wordpress" and a.get("status") == "active" for a in accounts):
        from agents.website_agent import _provision_state_from_activity
        website_provision_status = _provision_state_from_activity(activity)

    # A GTM grant without every current scope (one made before
    # tagmanager.edit.containerversions was requested) can read but cannot
    # publish — the card asks for a calm reconnect instead of failing later
    gtm_needs_reconnect = False
    if any(a.get("platform") == "google_tagmanager" and a.get("status") == "active" for a in accounts):
        try:
            from agents.website_agent import gtm_missing_scopes
            gtm_needs_reconnect = bool(gtm_missing_scopes(client_id))
        except Exception as e:
            print(f"[dashboard] gtm scope check failed for client {client_id}: {e}")
    # A dead Google Ads grant (core/google_ads_health): the card must say
    # "reconnect", never "connected ✓". DB reads only, no Google call.
    google_ads_needs_reconnect = False
    if any(a.get("platform") == "google_ads" and a.get("status") == "active" for a in accounts):
        try:
            from agents.google_ads_agent import grant_needs_reconnect
            google_ads_needs_reconnect = grant_needs_reconnect(client_id)
        except Exception as e:
            print(f"[dashboard] google ads health check failed for client {client_id}: {type(e).__name__}")

    return {"success": True, "data": {
        "client": {
            "id": client.get("id"),
            "name": client.get("name"),
            "email": client.get("email"),
            "package": client.get("package"),
            "status": client.get("status"),
            "created_at": client.get("created_at"),
            # Chat persona choice (male|female|null) - set by the client via
            # /api/client/profile, never inferred from their name
            "owner_gender": client.get("owner_gender"),
            # Client-uploaded data: URL (resized in the browser before upload);
            # null until they set one. Requires the clients.profile_image column.
            "profile_image": client.get("profile_image"),
        },
        "monthly_fee": monthly_fee,
        "next_billing_date": next_billing_date,
        # Internal no-billing account: the billing UI says so instead of
        # showing an empty fee that looks like missing data
        "billing_exempt": bool(client.get("billing_exempt")),
        # Never ship stored credentials to the browser - the dashboard only
        # needs platform + status to paint the connection cards
        "connections": [{"platform": a.get("platform"), "status": a.get("status")}
                        for a in accounts],
        # Which Google Ads account is connected, as the client sees it in
        # Google Ads (123-456-7890). An account number, not a credential.
        "google_ads_customer_id": next(
            (google_ads_service.format_customer_id(a.get("account_id"))
             for a in accounts if a.get("platform") == "google_ads"
             and a.get("status") == "active" and a.get("account_id")), ""),
        "google_ads_account_name": _google_ads_account_name(client_id, accounts),
        "gtm_needs_reconnect": gtm_needs_reconnect,
        "google_ads_needs_reconnect": google_ads_needs_reconnect,
        # Google verification round 2 cards: hidden unless their flag is on
        # (core/round2_flags; Gmail also needs the Gmail agent package)
        "features": _round2_features(client_id),
        # Admin "enter as client": the page shows the banner + Exit and turns
        # approve buttons into explicit staff buttons (core/admin_view)
        "admin_view": _admin_view_client(request) is not None,
        "website_provision_status": website_provision_status,  # None | 'requested' | 'failed'
        "activity": activity[:10],
        "activity_month_count": activity_month_count,
        "tour_completed": tour_completed,
    }}

@app.patch("/api/clients/{client_id}/status", dependencies=_admin_only)
async def api_update_client_status(client_id: int, req: UpdateStatusRequest):
    try:
        return {"success": True, "data": update_client_status(client_id, req.status)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/clients/{client_id}/complete-onboarding", dependencies=_admin_only)
async def api_complete_onboarding(client_id: int):
    try:
        return {"success": True, "data": complete_onboarding(client_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/clients/{client_id}/accounts", dependencies=_admin_only)
async def api_add_account(client_id: int, req: AddAccountRequest):
    try:
        return {"success": True, "data": add_account(client_id, req.platform, req.account_id, req.access_token, req.status)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/clients/{client_id}/accounts", dependencies=_admin_only)
async def api_get_accounts(client_id: int):
    try:
        return {"success": True, "data": get_accounts(client_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/clients/{client_id}/agents", dependencies=_admin_only)
async def api_assign_agent(client_id: int, req: AssignAgentRequest):
    try:
        return {"success": True, "data": assign_agent(client_id, req.agent_name)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/clients/{client_id}/agents", dependencies=_admin_only)
async def api_get_client_agents(client_id: int):
    try:
        return {"success": True, "data": get_client_agents(client_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/clients/{client_id}/agents/status", dependencies=_admin_only)
async def api_update_agent_status(client_id: int, req: UpdateAgentStatusRequest):
    try:
        return {"success": True, "data": update_agent_status(client_id, req.agent_name, req.status)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/clients/{client_id}/activity", dependencies=_admin_only)
async def api_log_activity(client_id: int, req: LogActivityRequest):
    try:
        return {"success": True, "data": log_activity(client_id, req.agent_name, req.action_type, req.details, req.result)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/clients/{client_id}/activity", dependencies=_admin_only)
async def api_get_activity(client_id: int, limit: int = 50):
    try:
        return {"success": True, "data": get_activity(client_id, limit)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/clients/{client_id}/communications", dependencies=_admin_only)
async def api_log_communication(client_id: int, req: LogCommunicationRequest):
    try:
        return {"success": True, "data": log_communication(client_id, req.direction, req.channel, req.content)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/clients/{client_id}/communications", dependencies=_admin_only)
async def api_get_communications(client_id: int, limit: int = 50):
    try:
        return {"success": True, "data": get_communications(client_id, limit)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class ClientChatRequest(BaseModel):
    message: str
    persona: str = "general"  # general (concierge, full capabilities) or a
                              # PERSONAS key (google|meta|website|media) —
                              # specialist personas are READ-ONLY by
                              # construction (see support_agent's docstring)
    help_connection: str = ""  # a core/connection_help id — the "עזרה" button

@app.get("/api/client/performance")
def client_performance(request: Request):
    # The "ביצועים" page: one status per channel (core/performance — the same
    # resolver future alerts reuse), paid windows, organic keywords. Plain
    # `def`: several platform reads; each is cached by its own reader.
    client_id = _require_session(request)
    from core import performance
    return {"success": True, "data": performance.performance_page(client_id)}

@app.get("/api/client/connection-help")
def client_connection_help(request: Request, lang: str = "he"):
    # Which connections have in-product help, so the dashboard renders the
    # "עזרה" buttons from config (inactive entries never show)
    _require_session(request)
    from core import connection_help
    return {"success": True, "data": connection_help.public_list(lang)}

@app.get("/api/client/connection-help/{connection_id}")
def client_connection_guide(connection_id: str, request: Request, lang: str = "he"):
    # The card's PRIMARY connect path: numbered steps, what the screen looks
    # like, direct links (from the config only), where the key is
    _require_session(request)
    from core import connection_help
    view = connection_help.guide_view(connection_id, lang)
    if not view:
        raise HTTPException(status_code=404, detail={"code": "no_guide"})
    return {"success": True, "data": view}

@app.post("/api/client/connection-help/{connection_id}/assist")
def client_connection_assist(connection_id: str, request: Request):
    # "Stuck? we'll do it with you": a staff request (admin alert, one per
    # client + connection a day), so the client is never left alone with a key
    # (the chat card request_connection_help sends the same request)
    client_id = _require_session(request)
    from core import connection_help
    try:
        return {"success": True, "data": connection_help.request_assist(client_id, connection_id)}
    except ValueError:
        raise HTTPException(status_code=404, detail={"code": "no_guide"})

@app.post("/api/client-chat")
def client_chat(req: ClientChatRequest, request: Request, background_tasks: BackgroundTasks):
    client_id = _require_session(request)
    try:
        from agents.support_agent import (PERSONAS, answer_persona_question,
                                           answer_support_question, persona_channel)
        persona = (req.persona or "general").strip()
        if persona != "general" and persona not in PERSONAS:
            raise HTTPException(status_code=400, detail={"code": "ERR_UNKNOWN_PERSONA"})
        # Each persona's messages are stored on their own channel, so the
        # conversation with (say) the Media specialist is fully separate from
        # the general concierge and every other specialist — persisted and
        # replayed independently (see support_agent.persona_channel).
        channel = persona_channel(persona)
        # Before anything is stored or reaches a model: a credential is never
        # saved, an overlong message or a client past today's cap gets a fixed
        # warm reply (core/chat_guard)
        from core import chat_guard
        blocked = chat_guard.check(client_id, req.message)
        if blocked:
            log_communication(client_id, "inbound", channel, blocked["store_as"])
            log_communication(client_id, "outbound", channel, blocked["reply"])
            return {"success": True, "reply": blocked["reply"], "needs_human_followup": False,
                    "poll": False, "limited": blocked["code"]}
        log_communication(client_id, "inbound", channel, req.message)
        from core.cost_tracker import cost_action
        # Every LLM cost of this turn is labelled with it (core/cost_tracker);
        # an article draft started inside it relabels its own calls
        with cost_action(f"chat_{persona}"):
            if persona == "general":
                result = answer_support_question(client_id, req.message,
                                                 help_connection=req.help_connection)
            else:
                # Specialist path: structurally read-only — no web search, no
                # action functions reachable (support_agent enforces), except the
                # website persona's code-run article DRAFT (never publish) and the
                # same priced upgrade QUOTE the concierge builds
                result = answer_persona_question(client_id, persona, req.message,
                                                 background_tasks)
        # Vendor names never reach a client (core/vendor_tools) — except the
        # connection help for those very tools, which must name the vendor
        from core import agent_actions, vendor_tools
        if req.help_connection not in vendor_tools.TOOLS:
            result["reply"] = vendor_tools.scrub_vendor_names(result["reply"], agent_actions._lang(client_id))
        log_communication(client_id, "outbound", channel, result["reply"])
        # poll: work continues in the background (an article draft) and its
        # outcome will be posted into this persona's thread — the window
        # watches for it (appends only; never re-renders the input).
        # action: a confirmation card for a stored proposal (core/agent_actions)
        # — the window renders it under the reply; only its button runs it.
        return {"success": True, "reply": result["reply"],
                "needs_human_followup": result["needs_human_followup"],
                "poll": bool(result.get("poll")), "action": result.get("action")}
    except HTTPException:
        raise
    except Exception as e:
        # The agents never raise (core/chat_fallback), so this is the plumbing
        # around them - storing the message, the import. Still never a dead
        # end: an in-lane handoff line, an alert, and the chat carries on.
        import traceback
        traceback.print_exc()
        from core import chat_fallback
        persona = (req.persona or "general").strip()
        chat_fallback.alert_handoff(
            chat_fallback.KIND_TECHNICAL, "support_agent",
            f'client-chat endpoint failed ({type(e).__name__}: {str(e)[:200]}). '
            f'Client asked: "{req.message[:300]}"',
            client_id=client_id, persona=persona,
            issue_key=f"technical:{persona}:endpoint:{chat_fallback.question_key(req.message)}")
        # No DB read for the stored language here - the DB may be what broke
        language = chat_fallback.detect_language(req.message)
        reply = chat_fallback.technical_reply(persona, language)
        try:
            from agents.support_agent import persona_channel
            log_communication(client_id, "outbound", persona_channel(persona), reply)
        except Exception:
            pass  # best effort - the history write may be the very thing failing
        return {"success": True, "needs_human_followup": True, "reply": reply}

@app.get("/api/client-chat/history")
async def client_chat_history(request: Request, scope: str = "current", persona: str = "general"):
    # scope=current -> the active thread only (bounded by clients.chat_started_at,
    # set by the 'שיחה חדשה' action); scope=all -> full history for browsing.
    # persona selects WHICH conversation: the general concierge or one specialist,
    # each stored on its own channel (see support_agent.persona_channel).
    from agents.support_agent import PERSONAS, persona_channel
    client_id = _require_session(request)
    persona = (persona or "general").strip()
    if persona != "general" and persona not in PERSONAS:
        raise HTTPException(status_code=400, detail={"code": "ERR_UNKNOWN_PERSONA"})
    channel = persona_channel(persona)
    history = list(reversed(get_communications(client_id, limit=200, channel=channel)))
    boundary = get_client(client_id).get("chat_started_at")
    current = ([h for h in history if (h.get("created_at") or "") >= boundary]
               if boundary else history)
    return {
        "success": True,
        "history": history if scope == "all" else current,
        "chat_started_at": boundary,
        "has_previous": len(history) > len(current),
    }

@app.get("/api/client-chat/unread")
def client_chat_unread(request: Request):  # plain `def`: blocking Supabase read
    # Newest OUTBOUND message per persona thread. Agents post proactively into
    # their own thread (media_agent's niche examples, ...) and the client is not
    # watching five windows — the dashboard compares these against a locally
    # stored "seen" stamp to decide where to show a dot. Read-only, no state
    # here: a per-client read marker would be a schema change for a UI hint.
    from agents.support_agent import PERSONAS, persona_channel
    client_id = _require_session(request)
    latest = {}
    for row in get_communications(client_id, limit=200):
        if row.get("direction") != "outbound":
            continue
        channel, created = row.get("channel") or "", row.get("created_at") or ""
        if created > latest.get(channel, ""):
            latest[channel] = created
    data = {persona: latest.get(persona_channel(persona), "")
            for persona in ["general"] + list(PERSONAS)}
    return {"success": True, "data": data}

@app.post("/api/client-chat/new")
async def client_chat_new(request: Request):
    # Start a fresh conversation thread; past messages stay browsable via
    # history?scope=all. Requires clients.chat_started_at (timestamptz) column.
    client_id = _require_session(request)
    db.table("clients").update(
        {"chat_started_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", client_id).execute()
    return {"success": True}

# ─── Package upgrade + billing (client-facing, session-gated) ────────────────

def _client_subscription_info(client_id: int) -> dict:
    """monthly_fee + subscription_id from the newest checkout activity row -
    the same derivation /api/dashboard uses (no dedicated billing table yet).
    A subscription_cancelled row (closure/transfer/webhook) newer than the
    checkout row means there is no live subscription anymore - stop there, so
    billing/upgrade flows never operate on a dead subscription."""
    for entry in get_activity(client_id, limit=100):
        if entry.get("agent_name") == "paypal_service" and entry.get("action_type") == "subscription_cancelled":
            break
        if entry.get("agent_name") == "paypal_service" and entry.get("action_type") == "subscription_created":
            details = entry.get("details") or {}
            return {
                "monthly_fee": details.get("monthly_management_total") or 0,
                "setup_fee": details.get("setup_fee_total") or 0,
                "subscription_id": (entry.get("result") or {}).get("subscription_id"),
                "checkout_at": entry.get("created_at"),
            }
    return {"monthly_fee": 0, "setup_fee": 0, "subscription_id": None, "checkout_at": None}

def _client_has_avatar_tier(client_id: int) -> bool:
    """Guard against double-selling the avatar add-on. FAILS CLOSED: if the
    read errors, we act as if a tier exists (don't offer) — the risk of
    hiding the option for one panel-load beats the risk of double-billing."""
    try:
        from agents.avatar_agent import get_monthly_usage
        return bool(get_monthly_usage(client_id).get("tier"))
    except Exception as e:
        print(f"[upgrade-options] client {client_id}: avatar tier read failed ({e}) - not offering avatar")
        return True

def _client_has_youtube_addon(client_id: int) -> bool:
    """Guard against double-selling the standalone YouTube add-on. A
    'youtube_content_agent / addon_activated' row is written on a confirmed
    purchase (see /api/upgrade-success); this is a TARGETED query for it (not a
    recent-activity scan, which could age the row out and re-offer to a busy
    client). FAILS CLOSED: a read error is treated as 'already has it', hiding
    the option for one load rather than risking a double-sell — same stance as
    _client_has_avatar_tier."""
    try:
        rows = (db.table("client_activity").select("id")
                .eq("client_id", client_id)
                .eq("agent_name", "youtube_content_agent")
                .eq("action_type", "addon_activated")
                .limit(1).execute().data or [])
        return bool(rows)
    except Exception as e:
        print(f"[upgrade-options] client {client_id}: youtube addon read failed ({e}) - not offering")
        return True

@app.get("/api/client/upgrade-options")
def client_upgrade_options(request: Request):
    from agents.onboarding_agent import (get_avatar_upgrade_tiers, get_upgrade_addons,
                                          get_upgrade_tiers, get_youtube_standalone_addon)
    client_id = _require_session(request)
    client = get_client(client_id)
    sub = _client_subscription_info(client_id)
    if not sub["subscription_id"]:
        return {"success": True, "data": {"available": False, "reason": "אין מנוי פעיל לשדרוג"}}
    tiers = [t for t in get_upgrade_tiers() if t["monthly_fee"] > sub["monthly_fee"]]
    # Avatar add-on: exact deterministic pricing -> directly billable like the
    # ladder (additive: current fee + tier). Offered only while no avatar tier
    # is assigned yet — double-buy guard.
    if not _client_has_avatar_tier(client_id):
        tiers += get_avatar_upgrade_tiers(sub["monthly_fee"])
    # Standalone "YouTube alone" add-on: exact +50/mo, directly billable like the
    # avatar tiers (additive), offered until the client already has it.
    if not _client_has_youtube_addon(client_id):
        tiers.append(get_youtube_standalone_addon(sub["monthly_fee"]))
    # addons: PRICING-derived at call time (new site/SEO/automation) — scope-
    # dependent pricing, so priced via the in-chat proposal brain instead
    addons = get_upgrade_addons()
    return {"success": True, "data": {
        "available": bool(tiers) or bool(addons),
        "current": {"package": client.get("package", ""), "monthly_fee": sub["monthly_fee"]},
        "tiers": tiers,
        "addons": addons,
    }}

class UpgradeRequest(BaseModel):
    tier_id: str

@app.post("/api/client/upgrade")
def client_upgrade(req: UpgradeRequest, request: Request):
    from agents.onboarding_agent import get_avatar_upgrade_tiers, get_upgrade_tiers
    client_id = _require_session(request)
    sub = _client_subscription_info(client_id)
    if not sub["subscription_id"]:
        raise HTTPException(status_code=400, detail={"code": "ERR_NO_ACTIVE_SUBSCRIPTION"})

    # Avatar + YouTube-standalone add-ons are computed per client (additive to
    # the CURRENT fee) — always recomputed server-side here, never trusted from
    # the panel.
    if req.tier_id.startswith("avatar_"):
        if _client_has_avatar_tier(client_id):
            raise HTTPException(status_code=400, detail={"code": "ERR_TIER_UNAVAILABLE"})
        candidates = get_avatar_upgrade_tiers(sub["monthly_fee"])
    elif req.tier_id == "youtube_standalone":
        if _client_has_youtube_addon(client_id):
            raise HTTPException(status_code=400, detail={"code": "ERR_TIER_UNAVAILABLE"})
        from agents.onboarding_agent import get_youtube_standalone_addon
        candidates = [get_youtube_standalone_addon(sub["monthly_fee"])]
    else:
        candidates = get_upgrade_tiers()
    tier = next((t for t in candidates if t["id"] == req.tier_id), None)
    if not tier or tier["monthly_fee"] <= sub["monthly_fee"]:
        raise HTTPException(status_code=400, detail={"code": "ERR_TIER_UNAVAILABLE"})

    try:
        # The upgraded plan must live under the same PayPal product as the
        # original - recover the product through the live subscription, since
        # checkout never stored the product id
        current_plan_id = get_subscription_status(sub["subscription_id"]).get("plan_id")
        product_id = get_plan(current_plan_id).get("product_id")
        new_plan_id = create_plan(product_id, f"uallak ניהול חודשי — {tier['name']}", tier["monthly_fee"])

        public_url = os.environ.get("PUBLIC_APP_URL", "https://app.uallak.com")
        revision = revise_subscription_plan(
            sub["subscription_id"], new_plan_id,
            return_url=(f"{public_url}/api/upgrade-success?client_id={client_id}"
                        f"&subscription_id={sub['subscription_id']}&plan_id={new_plan_id}&tier_id={tier['id']}"),
            cancel_url=f"{public_url}/dashboard/",
        )
        if not revision.get("approve_url"):
            raise RuntimeError("PayPal revision returned no approve link")

        log_activity(client_id, "paypal_service", "upgrade_requested",
                     {"tier_id": tier["id"], "tier_name": tier["name"],
                      "monthly_fee": tier["monthly_fee"], "new_plan_id": new_plan_id},
                     {"subscription_id": sub["subscription_id"]})
        return {"success": True, "approve_url": revision["approve_url"]}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail={"code": "ERR_UPGRADE_FAILED"})

@app.get("/api/upgrade-success")
def upgrade_success(client_id: int, subscription_id: str = "", plan_id: str = "", tier_id: str = ""):
    from agents.onboarding_agent import get_upgrade_tiers
    try:
        # Unauthenticated redirect from PayPal - only act if PayPal itself
        # confirms the subscription now sits on the plan we created
        if not (subscription_id and plan_id):
            return RedirectResponse(url="/dashboard/?upgrade=pending")
        live = get_subscription_status(subscription_id)
        if live.get("plan_id") != plan_id or live.get("status") != "ACTIVE":
            print(f"[upgrade_success] client {client_id}: live plan {live.get('plan_id')} != expected {plan_id}")
            return RedirectResponse(url="/dashboard/?upgrade=pending")

        if tier_id.startswith("avatar_"):
            # Recompute with the same basis the POST used — the old fee row is
            # still the newest subscription_created at this point
            from agents.onboarding_agent import get_avatar_upgrade_tiers
            sub_now = _client_subscription_info(client_id)
            tier = next((t for t in get_avatar_upgrade_tiers(sub_now["monthly_fee"])
                         if t["id"] == tier_id), None)
        elif tier_id == "youtube_standalone":
            from agents.onboarding_agent import get_youtube_standalone_addon
            sub_now = _client_subscription_info(client_id)
            tier = get_youtube_standalone_addon(sub_now["monthly_fee"])
        else:
            tier = next((t for t in get_upgrade_tiers() if t["id"] == tier_id), None)
        if tier:
            if tier_id.startswith("avatar_"):
                # Avatar ADDS to the package, never replaces its name — and the
                # tier assignment avatar_agent's generation gates need is
                # deterministic bookkeeping, safe to auto-run on payment
                client_row = get_client(client_id)
                current_package = client_row.get("package") or ""
                package_name = f"{current_package} + {tier['name']}" if current_package else tier["name"]
                from agents.avatar_agent import set_tier
                set_tier(client_id, tier["avatar_tier_id"])
                # One-time setup fee: auto-invoiced via the SAME create_invoice
                # mechanism checkout already uses for the original setup fee —
                # a plan revision can't charge it, but an invoice collects it
                # without a manual step. NOTE the honest limit: an invoice is a
                # payment REQUEST the client pays from their email, not an
                # instant charge — identical to how the checkout setup fee
                # already works (accepted house mechanism), so avatar reaches
                # parity, not beyond it. Non-fatal: an invoice failure never
                # breaks the upgrade itself (same idiom as checkout).
                invoice_note = ""
                try:
                    invoice = create_invoice(
                        client_id=client_id,
                        amount=tier["setup_fee_addition"],
                        description=f"uallak — דמי הקמה אווטאר דיגיטלי ({tier['name']})",
                        client_name=client_row.get("name", ""),
                        client_email=client_row.get("email", ""),
                        address=client_row.get("address", ""),
                        business_name=client_row.get("business_name", ""),
                        business_tax_id=client_row.get("business_tax_id", ""),
                    )
                    log_activity(client_id, "paypal_service", "invoice_sent",
                                 {"setup_fee": tier["setup_fee_addition"], "reason": "avatar_addon"},
                                 invoice)
                    invoice_note = (f"Setup-fee invoice ({tier['setup_fee_addition']} ILS) auto-sent "
                                    f"({invoice.get('invoice_number', '')}) — follow up only if it "
                                    "goes unpaid (PayPal dashboard shows status).")
                except Exception as invoice_err:
                    log_activity(client_id, "paypal_service", "invoice_failed",
                                 {"error": str(invoice_err), "reason": "avatar_addon"}, {})
                    invoice_note = (f"Setup-fee invoice FAILED to send ({invoice_err}) — collect the "
                                    f"{tier['setup_fee_addition']} ILS manually.")
                from core.agent_base import agent_alert as _alert
                _alert("support_agent", [
                    f"client {client_id} self-purchased the avatar add-on ({tier['name']}, "
                    f"+{tier['addon_monthly']} ILS/mo — recurring now billing via PayPal). "
                    f"{invoice_note} "
                    "REMAINING MANUAL STEP: walk them through avatar onboarding — HeyGen key "
                    "+ consent in the dashboard card, then the source kit. Tier is already assigned."])
            elif tier_id == "youtube_standalone":
                # Additive add-on: appends to the package name (never replaces),
                # same as avatar. The 'addon_activated' row is what
                # _client_has_youtube_addon reads to stop re-offering it.
                client_row = get_client(client_id)
                current_package = client_row.get("package") or ""
                package_name = f"{current_package} + {tier['name']}" if current_package else tier["name"]
                log_activity(client_id, "youtube_content_agent", "addon_activated",
                             {"addon_monthly": tier["addon_monthly"]},
                             {"subscription_id": subscription_id})
                from core.agent_base import agent_alert as _alert
                _alert("support_agent", [
                    f"client {client_id} self-purchased the standalone YouTube add-on "
                    f"(+{tier['addon_monthly']} ILS/mo, recurring via PayPal). REMAINING MANUAL "
                    "STEP: if their YouTube channel isn't connected yet, point them to the "
                    "Connect flow; the youtube_content_agent handles uploads once connected."])
            else:
                package_name = tier["name"]
            update_client_package(client_id, package_name)
            # Logged as subscription_created so the fee derivations (dashboard
            # + admin MRR) pick up the new amount - they read the newest row
            log_activity(client_id, "paypal_service", "subscription_created",
                         {"package_name": package_name, "monthly_management_total": tier["monthly_fee"],
                          "setup_fee_total": tier["setup_fee_addition"], "upgrade": True},
                         {"subscription_id": subscription_id})
        return RedirectResponse(url="/dashboard/?upgrade=success")
    except Exception:
        import traceback
        traceback.print_exc()
        return RedirectResponse(url="/dashboard/?upgrade=error")

@app.get("/api/client/billing")
def client_billing(request: Request):
    client_id = _require_session(request)
    client = get_client(client_id)
    sub = _client_subscription_info(client_id)

    transactions, next_billing = [], None
    if sub["subscription_id"]:
        try:
            next_billing = get_subscription_status(sub["subscription_id"]).get("next_billing_time")
        except Exception as e:
            print(f"[billing] subscription status failed for client {client_id}: {e}")
        try:
            start = sub["checkout_at"] or (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
            transactions = list_subscription_transactions(
                sub["subscription_id"], start, datetime.now(timezone.utc).isoformat()
            )
        except Exception as e:
            print(f"[billing] transactions fetch failed for client {client_id}: {e}")

    return {"success": True, "data": {
        "package": client.get("package", ""),
        "monthly_fee": sub["monthly_fee"],
        "setup_fee": sub["setup_fee"],
        # The setup fee rides PayPal's native payment_preferences.setup_fee on the
        # plan (setup_fee_failure_action=CANCEL) - it's charged in the same approval
        # as the first payment, so once the subscription is live (client status
        # flips to "active" in _mark_paid_and_notify) the setup fee was collected too.
        "setup_fee_charged": bool(sub["setup_fee"]) and client.get("status") == "active",
        "next_billing": next_billing,
        "transactions": transactions,
    }}

# ─── First-login interview + login moments + journey (2026-08-02) ────────────

class InterviewTurnRequest(BaseModel):
    message: str = ""      # empty = the opening turn
    language: str = "he"   # the client's chosen UI language

@app.post("/api/client/interview")
def client_interview_turn(req: InterviewTurnRequest, request: Request):
    # The first-login conversation that replaced the static tour. Plain `def`:
    # one blocking LLM call per turn.
    client_id = _require_session(request)
    from agents.interview_agent import start_or_continue
    result = start_or_continue(client_id, req.message, req.language)
    if not result.get("success"):
        # The deep interview waits for every required connection — the UI shows
        # the walkthrough instead of an error.
        raise HTTPException(status_code=409,
                            detail={"code": result.get("code", "ERR_INTERVIEW_NOT_READY")})
    return {"success": True, "data": result}

@app.get("/api/client/interview/state")
def client_interview_state(request: Request):
    client_id = _require_session(request)
    from agents.interview_agent import captured_facts, readiness
    facts = captured_facts(client_id)
    state = readiness(client_id)
    return {"success": True, "data": {
        "completed": state["completed"],
        "ready": state["ready"],
        "connection_status": state["connection_status"],
        "competitors_captured": len(facts.get("competitors") or []),
    }}

@app.get("/api/client/connection-checklist")
def client_connection_checklist(request: Request):
    """The ordered, package-derived connection walkthrough — one next step at a
    time. `resolved: false` means we could not read the purchased package; the
    UI must say so rather than implying nothing is required."""
    client_id = _require_session(request)
    from core import client_journey
    try:
        status = client_journey.connection_status(client_id)
        # Stamp the 90-day clock start the first time the last step lands.
        # Idempotent and a no-op unless the checklist is resolved AND complete.
        completion = client_journey.note_connections_complete(client_id)
        return {"success": True, "data": {**status,
                                          "completed_at": completion.get("at", ""),
                                          "newly_completed": completion.get("newly_completed", False)}}
    except Exception as e:
        print(f"[connections] client {client_id}: {e}")
        raise HTTPException(status_code=503, detail={"code": "ERR_CHECKLIST_UNAVAILABLE"})

class ConnectionNudgeRequest(BaseModel):
    language: str = "he"

@app.post("/api/client/connection-nudge")
def client_connection_nudge(req: ConnectionNudgeRequest, request: Request):
    # The first-login urgency message, naming the client's REAL next step.
    # Returns an empty message when nothing is missing or they were already
    # nudged today. Plain `def`: one blocking LLM call.
    client_id = _require_session(request)
    from agents.interview_agent import connection_nudge
    return {"success": True, "data": connection_nudge(client_id, req.language)}

class LoginMomentRequest(BaseModel):
    language: str = "he"

@app.post("/api/client/login-moment")
def client_login_moment(req: LoginMomentRequest, request: Request):
    # Fired once per day when the client opens the dashboard. An empty message
    # is a normal, frequent outcome — nothing was worth saying. Plain `def`:
    # may make one blocking LLM call.
    client_id = _require_session(request)
    from agents.engagement_agent import run_login_moment
    return {"success": True, "data": run_login_moment(client_id, req.language)}

class FeatureAnnouncementRequest(BaseModel):
    language: str = "he"

@app.post("/api/client/feature-announcement")
def client_feature_announcement(req: FeatureAnnouncementRequest, request: Request):
    # The second login trigger: at most ONE pending, relevant, not-yet-told
    # feature announcement, delivered into the owning persona's chat thread.
    # An empty message is the normal outcome. Plain `def`: may make one
    # blocking LLM call.
    client_id = _require_session(request)
    from agents.engagement_agent import run_feature_announcement
    return {"success": True, "data": run_feature_announcement(client_id, req.language)}

class ClientFeedbackRequest(BaseModel):
    message: str = ""
    rating: int = None     # 1-5, optional — never inferred from the text
    source: str = "weekly_checkin"

@app.post("/api/client/feedback")
def client_feedback_submit(req: ClientFeedbackRequest, request: Request):
    client_id = _require_session(request)
    from agents.engagement_agent import store_feedback
    result = store_feedback(client_id, req.message, req.rating, req.source)
    if not result.get("success"):
        raise HTTPException(status_code=503, detail={"code": "ERR_FEEDBACK_UNAVAILABLE"})
    return {"success": True, "data": result}

@app.get("/api/client/journey")
def client_journey_view(request: Request):
    # The progress timeline. Every milestone is DERIVED from rows that already
    # exist — see core/client_journey.py on why there is no milestones table.
    client_id = _require_session(request)
    from core import client_journey
    try:
        return {"success": True, "data": client_journey.journey(client_id)}
    except Exception as e:
        print(f"[journey] client {client_id}: {e}")
        raise HTTPException(status_code=503, detail={"code": "ERR_JOURNEY_UNAVAILABLE"})

@app.get("/api/admin/feedback", dependencies=_admin_only)
def admin_list_feedback(only_unreviewed: bool = False, limit: int = 100):
    from agents.engagement_agent import list_feedback
    return {"success": True, "data": list_feedback(limit, only_unreviewed)}

@app.post("/api/admin/feedback/{feedback_id}/reviewed", dependencies=_admin_only)
def admin_mark_feedback_reviewed(feedback_id: int):
    from agents.engagement_agent import mark_feedback_reviewed
    return {"success": True, "data": mark_feedback_reviewed(feedback_id)}

@app.post("/api/client/tour-completed")
def client_tour_completed(request: Request):
    client_id = _require_session(request)
    log_activity(client_id, "dashboard", "welcome_tour_completed", {}, {})
    return {"success": True}

@app.post("/api/logout")
async def client_logout(response: Response):
    response.delete_cookie("session", path="/")
    return {"success": True}

# ─── Platform disconnect (client-facing) ─────────────────────────────────────

# UI platform -> the client_accounts rows that platform's connect flow created.
# One Meta consent stores up to three rows (ad account, Page, Instagram), and
# they all die together - the revoke kills the single underlying grant.
_DISCONNECT_GROUPS = {
    "google_ads": ["google_ads"],
    "meta": ["meta_ads", "meta_page", "meta_instagram", "meta_pending"],
    "tiktok": ["tiktok"],
    "youtube": ["youtube", "youtube_pending"],
    "gtm": ["google_tagmanager"],
    "merchant_center": ["merchant_center", "merchant_center_pending"],
    "wordpress": ["wordpress"],
    "higgsfield": ["higgsfield"],
    # Each creative tool on its own (core/vendor_tools): disconnecting one
    # never touches another. Like wordpress/higgsfield, the keys live on the
    # client's own accounts and are revocable there - deleting our copy is
    # our half.
    "heygen": ["heygen"],
    "elevenlabs": ["elevenlabs"],
    # LEGACY group, kept only so an old dashboard tab still works; the
    # current dashboard disconnects each tool separately
    "avatar": ["heygen", "elevenlabs"],
    # Google verification round 2 (flags off by default)
    "gmail": ["gmail_send"],
    "search_console": ["search_console"],
    "analytics": ["google_analytics"],
    # The client's OWN Drive (drive.file): disconnecting revokes our grant;
    # the uallak folder and its files stay theirs, in their Drive
    "google_drive": ["google_drive_file"],
    # Keys the client created in their own CRM / automation accounts
    # (core/ownership): disconnecting deletes our copy, nothing at the vendor
    "crm": ["crm"],
    "manychat": ["manychat"],
    "make": ["make"],
}

# Groups whose grant can be revoked at the provider; every other group's
# disconnect is deleting our stored copy (see the notes in _revoke_grant)
_REVOCABLE_GROUPS = ("google_ads", "gtm", "youtube", "merchant_center", "meta", "gmail", "search_console",
                     "analytics", "google_drive", "wordpress")

def _revoke_wordpress_password(rows: list, secret: str):
    """Delete OUR Application Password on the client's site — the one we
    authenticate with, found by introspecting it. Nothing else on the site
    is touched (no other password, user, post or page). True = deleted,
    False = not confirmed (site down, WP < 5.7), None = nothing stored."""
    row = next((r for r in rows if r.get("platform") == "wordpress"), None)
    if not row or ":" not in (secret or ""):
        return None
    from core import wordpress_service
    site_url = row.get("account_id") or ""
    username, password = secret.split(":", 1)
    try:
        uuid = (wordpress_service.introspect_application_password(site_url, username, password) or {}).get("uuid")
        if not uuid:
            return False
        wordpress_service.delete_application_password(site_url, username, password, uuid)
        return True
    except Exception as e:
        print(f"[disconnect] wordpress password not deleted on the site: {type(e).__name__}")
        return False

def _revoke_grant(client_id: int, platform: str, rows: list = None):
    """Revoke our access at the provider for ONE disconnect group. Never
    deletes a row. True = revoked (or already dead), False = not confirmed,
    None = nothing to revoke remotely. `rows`: the client's accounts in this
    group (read here when not given)."""
    from core.secrets_vault import get_client_secret
    if platform not in _REVOCABLE_GROUPS:
        return None
    if rows is None:
        rows = [a for a in get_accounts(client_id) if a.get("platform") in _DISCONNECT_GROUPS[platform]]
    # The token is decrypted only here, for the revoke call itself
    tokens = {r.get("platform"): get_client_secret(client_id, r.get("platform"), any_status=True)
              for r in rows if r.get("has_secret")}

    revoked = None
    if platform in ("google_ads", "gtm", "youtube", "merchant_center", "gmail", "search_console",
                    "analytics", "google_drive"):
        # Same Google OAuth infrastructure — revoking the refresh token kills
        # the grant regardless of which scopes it carried. Every token must
        # be confirmed, not just the last one tried.
        for token in tokens.values():
            if token:
                ok = google_ads_service.revoke_token(token)
                revoked = ok if revoked is None else (revoked and ok)
    elif platform == "meta":
        # Any of the group's tokens traces back to the same user grant; prefer
        # the user token on the meta_ads row, fall back to whatever exists
        token = tokens.get("meta_ads") or next((t for t in tokens.values() if t), "")
        if token:
            revoked = meta_service.revoke_permissions(token)
    elif platform == "wordpress":
        # Our Application Password is deleted ON the site (core/ownership:
        # leaving removes our access everywhere); the client can also delete
        # it in their own wp-admin, and deleting our stored copy follows.
        revoked = _revoke_wordpress_password(rows, tokens.get("wordpress"))
    # higgsfield: the API key lives on the client's own
    # Higgsfield account; they can revoke it themselves at
    # cloud.higgsfield.ai/api-keys, and deleting our stored copy is our half.
    return revoked

def _disconnect_platform(client_id: int, platform: str) -> dict:
    """Revoke our access at the provider (best-effort - the grant may already
    be dead) and DELETE the stored client_accounts rows. Only OUR access is
    removed; the client's ad accounts / Pages / site are never touched."""
    rows = [a for a in get_accounts(client_id) if a.get("platform") in _DISCONNECT_GROUPS[platform]]
    revoked = _revoke_grant(client_id, platform, rows)

    removed = remove_accounts(client_id, _DISCONNECT_GROUPS[platform])

    # Drop stale in-memory caches so a reconnect doesn't serve the old account's data
    from agents import google_ads_agent, meta_ads_agent, website_agent
    from core import gtm_connection
    gtm_connection.forget(client_id)
    google_ads_agent._perf_cache.pop(client_id, None)
    meta_ads_agent._perf_cache.pop(client_id, None)
    website_agent._overview_cache.pop(client_id, None)
    if platform == "search_console":
        from core import rank_tracking
        for provider in rank_tracking.PROVIDERS:
            if provider.id == "search_console":
                provider.forget(client_id)
    if platform == "analytics":
        from core import site_traffic
        site_traffic.forget(client_id)
    if platform == "google_drive":
        # Files stay in the client's Drive; we stop writing there. New files
        # go to our Shared Drive again (core/drive_storage)
        from core import drive_storage
        drive_storage.forget(client_id)

    return {"removed": removed, "revoked": revoked}

class DisconnectRequest(BaseModel):
    platform: str  # google_ads | meta | wordpress

@app.post("/api/client/disconnect")
def client_disconnect(req: DisconnectRequest, request: Request):
    # Plain `def`: the revoke is a blocking HTTP call to the provider
    client_id = _require_session(request)
    if req.platform not in _DISCONNECT_GROUPS:
        raise HTTPException(status_code=400, detail={"code": "ERR_UNKNOWN_PLATFORM"})
    result = _disconnect_platform(client_id, req.platform)
    if not result["removed"]:
        raise HTTPException(status_code=404, detail={"code": "ERR_NOT_CONNECTED"})
    log_activity(client_id, "client_agent", "account_disconnected",
                 {"platform": req.platform, "revoked": result["revoked"]}, {})
    return {"success": True, "data": result}

# ─── External cost transparency (client-facing) ──────────────────────────────

@app.get("/api/client/external-costs")
def client_external_costs(request: Request):
    """What the client pays external platforms/tools DIRECTLY (their own
    card, never through us) - the transparency every proposal's honest_note
    promises. Delegates to budget_agent, which is the single place this
    cross-agent aggregation now lives (ad spend via get_campaign_performance,
    plus Higgsfield/HeyGen/ElevenLabs/SEO-tool visibility, each honestly
    labeled hard/estimate/unknown - see the budget skill). Plain `def`:
    blocking calls to the ad APIs."""
    client_id = _require_session(request)
    from agents.budget_agent import get_client_facing_costs
    return {"success": True, "data": get_client_facing_costs(client_id)}

# ─── Data export + closure / transfer protocols (client-facing) ──────────────

def _collect_campaign_performance(client_id: int) -> dict:
    """Last-30-days snapshot per connected ad platform (never raises - the
    agents' getters return error dicts instead)."""
    performance = {}
    connected = {a.get("platform") for a in get_accounts(client_id) if a.get("status") == "active"}
    if "google_ads" in connected:
        from agents.google_ads_agent import get_campaign_performance as google_perf
        performance["google_ads"] = google_perf(client_id)
    if "meta_ads" in connected:
        from agents.meta_ads_agent import get_campaign_performance as meta_perf
        performance["meta_ads"] = meta_perf(client_id)
    return performance

def _build_client_export(client_id: int, performance_override: dict = None) -> dict:
    """Everything we hold about the client, as one JSON-able dict. Three
    consumers: the client's own download (/api/client/data-export), the
    attachment on the closure/transfer confirmation email, and the Google
    Drive archive that replaces the live DB rows after offboarding.

    performance_override: the offboarding flow snapshots campaign performance
    BEFORE disconnecting the platforms and passes it in here - by the time the
    export is built the accounts are already gone."""
    client = get_client(client_id)

    # Billing info comes straight from the newest checkout activity row -
    # deliberately NOT _client_subscription_info, whose cancel-sentinel exists
    # for live flows: the archive must keep billing history even (especially)
    # when the subscription was cancelled moments ago.
    sub = {"monthly_fee": 0, "setup_fee": 0, "subscription_id": None, "checkout_at": None}
    subscription_cancelled = False
    for entry in get_activity(client_id, limit=200):
        if entry.get("agent_name") != "paypal_service":
            continue
        if entry.get("action_type") == "subscription_cancelled":
            subscription_cancelled = True
        elif entry.get("action_type") == "subscription_created":
            details = entry.get("details") or {}
            sub = {"monthly_fee": details.get("monthly_management_total") or 0,
                   "setup_fee": details.get("setup_fee_total") or 0,
                   "subscription_id": (entry.get("result") or {}).get("subscription_id"),
                   "checkout_at": entry.get("created_at")}
            break

    transactions = []
    if sub["subscription_id"]:
        try:
            # PayPal keeps a cancelled subscription's transaction history readable
            start = sub["checkout_at"] or (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
            transactions = list_subscription_transactions(
                sub["subscription_id"], start, datetime.now(timezone.utc).isoformat())
        except Exception as e:
            print(f"[data_export] transactions fetch failed for client {client_id}: {e}")

    performance = (performance_override if performance_override is not None
                   else _collect_campaign_performance(client_id))

    export = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "client": {k: client.get(k) for k in
                   ("id", "name", "email", "phone", "package", "status", "created_at",
                    "address", "business_name", "business_tax_id")},
        "subscription": {"monthly_fee": sub["monthly_fee"], "setup_fee": sub["setup_fee"],
                          "subscription_id": sub["subscription_id"],
                          "cancelled": subscription_cancelled},
        "transactions": transactions,
        # platform + account id only - stored credentials are never exported
        "connections": [{"platform": a.get("platform"), "account_id": a.get("account_id"),
                          "status": a.get("status")} for a in get_accounts(client_id)],
        "campaign_performance_last_30_days": performance,
        "activity": get_activity(client_id, limit=500),
        "communications": get_communications(client_id, limit=500),
    }

    # The client's OWN leads - people who approached THEIR business. The
    # offboarding purge deletes these rows, so the export has to carry them or
    # a leaving client loses their customers' contact details. Never fatal:
    # the table may not exist yet (migration not run), and a missing leads
    # block must not sink the archive that everything else depends on.
    try:
        export["my_leads"] = client_leads_service.list_leads(client_id, limit=1000)
    except Exception as e:
        print(f"[data_export] client_leads unavailable for client {client_id}: {e}")
        export["my_leads"] = []

    return export

@app.get("/api/client/data-export")
def client_data_export(request: Request):
    # Plain `def`: hits PayPal + both ad platforms
    client_id = _require_session(request)
    export = _build_client_export(client_id)
    return Response(
        content=json.dumps(export, ensure_ascii=False, indent=2, default=str),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="uallak-export-{client_id}.json"'},
    )

# Live tables that get purged once the Drive archive is verified durable.
# client_accounts is normally already empty (the disconnect step deletes it) -
# it's listed anyway as a catch-all for stray rows from platforms outside the
# disconnect groups. Not purged here: leads (pre-client sales funnel records).
# The client's alerts and weekly_reports entries are removed earlier in the
# run by google_data_purge.end_of_client, together with every Google-bearing
# live row, so none of that depends on the archive succeeding.
_ARCHIVE_PURGE_TABLES = ("client_accounts", "client_agents", "client_activity",
                          "client_communications", "client_suggestions",
                          "client_costs", "login_codes", "client_leads")

def _archive_and_purge_client(client_id: int, client: dict, export_json: str) -> dict:
    """Retention model (business decision, 2026-07): the Drive archive - one
    subfolder per client under DRIVE_ARCHIVE_FOLDER_ID - is the long-term
    record for offboarded clients; the live DB keeps only a PII-stripped
    tombstone row. Purge runs ONLY after Drive confirms a non-empty file:
    if the archive can't be written, the records stay put and an alert asks
    for a manual retry (POST /api/admin/clients/{id}/archive)."""
    if not drive_service.is_configured():
        alert("client_offboarding", [
            f"client {client_id}: Drive archive NOT configured (GOOGLE_SERVICE_ACCOUNT_JSON / "
            f"DRIVE_ARCHIVE_FOLDER_ID missing) - records retained in DB. After setup, run "
            f"POST /api/admin/clients/{client_id}/archive to archive + purge."])
        return {"archived": False, "purged": False, "reason": "drive_not_configured"}

    try:
        folder_name = f"client-{client_id} — {(client.get('name') or 'unnamed').strip()}"
        folder_id = drive_service.ensure_folder(folder_name, drive_service.archive_root_folder_id())
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
        uploaded = drive_service.upload_json(
            folder_id, f"uallak-export-{client_id}-{stamp}.json", export_json)
        if not uploaded.get("id") or int(uploaded.get("size") or 0) <= 0:
            raise RuntimeError(f"Drive did not confirm a durable file: {uploaded}")
    except Exception as e:
        alert("client_offboarding", [
            f"client {client_id}: Drive archive FAILED ({e}) - records retained in DB. "
            f"Retry with POST /api/admin/clients/{client_id}/archive."])
        return {"archived": False, "purged": False, "reason": str(e)}

    purge_errors = []
    for table in _ARCHIVE_PURGE_TABLES:
        try:
            db.table(table).delete().eq("client_id", client_id).execute()
        except Exception as e:
            purge_errors.append(f"{table}: {e}")
    try:
        # Johnny's Control Room conversations about this client. Outside the
        # loop above: before migrations/2026-10-13-admin-agent-chat.sql runs
        # the table doesn't exist, and that must not read as a failed purge.
        db.table("admin_agent_chat").delete().eq("client_id", client_id).execute()
    except Exception as e:
        print(f"[offboarding] admin_agent_chat purge skipped for client {client_id}: {e}")
    try:
        # Tombstone: id/name/package/status/created_at stay for the admin list;
        # every contact/PII field goes. An emptied email also backstops the
        # login hard-lock - request-code starts from an email lookup.
        db.table("clients").update({
            "email": "", "phone": "", "address": "", "business_name": "",
            "business_tax_id": "", "profile_image": None, "owner_gender": None,
        }).eq("id", client_id).execute()
    except Exception as e:
        purge_errors.append(f"clients tombstone: {e}")
    client_auth.wipe_credentials(client_id)
    if purge_errors:
        alert("client_offboarding",
              [f"client {client_id}: archive OK (Drive file {uploaded['id']}) but purge "
               f"partially failed - clean up manually: {'; '.join(purge_errors)}"])

    return {"archived": True, "purged": not purge_errors, "drive_file_id": uploaded["id"]}

def _offboard_client(client_id: int, mode: str, reason: str = "") -> dict:
    """Shared closure/transfer flow. Order matters: billing stops FIRST (the
    whole point of both protocols is that a leaving client is never charged
    again), then platform access is revoked, then the record flips. Raises if
    the PayPal cancel fails - we must never report success while billing might
    continue. mode: 'closure' -> status 'closed', 'transfer' -> 'transferred'."""
    from core import google_data_purge
    client = get_client(client_id)
    if not client:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if client.get("status") in ("closed", "transferred"):
        return {"status": client["status"], "already_offboarded": True}
    run_started = google_data_purge.now()   # alerts from here on are this run's own record

    # 1 - stop billing
    sub = _client_subscription_info(client_id)
    subscription_cancelled = False
    if sub["subscription_id"]:
        cancel_reason = ("Client closed their account" if mode == "closure"
                         else "Client transferred to another agency")
        result = cancel_subscription(sub["subscription_id"], reason=cancel_reason)
        if result.get("cancelled"):
            subscription_cancelled = True
        else:
            # Cancel is only valid on a live subscription - if PayPal says it's
            # already cancelled/expired, billing is stopped and that's what counts
            try:
                live = get_subscription_status(sub["subscription_id"]).get("status")
            except Exception:
                live = "UNKNOWN"
            if live not in ("CANCELLED", "EXPIRED"):
                alert("client_offboarding", [
                    f"client {client_id} ({client.get('name')}): PayPal cancel FAILED for "
                    f"subscription {sub['subscription_id']} (live status: {live}) during {mode} - "
                    f"CANCEL IT MANUALLY NOW or the client keeps getting billed"
                ])
                raise HTTPException(status_code=502, detail=(
                    "ביטול המנוי ב-PayPal נכשל, ולכן לא השלמנו את הבקשה - "
                    "הצוות קיבל התראה ויטפל בביטול באופן ידני בהקדם. אפשר גם לפנות אלינו בצ'אט."))
        log_activity(client_id, "paypal_service", "subscription_cancelled",
                     {"mode": mode}, {"subscription_id": sub["subscription_id"]})

    # 2 - snapshot campaign performance BEFORE the disconnect kills our access;
    # it goes into the export/archive built at the end of this flow
    performance_snapshot = _collect_campaign_performance(client_id)

    # 3 - revoke our access everywhere (the client's accounts are untouched)
    disconnected = []
    for platform in _DISCONNECT_GROUPS:
        try:
            if _disconnect_platform(client_id, platform)["removed"]:
                disconnected.append(platform)
        except Exception as e:
            # Credentials that failed to delete must not block the offboarding -
            # but they are exactly what closure promises to remove, so alert
            alert("client_offboarding",
                  [f"client {client_id}: disconnect of {platform} failed during {mode}: {e}"])

    # 4 - flip the record; the hard lock in _require_session and the login
    # endpoints keys off this status
    new_status = "closed" if mode == "closure" else "transferred"
    update_client_status(client_id, new_status)
    log_activity(client_id, "client_agent",
                 "account_closed" if mode == "closure" else "account_transferred",
                 {"reason": reason, "subscription_cancelled": subscription_cancelled,
                  "disconnected": disconnected}, {})

    # 5 - build the final export NOW (it must capture the offboarding activity
    # rows just logged, and it must exist before the purge below deletes them).
    # Google-sourced data is left out of it (core/google_data_purge): it goes
    # to the long-term Drive archive and through our own mailbox, and the
    # client's Google data stays in their Google accounts anyway.
    export_json = ""
    try:
        export_json = json.dumps(
            google_data_purge.archive_safe_export(_build_client_export(client_id, performance_snapshot)),
            ensure_ascii=False, indent=2, default=str)
    except Exception as e:
        alert("client_offboarding",
              [f"client {client_id}: export build failed during {mode}: {e}"])

    # 6 - written confirmation to the client, with their data as an attachment
    # (the hard lock means this email is their only self-service copy)
    try:
        if client.get("email"):
            export_name = f"uallak-export-{client_id}.json" if export_json else ""
            if mode == "closure":
                send_account_closed(client["email"], client.get("name", ""),
                                    export_name, export_json, client_id)
            else:
                send_account_transferred(client["email"], client.get("name", ""),
                                         export_name, export_json, client_id)
    except Exception as e:
        print(f"[offboarding] confirmation email failed for client {client_id} (non-fatal): {e}")

    # 6b - Google-sourced data goes NOW, whatever happens to the archive below:
    # Google-bearing live rows, the client's weekly-report entries and alerts,
    # each verified (privacy policy section 9)
    google = google_data_purge.end_of_client(client_id, run_started)
    if google["leftovers"] or google["unverified"] or google["error"]:
        alert("client_offboarding", [
            f"client {client_id}: Google data NOT fully removed during {mode} - "
            f"still present: {google['leftovers'] or '-'}, unverified: {google['unverified'] or '-'}, "
            f"error: {google['error'] or '-'}. Re-run with POST /api/admin/clients/{client_id}/archive."])

    # 7 - long-term archive to Drive, then purge the live rows (skipped, with
    # an alert, if the export could not be built or Drive isn't configured)
    if export_json:
        archive = _archive_and_purge_client(client_id, client, export_json)
    else:
        archive = {"archived": False, "purged": False, "reason": "export_build_failed"}

    alert("client_offboarding", [
        f"client {client_id} ({client.get('name')}) completed {mode}: "
        f"subscription {'cancelled' if subscription_cancelled else 'was not live'}, "
        f"disconnected: {', '.join(disconnected) or 'nothing was connected'}, "
        f"archive: {'Drive file ' + archive['drive_file_id'] if archive.get('archived') else 'FAILED - ' + str(archive.get('reason'))}, "
        f"google data: {'removed' if not (google['leftovers'] or google['unverified']) else 'LEFTOVERS - see alert'}, "
        f"reason: {reason or '-'}"
    ])

    return {"status": new_status, "subscription_cancelled": subscription_cancelled,
            "disconnected": disconnected, "archive": archive,
            "google_data": {"removed": google["removed"],
                            "complete": not (google["leftovers"] or google["unverified"])}}

class OffboardRequest(BaseModel):
    confirm_phrase: str
    reason: str = ""

# The exact phrases the client must type - checked server-side too, so a stray
# API call can never close an account by accident. One accepted phrase per UI
# language (the profile page shows the one matching the client's language).
CLOSE_CONFIRM_PHRASES = {"סגירת חשבון", "close account", "fermer le compte",
                          "إغلاق الحساب", "закрыть аккаунт"}
TRANSFER_CONFIRM_PHRASES = {"מעבר סוכנות", "transfer agency", "changer d'agence",
                             "نقل الوكالة", "смена агентства"}

@app.post("/api/client/close-account")
def client_close_account(req: OffboardRequest, request: Request, response: Response):
    # Plain `def`: PayPal cancel + provider revokes are blocking HTTP calls
    client_id = _require_session(request)
    if req.confirm_phrase.strip().lower() not in CLOSE_CONFIRM_PHRASES:
        raise HTTPException(status_code=400, detail={"code": "ERR_INVALID_CLOSE_PHRASE"})
    result = _offboard_client(client_id, "closure", req.reason.strip())
    response.delete_cookie("session", path="/")  # closure ends the session too
    return {"success": True, "data": result}

@app.post("/api/client/transfer-out")
def client_transfer_out(req: OffboardRequest, request: Request, response: Response):
    # Hard lock applies to transfers too - the client's copy of their data
    # rides the confirmation email as an attachment, not the dashboard
    client_id = _require_session(request)
    if req.confirm_phrase.strip().lower() not in TRANSFER_CONFIRM_PHRASES:
        raise HTTPException(status_code=400, detail={"code": "ERR_INVALID_TRANSFER_PHRASE"})
    result = _offboard_client(client_id, "transfer", req.reason.strip())
    response.delete_cookie("session", path="/")
    return {"success": True, "data": result}

@app.post("/api/admin/clients/{client_id}/archive")
def admin_archive_client(client_id: int, request: Request):
    """Manual retry for the Drive archive + purge, for offboardings that ran
    while Drive was unconfigured/unreachable (the alert names this endpoint).
    Plain `def`: Drive upload + PayPal/ad-platform reads are blocking."""
    _require_admin(request)
    client = get_client(client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    if client.get("status") not in OFFBOARDED_STATUSES:
        raise HTTPException(status_code=400, detail="Client is not offboarded - archive is for closed/transferred clients only")
    from core import google_data_purge
    # Also the retry for a closure whose Google step left something behind
    google = google_data_purge.end_of_client(client_id, google_data_purge.now())
    export_json = json.dumps(google_data_purge.archive_safe_export(_build_client_export(client_id)),
                             ensure_ascii=False, indent=2, default=str)
    return {"success": True, "data": {**_archive_and_purge_client(client_id, client, export_json),
                                      "google_data": google}}

# ─── Admin: delete a client completely (core/client_deletion.py) ─────────────
# Admin-session only, never mirrored on a client endpoint. Preview first (no
# changes), backup download, then the delete itself with the typed
# confirmation — every refusal is re-checked here, not only in the UI.

@app.get("/api/admin/clients/{client_id}/delete-preview")
def admin_delete_preview(client_id: int, request: Request):
    # Plain `def`: reads PayPal + the ad platforms for the warnings
    _require_admin(request)
    from core import client_deletion
    data = client_deletion.preview(client_id, _client_subscription_info)
    if not data.get("found"):
        raise HTTPException(status_code=404, detail={"code": "ERR_CLIENT_NOT_FOUND"})
    return {"success": True, "data": data}

@app.get("/api/admin/clients/{client_id}/delete-backup")
def admin_delete_backup(client_id: int, request: Request):
    # Everything about the client EXCEPT secrets (connections are platform +
    # account id + status only; every string is redacted). Downloading it is
    # what unlocks the delete without "delete without backup".
    # Google-sourced data is left out (core/google_data_purge): this file
    # outlives the client on the admin's machine, and the privacy policy
    # promises no Google data survives a deletion.
    _require_admin(request)
    from core import client_deletion, google_data_purge
    from core.redaction import redact_obj
    if not get_client(client_id):
        raise HTTPException(status_code=404, detail={"code": "ERR_CLIENT_NOT_FOUND"})
    backup = {"backup_kind": "pre_deletion",
              **google_data_purge.archive_safe_export(_build_client_export(client_id)),
              **google_data_purge.backup_safe_extra(client_deletion.extra_backup(client_id))}
    client_deletion.mark_backup(client_id)
    return Response(
        content=json.dumps(redact_obj(backup), ensure_ascii=False, indent=2, default=str),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="uallak-client-{client_id}-backup.json"'})

class AdminDeleteClientRequest(BaseModel):
    confirm: str             # the client's exact name, or their id
    skip_backup: bool = False  # the explicit "delete without backup" tick

_DELETE_REFUSALS = {
    "not_found": (404, "ERR_CLIENT_NOT_FOUND"),
    "confirm_mismatch": (400, "ERR_CONFIRM_MISMATCH"),
    "backup_required": (400, "ERR_BACKUP_REQUIRED"),
    "in_progress": (409, "ERR_DELETE_IN_PROGRESS"),
}

@app.post("/api/admin/clients/{client_id}/delete")
def admin_delete_client(client_id: int, req: AdminDeleteClientRequest, request: Request):
    # Plain `def`: provider revokes + many deletes are blocking calls
    _require_admin(request)
    from core import client_deletion
    try:
        result = client_deletion.delete_client_full(
            client_id, req.confirm, req.skip_backup,
            revoke=_revoke_grant, revoke_groups=_DISCONNECT_GROUPS)
    except ValueError as e:
        reason = str(e)
        if reason.startswith("hard_stop:"):
            raise HTTPException(status_code=403, detail={"code": "ERR_PROTECTED_CLIENT",
                                                         "reasons": reason.split(":", 1)[1].split(",")})
        status, code = _DELETE_REFUSALS.get(reason, (400, "ERR_DELETE_REFUSED"))
        raise HTTPException(status_code=status, detail={"code": code})
    return {"success": True, "data": result}

# ─── Admin: bulk delete (core/bulk_delete.py) ────────────────────────────────
# One preview + one delete route per list kind (clients | leads |
# client_leads | alerts). Everything is re-validated here and in the module:
# the ids, the cap, protection and the typed "מחק N".

class BulkDeleteRequest(BaseModel):
    ids: list                    # preview: the selection; delete: THIS chunk
    confirm: str = ""
    skip_backup: bool = False    # clients only
    client_id: int = 0           # client_leads only: whose leads
    batch_id: str = ""           # delete: the batch this chunk belongs to
    batch_ids: list = []         # delete: the WHOLE batch, re-validated every chunk

_BULK_REFUSALS = {
    "unknown_kind": (404, "ERR_UNKNOWN_KIND"),
    "empty_selection": (400, "ERR_EMPTY_SELECTION"),
    "too_many": (400, "ERR_TOO_MANY"),
    "confirm_mismatch": (400, "ERR_CONFIRM_MISMATCH"),
    "client_required": (400, "ERR_CLIENT_REQUIRED"),
    "chunk_too_large": (400, "ERR_CHUNK_TOO_LARGE"),
    "not_in_batch": (400, "ERR_NOT_IN_BATCH"),
    "batch_mismatch": (409, "ERR_BATCH_MISMATCH"),
    "bad_batch_id": (400, "ERR_BAD_BATCH_ID"),
}

def _bulk_refusal(e: ValueError):
    from core.bulk_delete import BULK_DELETE_MAX
    status, code = _BULK_REFUSALS.get(str(e), (400, "ERR_BULK_REFUSED"))
    return HTTPException(status_code=status, detail={"code": code, "max": BULK_DELETE_MAX})

@app.post("/api/admin/bulk/{kind}/preview")
def admin_bulk_preview(kind: str, req: BulkDeleteRequest, request: Request):
    # Plain `def`: a client preview reads PayPal + the ad platforms
    _require_admin(request)
    from core import bulk_delete
    try:
        return {"success": True, "data": bulk_delete.preview(
            kind, req.ids, subscription_info=_client_subscription_info, client_id=req.client_id)}
    except ValueError as e:
        raise _bulk_refusal(e)

@app.post("/api/admin/bulk/{kind}/delete")
def admin_bulk_delete(kind: str, req: BulkDeleteRequest, request: Request):
    _require_admin(request)
    from core import bulk_delete
    # ONE chunk (at most bulk_delete.CHUNK_MAX[kind] items) per request, so no
    # request comes near the 300 s Cloud Run timeout
    try:
        return {"success": True, "data": bulk_delete.run(
            kind, req.ids, req.confirm, req.batch_id, req.batch_ids,
            skip_backup=req.skip_backup, client_id=req.client_id,
            revoke=_revoke_grant, revoke_groups=_DISCONNECT_GROUPS)}
    except ValueError as e:
        raise _bulk_refusal(e)

@app.get("/api/admin/bulk/batch/{batch_id}")
def admin_bulk_batch_status(batch_id: str, request: Request):
    # Resume after a closed window: which items of the batch are done
    _require_admin(request)
    from core import bulk_delete
    try:
        record = bulk_delete.batch_status(batch_id)
    except ValueError as e:
        raise _bulk_refusal(e)
    return {"success": True, "data": {"found": bool(record), "kind": record.get("kind"),
                                      "total": len(record.get("ids") or []),
                                      "results": record.get("results") or {},
                                      "audited": bool(record.get("audited")),
                                      # the chunk running right now: ids + current step
                                      "live": record.get("live")}}

@app.get("/api/admin/bulk/clients/backup")
def admin_bulk_clients_backup(request: Request, ids: str = ""):
    # ONE combined JSON for the selected clients, no secrets (same content as
    # the single pre-deletion backup). Marks each client's backup as taken.
    _require_admin(request)
    from core import bulk_delete, client_deletion
    from core.redaction import redact_obj
    try:
        clean = bulk_delete.validate("clients", ids.split(","))
    except ValueError as e:
        raise _bulk_refusal(e)
    combined = {"backup_kind": "pre_bulk_deletion", "exported_at": datetime.now(timezone.utc).isoformat(),
                "clients": []}
    for cid in clean:
        if not get_client(cid):
            continue
        combined["clients"].append({**_build_client_export(cid), **client_deletion.extra_backup(cid)})
        client_deletion.mark_backup(cid)
    return Response(
        content=json.dumps(redact_obj(combined), ensure_ascii=False, indent=2, default=str),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="uallak-clients-backup.json"'})

@app.get("/api/admin/clients/{client_id}/client-leads")
def admin_client_leads(client_id: int, request: Request):
    # The leads THIS client received (their customers) — read-only list for
    # the admin drawer, where bulk delete lives
    _require_admin(request)
    try:
        rows = client_leads_service.list_leads(client_id, limit=500)
    except Exception:
        rows = []   # table not migrated yet
    return {"success": True, "data": [
        {"id": r.get("id"), "name": r.get("name") or "", "phone": r.get("phone") or "",
         "email": r.get("email") or "", "status": r.get("status") or "", "source": r.get("source") or "",
         "created_at": r.get("created_at")} for r in rows]}

# ─── Google Ads OAuth (client connects their own ad account) ─────────────────

def _log_bad_oauth_state(provider: str, state: str, error: str = ""):
    # Every OAuth callback used to bounce a bad state back to the dashboard
    # without a log line, so an expired (>10 min) or tampered consent round
    # trip was indistinguishable from any other failure in Cloud Run logs.
    # Never logs the state value itself.
    reason = "missing" if not state else "invalid or expired (10-minute window)"
    print(f"[{provider} oauth] state check failed: {reason}"
          + (f" (provider error={error})" if error else ""))

# The dashboard's Google Ads card makes the client tick a staff-access consent
# (authorized uallak staff see the account + campaign performance and manage
# it) before connecting; the privacy policy's "who at uallak can see your
# data" section rests on it. Bump the version whenever that card text changes.
GOOGLE_ADS_STAFF_CONSENT = "staff_access_v1"

@app.get("/api/oauth/google-ads/start")
def google_ads_oauth_start(request: Request, consent: str = ""):
    # Plain `def`: the consent record is a blocking DB write
    client_id = _require_session(request)
    if consent != GOOGLE_ADS_STAFF_CONSENT:
        return RedirectResponse(url="/dashboard/?connect_error=google_ads_consent")
    log_activity(client_id, "client_agent", "google_ads_staff_access_consent",
                 {"version": GOOGLE_ADS_STAFF_CONSENT}, {})
    state = create_oauth_state_token(client_id)
    return RedirectResponse(url=google_ads_service.build_consent_url(state))

@app.get("/api/oauth/google-ads/callback")
def google_ads_oauth_callback(state: str = "", code: str = "", error: str = ""):
    # Identity comes from the signed state token, not the session cookie - the
    # browser arrives here from Google's domain and the state also blocks CSRF
    client_id = verify_oauth_state_token(state)
    if not client_id:
        _log_bad_oauth_state("google_ads", state, error)
        return RedirectResponse(url="/dashboard/?connect_error=google_ads")
    if error or not code:
        print(f"[google_ads oauth] client {client_id}: consent denied or no code (error={error})")
        return RedirectResponse(url="/dashboard/?connect_error=google_ads")

    try:
        tokens = google_ads_service.exchange_code(code)
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            print(f"[google_ads oauth] client {client_id}: token exchange returned no refresh_token")
            return RedirectResponse(url="/dashboard/?connect_error=google_ads")
        # Evidence for scope verification: must equal google_ads_service.ADS_SCOPES
        print(f"[google_ads oauth] client {client_id}: granted scopes {(tokens.get('scope') or '').split()}")

        # Which account: only an enabled, non-manager account is ever chosen
        # automatically, and only when it is the only one — otherwise the
        # client picks (google_ads_agent.connect_from_oauth). This used to take
        # listAccessibleCustomers()[0], which stored a wrong account for a
        # client whose login reaches six.
        from agents.google_ads_agent import connect_from_oauth, forget_grant
        outcome = connect_from_oauth(client_id, refresh_token)
        print(f"[google_ads oauth] client {client_id}: {outcome['outcome']}")
        if outcome["outcome"] != "no_accounts":
            # A NEW grant is stored: a dead record for the old one no longer
            # applies (core/google_ads_health) — clear it and the cached reads
            forget_grant(client_id)
        # Zero usable accounts is not an error the client caused: the
        # dashboard explains it plainly (create/finish an account, or talk to
        # us) instead of a "connection failed" notice
        if outcome["outcome"] in ("no_accounts", "no_enabled_account"):
            return RedirectResponse(url="/dashboard/?gads=none")
        if outcome["outcome"] == "choose":
            return RedirectResponse(url="/dashboard/?choose=google_ads")
        return RedirectResponse(url="/dashboard/?connected=google_ads")
    except google_ads_service.AdsApiVersionSunsetError as e:
        # OUR outage, not this client's. Distinct branch + admin alert because
        # the v21 sunset hid behind the generic redirect below for 13 days: the
        # client saw "try again", which is advice that could never work.
        from core.agent_base import agent_alert as _alert
        print(f"[google_ads oauth] client {client_id}: API VERSION SUNSET - {e}")
        _alert("google_ads_agent", [
            f"GOOGLE ADS API VERSION SUNSET - every Google Ads call is failing, "
            f"no client can connect (client {client_id} just hit it). {e}"])
        return RedirectResponse(url="/dashboard/?connect_error=google_ads_platform")
    except Exception as e:
        # Never swallow silently: a connect failure that only ever showed up as
        # a redirect param is a failure nobody finds out about.
        import traceback
        traceback.print_exc()
        from core.agent_base import agent_alert as _alert
        print(f"[google_ads oauth] client {client_id}: connect failed - {type(e).__name__}: {e}")
        _alert("google_ads_agent", [
            f"client {client_id}: Google Ads connect failed after consent - "
            f"{type(e).__name__}: {e}"])
        return RedirectResponse(url="/dashboard/?connect_error=google_ads")

def _round2_features(client_id: int) -> dict:
    """Which Google round-2 cards the dashboard may show. Both False while
    the flags are off — then nothing else is read."""
    from core.round2_flags import (analytics_enabled, drive_file_enabled, gmail_send_enabled,
                                   search_console_enabled)
    gmail = False
    if gmail_send_enabled():
        from core import gmail_agent_plan
        gmail = gmail_agent_plan.entitled(client_id)
    return {"gmail_send": gmail, "search_console": search_console_enabled(),
            "analytics": analytics_enabled(), "drive_file": drive_file_enabled()}

def _google_ads_account_name(client_id: int, accounts: list) -> str:
    """The connected account's name for the Connections card ("" if unknown).
    Only looked up when an active Google Ads row exists; never raises."""
    if not any(a.get("platform") == "google_ads" and a.get("status") == "active" for a in accounts):
        return ""
    try:
        from agents.google_ads_agent import connection_info
        return connection_info(client_id).get("name", "")
    except Exception as e:
        print(f"[dashboard] google ads account name unavailable for client {client_id}: {e}")
        return ""

# ─── Google Ads: choosing WHICH customer account ──────────────────────────────
# After consent, a login that reaches more than one enabled account parks the
# connection as pending_selection and the dashboard asks the client to pick.
# Plain `def`: these call the Google Ads API.

class GoogleAdsAccountChoice(BaseModel):
    customer_id: str

@app.get("/api/google-ads/accounts")
def google_ads_accounts(request: Request, refresh: bool = False):
    # The picker: every account the client's OWN stored login can reach (the
    # same listing the admin's "החלף חשבון" uses). Also serves "change
    # account" later — no new consent while the token still has access.
    client_id = _require_session(request)
    from agents.google_ads_agent import client_account_options
    return {"success": True, "data": client_account_options(client_id, refresh=refresh)}

@app.post("/api/google-ads/select-account")
def google_ads_select_account(req: GoogleAdsAccountChoice, request: Request):
    client_id = _require_session(request)
    from agents.google_ads_agent import select_account
    result = select_account(client_id, req.customer_id, chosen_by="client")
    if not result.get("success"):
        raise HTTPException(status_code=400, detail={"code": result.get("code", "ERR_SELECT_FAILED")})
    return {"success": True, "data": result}

# ─── Tag Manager: which container (the client sees and chooses) ──────────────
# core/gtm_connection. Plain `def`: Tag Manager API + homepage fetch.

@app.get("/api/gtm/connection")
def gtm_connection_status(request: Request, refresh: bool = False):
    # The GTM card's live state, loaded AFTER the dashboard: container name +
    # GTM-XXXX, does the site load it, does the stored grant still work
    client_id = _require_session(request)
    from core import gtm_connection
    return {"success": True, "data": gtm_connection.connection_view(client_id, refresh=refresh)}

@app.get("/api/gtm/containers")
def gtm_containers(request: Request):
    client_id = _require_session(request)
    from core import gtm_connection
    return {"success": True, "data": gtm_connection.container_options(client_id)}

class GtmContainerChoice(BaseModel):
    container_path: str

@app.post("/api/gtm/select-container")
def gtm_select_container(req: GtmContainerChoice, request: Request):
    client_id = _require_session(request)
    from core import gtm_connection
    result = gtm_connection.select_container(client_id, (req.container_path or "").strip())
    if not result.get("success"):
        raise HTTPException(status_code=400, detail={"code": result.get("code", "ERR_SELECT_FAILED"),
                                                     "state": result.get("state")})
    return {"success": True, "data": result["view"]}

@app.get("/api/admin/control-room/{client_id}/google-ads/accounts")
def admin_google_ads_accounts(client_id: int, request: Request, refresh: bool = False):
    # Every account the client's STORED login can reach, so the admin can
    # correct a wrong pick without the client re-running OAuth
    _require_admin(request)
    from agents.google_ads_agent import account_options
    return {"success": True, "data": account_options(client_id, refresh=refresh)}

@app.post("/api/admin/control-room/{client_id}/google-ads/account")
def admin_google_ads_select_account(client_id: int, req: GoogleAdsAccountChoice, request: Request):
    _require_admin(request)
    from agents.google_ads_agent import select_account
    result = select_account(client_id, req.customer_id, chosen_by="admin")
    if not result.get("success"):
        raise HTTPException(status_code=400, detail={"code": result.get("code", "ERR_SELECT_FAILED")})
    return {"success": True, "data": result}

# ─── Google Tag Manager OAuth (separate consent — measurement, not ads) ─────

@app.get("/api/oauth/gtm/start")
async def gtm_oauth_start(request: Request):
    client_id = _require_session(request)
    state = create_oauth_state_token(client_id)
    from core import gtm_service
    return RedirectResponse(url=gtm_service.build_consent_url(state))

@app.get("/api/oauth/gtm/callback")
def gtm_oauth_callback(state: str = "", code: str = "", error: str = ""):
    from core import gtm_service
    client_id = verify_oauth_state_token(state)
    if not client_id:
        _log_bad_oauth_state("gtm", state, error)
        return RedirectResponse(url="/dashboard/?connect_error=gtm")
    if error or not code:
        print(f"[gtm oauth] client {client_id}: consent denied or no code (error={error})")
        return RedirectResponse(url="/dashboard/?connect_error=gtm")
    try:
        tokens = gtm_service.exchange_code(code)
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            print(f"[gtm oauth] client {client_id}: token exchange returned no refresh_token")
            return RedirectResponse(url="/dashboard/?connect_error=gtm")

        # A DEFAULT only — the dashboard opens the container picker right after
        # this (?choose=gtm), showing it preselected (core/gtm_connection).
        # Prefer the container actually INSTALLED on the client's site (the
        # GTM-XXX id the tracking audit sees in the homepage HTML), else the
        # first container the grant can see.
        container = {}
        try:
            from agents.website_agent import get_tracking_status
            detected_id = ((get_tracking_status(client_id).get("detected") or {})
                           .get("gtm_container_id"))
            if detected_id:
                container = gtm_service.find_container_by_public_id(refresh_token, detected_id)
        except Exception as e:
            print(f"[gtm oauth] client {client_id}: site-container match failed ({e})")
        if not container:
            accounts = gtm_service.list_accounts(refresh_token)
            for account in accounts:
                containers = gtm_service.list_containers(refresh_token, account["path"])
                if containers:
                    container = containers[0]
                    break
        if not container:
            print(f"[gtm oauth] client {client_id}: authorized user has no GTM containers")
            return RedirectResponse(url="/dashboard/?connect_error=gtm_no_container")

        upsert_account(client_id, "google_tagmanager", container["path"], refresh_token, "active")
        # Record what Google actually GRANTED (granular consent lets a user
        # untick a scope): website_agent.gtm_missing_scopes reads this to ask
        # for a reconnect instead of failing at publish time
        granted = (tokens.get("scope") or "").split()
        log_activity(client_id, "website_agent", "gtm_api_connected",
                     {"container_path": container["path"],
                      "public_id": container.get("publicId", ""),
                      "container_name": container.get("name", ""),
                      "scopes": granted}, {})
        if gtm_service.missing_scopes(granted):
            print(f"[gtm oauth] client {client_id}: grant lacks {gtm_service.missing_scopes(granted)}")
        from core import gtm_connection
        gtm_connection.forget(client_id)
        return RedirectResponse(url="/dashboard/?connected=gtm&choose=gtm")
    except Exception:
        import traceback
        traceback.print_exc()
        return RedirectResponse(url="/dashboard/?connect_error=gtm")

# ─── YouTube OAuth (client connects their own channel — separate consent) ───

@app.get("/api/oauth/youtube/start")
async def youtube_oauth_start(request: Request):
    client_id = _require_session(request)
    state = create_oauth_state_token(client_id)
    from core import youtube_service
    return RedirectResponse(url=youtube_service.build_consent_url(state))

@app.get("/api/oauth/youtube/callback")
def youtube_oauth_callback(state: str = "", code: str = "", error: str = ""):
    from core import youtube_service
    client_id = verify_oauth_state_token(state)
    if not client_id:
        _log_bad_oauth_state("youtube", state, error)
        return RedirectResponse(url="/dashboard/?connect_error=youtube")
    if error or not code:
        print(f"[youtube oauth] client {client_id}: consent denied or no code (error={error})")
        return RedirectResponse(url="/dashboard/?connect_error=youtube")
    try:
        tokens = youtube_service.exchange_code(code)
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            print(f"[youtube oauth] client {client_id}: token exchange returned no refresh_token")
            return RedirectResponse(url="/dashboard/?connect_error=youtube")
        channel = youtube_service.get_own_channel(refresh_token)
        if not channel.get("channel_id"):
            # No channel yet - guided state, not an error (same pattern as
            # meta_content_agent's no-Page flow): park the token so the
            # detection scan can watch for the self-created channel, send
            # the guide, tell the dashboard what happens next.
            upsert_account(client_id, "youtube_pending", "pending_channel", refresh_token, "active")
            from agents.youtube_content_agent import send_channel_creation_guide
            send_channel_creation_guide(client_id)
            print(f"[youtube oauth] client {client_id}: no channel yet - guided + parked for detection")
            return RedirectResponse(url="/dashboard/?connected=youtube_no_channel")
        upsert_account(client_id, "youtube", channel["channel_id"], refresh_token, "active")
        # What Google actually GRANTED, as evidence for scope verification
        # (must equal youtube_service.YOUTUBE_SCOPES — never the full youtube scope)
        granted = (tokens.get("scope") or "").split()
        print(f"[youtube oauth] client {client_id}: granted scopes {granted}")
        log_activity(client_id, "youtube_content_agent", "account_connected",
                     {"channel_id": channel["channel_id"], "title": channel.get("title", ""),
                      "scopes": granted}, {})
        return RedirectResponse(url="/dashboard/?connected=youtube")
    except Exception:
        import traceback
        traceback.print_exc()
        return RedirectResponse(url="/dashboard/?connect_error=youtube")

# ─── Google verification ROUND 2 (docs/GOOGLE-ROUND2.md) ─────────────────────
# Both flows are OFF unless their flag is set (core/round2_flags). Off = every
# route below answers 404 and no consent URL is ever built, so round 1's scope
# submission is untouched.

def _round2_or_404(enabled: bool):
    if not enabled:
        raise HTTPException(status_code=404, detail="Not found")

@app.get("/api/oauth/gmail/start")
def gmail_oauth_start(request: Request):
    from core import gmail_agent_plan, gmail_service
    from core.round2_flags import gmail_send_enabled
    _round2_or_404(gmail_send_enabled())
    client_id = _require_session(request)
    if not gmail_agent_plan.entitled(client_id):
        return RedirectResponse(url="/dashboard/?connect_error=gmail_not_entitled")
    return RedirectResponse(url=gmail_service.build_consent_url(create_oauth_state_token(client_id)))

@app.get("/api/oauth/gmail/callback")
def gmail_oauth_callback(state: str = "", code: str = "", error: str = ""):
    from core import gmail_service
    from core.round2_flags import gmail_send_enabled
    _round2_or_404(gmail_send_enabled())
    client_id = verify_oauth_state_token(state)
    if not client_id:
        _log_bad_oauth_state("gmail", state, error)
        return RedirectResponse(url="/dashboard/?connect_error=gmail")
    if error or not code:
        print(f"[gmail oauth] client {client_id}: consent denied or no code (error={error})")
        return RedirectResponse(url="/dashboard/?connect_error=gmail")
    try:
        tokens = gmail_service.exchange_code(code)
        refresh_token = tokens.get("refresh_token")
        granted = (tokens.get("scope") or "").split()
        if not refresh_token or gmail_service.SEND_SCOPE not in granted:
            print(f"[gmail oauth] client {client_id}: no refresh_token or gmail.send not granted ({granted})")
            if refresh_token:
                google_ads_service.revoke_token(refresh_token)
            return RedirectResponse(url="/dashboard/?connect_error=gmail")
        sender = gmail_service.sender_address(refresh_token)
        if not sender:
            google_ads_service.revoke_token(refresh_token)
            return RedirectResponse(url="/dashboard/?connect_error=gmail")
        upsert_account(client_id, "gmail_send", sender, refresh_token, "active")
        print(f"[gmail oauth] client {client_id}: granted scopes {granted}")
        log_activity(client_id, "gmail_outreach", "account_connected", {"scopes": granted}, {})
        return RedirectResponse(url="/dashboard/?connected=gmail")
    except Exception:
        import traceback
        traceback.print_exc()
        return RedirectResponse(url="/dashboard/?connect_error=gmail")

@app.get("/api/oauth/search-console/start")
def search_console_oauth_start(request: Request):
    from core import search_console_service
    from core.round2_flags import search_console_enabled
    _round2_or_404(search_console_enabled())
    client_id = _require_session(request)
    return RedirectResponse(url=search_console_service.build_consent_url(create_oauth_state_token(client_id)))

def _client_site_domain(client_id: int) -> str:
    from urllib.parse import urlparse
    site = next((a.get("account_id") for a in get_accounts(client_id)
                 if a.get("platform") == "wordpress" and a.get("status") == "active"), "") or ""
    return urlparse(site).netloc.lower() if site.startswith(("http://", "https://")) else ""

@app.get("/api/oauth/search-console/callback")
def search_console_oauth_callback(state: str = "", code: str = "", error: str = ""):
    from core import search_console_service as sc
    from core.round2_flags import search_console_enabled
    _round2_or_404(search_console_enabled())
    client_id = verify_oauth_state_token(state)
    if not client_id:
        _log_bad_oauth_state("search_console", state, error)
        return RedirectResponse(url="/dashboard/?connect_error=search_console")
    if error or not code:
        print(f"[search_console oauth] client {client_id}: consent denied or no code (error={error})")
        return RedirectResponse(url="/dashboard/?connect_error=search_console")
    try:
        tokens = sc.exchange_code(code)
        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            return RedirectResponse(url="/dashboard/?connect_error=search_console")
        sites = sc.list_verified_sites(refresh_token)
        if not sites:
            # Nothing we could read — keep no grant we can't use
            google_ads_service.revoke_token(refresh_token)
            return RedirectResponse(url="/dashboard/?connect_error=search_console_no_sites")
        domain = _client_site_domain(client_id)
        chosen = next((s["site_url"] for s in sites if sc.site_matches_domain(s["site_url"], domain)),
                      sites[0]["site_url"])
        upsert_account(client_id, "search_console", chosen, refresh_token, "active")
        granted = (tokens.get("scope") or "").split()
        print(f"[search_console oauth] client {client_id}: granted scopes {granted}")
        log_activity(client_id, "rank_tracking", "account_connected",
                     {"scopes": granted, "sites": len(sites)}, {})
        return RedirectResponse(url="/dashboard/?connected=search_console")
    except Exception:
        import traceback
        traceback.print_exc()
        return RedirectResponse(url="/dashboard/?connect_error=search_console")

def _search_console_row(client_id: int) -> dict:
    return next((a for a in get_accounts(client_id)
                 if a.get("platform") == "search_console" and a.get("status") == "active"), {})

@app.get("/api/client/search-console/sites")
def client_search_console_sites(request: Request):
    # The client's verified Search Console properties, and which one we read
    from core import search_console_service as sc
    from core.round2_flags import search_console_enabled
    from core.secrets_vault import get_client_secret
    _round2_or_404(search_console_enabled())
    client_id = _require_session(request)
    row = _search_console_row(client_id)
    if not row:
        return {"success": True, "data": {"connected": False, "sites": [], "selected": ""}}
    try:
        sites = sc.list_verified_sites(get_client_secret(client_id, "search_console"))
    except Exception as e:
        print(f"[search_console] client {client_id}: sites.list failed: {type(e).__name__}")
        raise HTTPException(status_code=502, detail={"code": "ERR_SEARCH_CONSOLE"})
    return {"success": True, "data": {"connected": True, "selected": row.get("account_id", ""),
                                      "sites": [s["site_url"] for s in sites]}}

class SearchConsoleSiteRequest(BaseModel):
    site_url: str

@app.post("/api/client/search-console/site")
def client_search_console_choose(req: SearchConsoleSiteRequest, request: Request):
    from core import rank_tracking
    from core import search_console_service as sc
    from core.round2_flags import search_console_enabled
    from core.secrets_vault import get_client_secret
    _round2_or_404(search_console_enabled())
    client_id = _require_session(request)
    if not _search_console_row(client_id):
        raise HTTPException(status_code=404, detail={"code": "ERR_NOT_CONNECTED"})
    refresh_token = get_client_secret(client_id, "search_console")
    # Only a property this grant really can read — never a typed-in URL
    if req.site_url not in [s["site_url"] for s in sc.list_verified_sites(refresh_token)]:
        raise HTTPException(status_code=400, detail={"code": "ERR_UNKNOWN_SITE"})
    # One row per (client, platform): this replaces the property we read
    upsert_account(client_id, "search_console", req.site_url, refresh_token, "active")
    for provider in rank_tracking.PROVIDERS:
        if provider.id == "search_console":
            provider.forget(client_id)
    return {"success": True}

# ─── Google Analytics (round 2, ANALYTICS_ENABLED, off) ──────────────────────
# Read-only (analytics.readonly). One GA4 property per client, chosen from the
# properties THEIR login can read and stored in the row's account_id like the
# GTM container choice. Several properties → status 'pending' until chosen.

@app.get("/api/oauth/analytics/start")
def analytics_oauth_start(request: Request):
    from core import analytics_service
    from core.round2_flags import analytics_enabled
    _round2_or_404(analytics_enabled())
    client_id = _require_session(request)
    return RedirectResponse(url=analytics_service.build_consent_url(create_oauth_state_token(client_id)))

@app.get("/api/oauth/analytics/callback")
def analytics_oauth_callback(state: str = "", code: str = "", error: str = ""):
    from core import analytics_service as ga
    from core import site_traffic
    from core.round2_flags import analytics_enabled
    _round2_or_404(analytics_enabled())
    client_id = verify_oauth_state_token(state)
    if not client_id:
        _log_bad_oauth_state("analytics", state, error)
        return RedirectResponse(url="/dashboard/?connect_error=analytics")
    if error or not code:
        print(f"[analytics oauth] client {client_id}: consent denied or no code (error={error})")
        return RedirectResponse(url="/dashboard/?connect_error=analytics")
    try:
        tokens = ga.exchange_code(code)
        refresh_token = tokens.get("refresh_token")
        granted = (tokens.get("scope") or "").split()
        if not refresh_token or ga.ANALYTICS_SCOPES[0] not in granted:
            print(f"[analytics oauth] client {client_id}: no refresh_token or scope not granted ({granted})")
            if refresh_token:
                google_ads_service.revoke_token(refresh_token)
            return RedirectResponse(url="/dashboard/?connect_error=analytics")
        properties = ga.list_properties(refresh_token)
        if not properties:
            # Nothing we could read — keep no grant we can't use
            google_ads_service.revoke_token(refresh_token)
            return RedirectResponse(url="/dashboard/?connect_error=analytics_no_properties")
        if len(properties) == 1:
            upsert_account(client_id, "google_analytics", properties[0]["property"], refresh_token, "active")
        else:
            # The client picks which one is their site (the dashboard card lists them)
            upsert_account(client_id, "google_analytics", "", refresh_token, "pending")
        site_traffic.forget(client_id)
        print(f"[analytics oauth] client {client_id}: granted scopes {granted}")
        log_activity(client_id, "site_traffic", "account_connected",
                     {"scopes": granted, "properties": len(properties)}, {})
        return RedirectResponse(url="/dashboard/?connected=analytics")
    except Exception:
        import traceback
        traceback.print_exc()
        return RedirectResponse(url="/dashboard/?connect_error=analytics")

def _analytics_row(client_id: int) -> dict:
    return next((a for a in get_accounts(client_id) if a.get("platform") == "google_analytics"), {})

@app.get("/api/client/analytics/properties")
def client_analytics_properties(request: Request):
    # The GA4 properties the client's own login can read, and which one we read
    from core import analytics_service as ga
    from core.round2_flags import analytics_enabled
    from core.secrets_vault import get_client_secret
    _round2_or_404(analytics_enabled())
    client_id = _require_session(request)
    row = _analytics_row(client_id)
    if not row:
        return {"success": True, "data": {"connected": False, "properties": [], "selected": ""}}
    try:
        properties = ga.list_properties(get_client_secret(client_id, "google_analytics", any_status=True))
    except Exception as e:
        print(f"[analytics] client {client_id}: accountSummaries failed: {type(e).__name__}")
        raise HTTPException(status_code=502, detail={"code": "ERR_ANALYTICS"})
    return {"success": True, "data": {"connected": True,
                                      "selected": row.get("account_id", "") if row.get("status") == "active" else "",
                                      "properties": properties}}

class AnalyticsPropertyRequest(BaseModel):
    property: str

@app.post("/api/client/analytics/property")
def client_analytics_choose(req: AnalyticsPropertyRequest, request: Request):
    from core import analytics_service as ga
    from core import site_traffic
    from core.round2_flags import analytics_enabled
    from core.secrets_vault import get_client_secret
    _round2_or_404(analytics_enabled())
    client_id = _require_session(request)
    if not _analytics_row(client_id):
        raise HTTPException(status_code=404, detail={"code": "ERR_NOT_CONNECTED"})
    refresh_token = get_client_secret(client_id, "google_analytics", any_status=True)
    # Only a property this grant really can read — never a typed-in id
    if req.property not in [p["property"] for p in ga.list_properties(refresh_token)]:
        raise HTTPException(status_code=400, detail={"code": "ERR_UNKNOWN_PROPERTY"})
    upsert_account(client_id, "google_analytics", req.property, refresh_token, "active")
    site_traffic.forget(client_id)
    return {"success": True}

# ─── The client's OWN Google Drive (round 2, DRIVE_FILE_ENABLED, off) ────────
# drive.file only: the app manages the files it creates and nothing else in
# their Drive. Where files go is core/drive_storage.location — never decided
# here.

@app.get("/api/oauth/google-drive/start")
def google_drive_oauth_start(request: Request):
    from core import drive_file_service
    from core.round2_flags import drive_file_enabled
    _round2_or_404(drive_file_enabled())
    client_id = _require_session(request)
    return RedirectResponse(url=drive_file_service.build_consent_url(create_oauth_state_token(client_id)))

@app.get("/api/oauth/google-drive/callback")
def google_drive_oauth_callback(state: str = "", code: str = "", error: str = ""):
    from core import drive_file_service, drive_migration, drive_storage
    from core.round2_flags import drive_file_enabled
    _round2_or_404(drive_file_enabled())
    client_id = verify_oauth_state_token(state)
    if not client_id:
        _log_bad_oauth_state("google_drive", state, error)
        return RedirectResponse(url="/dashboard/?connect_error=google_drive")
    if error or not code:
        print(f"[google_drive oauth] client {client_id}: consent denied or no code (error={error})")
        return RedirectResponse(url="/dashboard/?connect_error=google_drive")
    try:
        tokens = drive_file_service.exchange_code(code)
        refresh_token = tokens.get("refresh_token")
        granted = (tokens.get("scope") or "").split()
        if not refresh_token or drive_file_service.DRIVE_FILE_SCOPES[0] not in granted:
            print(f"[google_drive oauth] client {client_id}: no refresh_token or drive.file not granted ({granted})")
            if refresh_token:
                google_ads_service.revoke_token(refresh_token)
            return RedirectResponse(url="/dashboard/?connect_error=google_drive")
        client = get_client(client_id) or {}
        # Stored first (status active, account_id filled in below) so the
        # folder is created with THIS grant through drive_storage.as_client
        upsert_account(client_id, "google_drive_file", "pending_folder", refresh_token, "active")
        with drive_storage.as_client(client_id):
            folders = drive_file_service.create_folders(client.get("business_name") or client.get("name") or "")
        upsert_account(client_id, "google_drive_file", folders["root"], refresh_token, "active")
        # Nothing of theirs in our Shared Drive → their Drive is the home at
        # once. Otherwise files keep going to our Shared Drive until the team
        # has copied AND verified (core/drive_migration) and flips the pointer.
        try:
            needs_copy = drive_migration.has_files(client_id)
        except Exception as e:
            print(f"[google_drive oauth] client {client_id}: Shared Drive check failed: {type(e).__name__}")
            needs_copy = True    # unknown → never flip blind
        previous = drive_storage.record(client_id)
        drive_storage.save_record(client_id, {
            **previous, "root": folders["root"], "parent": folders["parent"], "link": folders["link"],
            "folders": folders["folders"], "connected_at": datetime.now(timezone.utc).isoformat(),
            "pointer": drive_storage.SHARED if needs_copy else drive_storage.CLIENT})
        log_activity(client_id, "drive_storage", "account_connected",
                     {"scopes": granted, "needs_migration": needs_copy}, {})
        if needs_copy:
            from core.chat_fallback import alert_once
            alert_once("drive_storage", client_id, "migration_needed",
                       f"client {client_id}: connected their own Google Drive — their existing files are "
                       f"still in our Shared Drive. Admin drawer → Drive migration: copy, verify, flip.", 7)
        return RedirectResponse(url="/dashboard/?connected=google_drive"
                                    + ("_pending" if needs_copy else ""))
    except Exception:
        import traceback
        traceback.print_exc()
        return RedirectResponse(url="/dashboard/?connect_error=google_drive")

@app.get("/api/client/google-drive/status")
def client_google_drive_status(request: Request):
    # Where the client's files live right now (the card's line)
    from core import drive_storage
    from core.round2_flags import drive_file_enabled
    _round2_or_404(drive_file_enabled())
    client_id = _require_session(request)
    where = drive_storage.location(client_id)
    rec = drive_storage.record(client_id)
    return {"success": True, "data": {"connected": bool(rec.get("root")), "where": where["kind"],
                                      "link": rec.get("link", "") if rec.get("root") else ""}}

class DriveMigrationRequest(BaseModel):
    step: str              # plan | copy | verify | flip | free
    confirm: str = ""

@app.post("/api/admin/clients/{client_id}/drive-migration")
def admin_drive_migration(client_id: int, req: DriveMigrationRequest, request: Request):
    # Plain `def`: Drive HTTP calls. One step per request, each re-checked here.
    from core import drive_migration
    from core.round2_flags import drive_file_enabled
    _round2_or_404(drive_file_enabled())
    _require_admin(request)
    steps = {"plan": lambda: drive_migration.plan(client_id),
             "copy": lambda: drive_migration.copy(client_id),
             "verify": lambda: drive_migration.verify(client_id),
             "flip": lambda: drive_migration.flip(client_id),
             "free": lambda: drive_migration.free_shared(client_id, req.confirm),
             "status": lambda: drive_migration.status(client_id)}
    if req.step not in steps:
        raise HTTPException(status_code=400, detail={"code": "ERR_UNKNOWN_STEP"})
    try:
        result = steps[req.step]()
    except drive_migration.MigrationRefused as e:
        raise HTTPException(status_code=409, detail={"code": e.code, "detail": e.detail})
    if req.step in ("flip", "free"):
        log_activity(client_id, "drive_storage", f"drive_migration_{req.step}", {"by": "admin"}, result)
    return {"success": True, "data": result}

def _drive_usage_scan() -> dict:
    # Our Shared Drive storage per client, with an admin alert on each status
    # change across DRIVE_SHARED_ALERT_GB. Works whatever the round-2 flag
    # says: until drive.file is approved, OUR storage is what is being billed.
    from core import drive_storage
    ids = [c["id"] for c in list_clients() if c.get("id")]
    rows = drive_storage.shared_usage_scan(ids)
    return {"success": True, "data": {"threshold_bytes": drive_storage.shared_alert_bytes(), "clients": rows}}

@app.post("/api/admin/drive/usage-scan")
def admin_drive_usage_scan(request: Request):
    # The dashboard button (admin_session cookie)
    _require_admin(request)
    return _drive_usage_scan()

@app.get("/api/drive/usage-scan", dependencies=_admin_only)
def drive_usage_scan():
    # Daily Cloud Scheduler job (X-Admin-Key), same pattern as
    # /api/monitor/scan and /api/leads/volume-scan. Plain `def`: it walks
    # every client's folder in Drive.
    try:
        return _drive_usage_scan()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─── Merchant Center: RETIRED 2026-10-21 ─────────────────────────────────────
# It never had a feature a client could see (no feed-status screen, the scan
# was never scheduled), so the `content` scope it requested could not pass
# Google's least-privilege review. The connect flow and the scope are gone.
# Grants made before that still sit in client_accounts: this one-time admin
# call revokes each at Google and deletes the rows (the same
# _disconnect_platform a client's own Disconnect uses). Safe to re-run.

@app.post("/api/admin/merchant-center/retire", dependencies=_admin_only)
def merchant_center_retire():
    # Plain `def`: one blocking revoke call per connected client
    rows = (db.table("client_accounts").select("client_id")
            .in_("platform", _DISCONNECT_GROUPS["merchant_center"]).execute().data or [])
    results = {}
    for client_id in sorted({r["client_id"] for r in rows if r.get("client_id")}):
        try:
            outcome = _disconnect_platform(client_id, "merchant_center")
            log_activity(client_id, "client_agent", "account_disconnected",
                         {"platform": "merchant_center", "revoked": outcome["revoked"],
                          "reason": "merchant_center_retired"}, {})
            results[client_id] = {"removed": outcome["removed"], "revoked": outcome["revoked"]}
        except Exception as e:
            results[client_id] = {"error": type(e).__name__}
    return {"success": True, "data": {"clients": results, "count": len(results)}}

# ─── Google Ads execution (admin/scheduler only) ─────────────────────────────

class CreateCampaignRequest(BaseModel):
    client_id: int
    name: str
    daily_budget_ils: float
    final_url: str
    keywords: list
    headlines: list
    descriptions: list
    locations: list = []
    languages: list = []

@app.post("/api/google-ads/create-campaign", dependencies=_admin_only)
def google_ads_create_campaign(req: CreateCampaignRequest):
    from agents.google_ads_agent import create_search_campaign
    spec = req.model_dump(exclude={"client_id"})
    result = create_search_campaign(req.client_id, spec)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

class DraftCampaignRequest(BaseModel):
    # Shared by both ads agents' draft endpoints (Google below, Meta further
    # down) — the drafting inputs are identical; only the produced spec differs.
    client_id: int
    daily_budget_ils: float        # REQUIRED — never inferred from the proposal
    final_url: str = ""            # defaults to the client's connected site
    goal: str = ""                 # free text, e.g. "leads for weekday appointments"
    force_refresh_research: bool = False

@app.post("/api/google-ads/draft-campaign", dependencies=_admin_only)
def google_ads_draft_campaign(req: DraftCampaignRequest):
    # Researches the niche, then drafts a spec FROM that research. Creates
    # NOTHING — review the draft, then POST it to /create-campaign (which still
    # creates the campaign PAUSED). Plain `def`: web-search + LLM, both blocking.
    from agents.google_ads_agent import draft_campaign_spec
    result = draft_campaign_spec(req.client_id, req.daily_budget_ils, req.final_url,
                                 req.goal, req.force_refresh_research)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

@app.get("/api/google-ads/scan", dependencies=_admin_only)
def google_ads_scan():
    from agents.google_ads_agent import run_health_scan
    try:
        return {"success": True, "data": run_health_scan()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/google-ads/weekly-report", dependencies=_admin_only)
def google_ads_weekly_report():
    from agents.google_ads_agent import run_weekly_report
    try:
        return {"success": True, "data": run_weekly_report()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─── Meta OAuth (client connects their own ad account + Facebook Page) ───────

@app.get("/api/oauth/meta/start")
async def meta_oauth_start(request: Request):
    client_id = _require_session(request)
    state = create_oauth_state_token(client_id)
    return RedirectResponse(url=meta_service.build_consent_url(state))

@app.get("/api/oauth/meta/callback")
def meta_oauth_callback(state: str = "", code: str = "", error: str = ""):
    # Identity comes from the signed state token, not the session cookie - the
    # browser arrives here from Meta's domain and the state also blocks CSRF
    client_id = verify_oauth_state_token(state)
    if not client_id:
        _log_bad_oauth_state("meta", state, error)
        return RedirectResponse(url="/dashboard/?connect_error=meta")
    if error or not code:
        print(f"[meta oauth] client {client_id}: consent denied or no code (error={error})")
        return RedirectResponse(url="/dashboard/?connect_error=meta")

    try:
        short_token = meta_service.exchange_code(code)["access_token"]
        # Never store the short-lived token - swap it for the ~60-day one now
        user_token = meta_service.exchange_long_lived(short_token)["access_token"]

        ad_accounts = meta_service.get_ad_accounts(user_token)
        pages = meta_service.get_pages(user_token)

        # One consent connects every asset the user has: ad account for the ads
        # agent, Page (+ linked Instagram) for the content agent. MVP: first of
        # each - multi-asset clients need a picker step (same known limitation
        # as Google Ads).
        connected = {}
        if ad_accounts:
            account = ad_accounts[0]
            if len(ad_accounts) > 1:
                print(f"[meta oauth] client {client_id}: {len(ad_accounts)} ad accounts, using {account['id']}")
            upsert_account(client_id, "meta_ads", account["id"], user_token, "active")
            connected["ad_account"] = account["id"]
        if pages:
            page = pages[0]
            if len(pages) > 1:
                print(f"[meta oauth] client {client_id}: {len(pages)} Pages, using {page['id']}")
            page_token = page.get("access_token") or user_token
            upsert_account(client_id, "meta_page", page["id"], page_token, "active")
            connected["page"] = page["id"]
            instagram_id = (page.get("instagram_business_account") or {}).get("id")
            if instagram_id:
                upsert_account(client_id, "meta_instagram", instagram_id, page_token, "active")
                connected["instagram"] = instagram_id
        else:
            # No Page (with or without an ad account) is a GUIDED state now,
            # not an error: park the user token so the detection scan can
            # watch me/accounts (no-assets case - the ads row already holds it
            # otherwise), send the create-your-Page guide, and tell the
            # dashboard to explain what happens next. The old
            # connect_error=no_meta_assets dead-end is gone on purpose.
            from agents.meta_content_agent import send_page_creation_guide
            if not ad_accounts:
                upsert_account(client_id, "meta_pending", "pending_page", user_token, "active")
                connected["pending_page_token"] = True
            send_page_creation_guide(client_id)
            log_activity(client_id, "meta_ads_agent", "account_connected", connected, {})
            return RedirectResponse(url="/dashboard/?connected=meta_no_page")

        log_activity(client_id, "meta_ads_agent", "account_connected", connected, {})
        return RedirectResponse(url="/dashboard/?connected=meta")
    except Exception as e:
        import traceback
        traceback.print_exc()
        return RedirectResponse(url="/dashboard/?connect_error=meta")

# ─── TikTok OAuth (client connects their own TikTok account) ────────────────

@app.get("/api/oauth/tiktok/start")
async def tiktok_oauth_start(request: Request):
    client_id = _require_session(request)
    state = create_oauth_state_token(client_id)
    return RedirectResponse(url=tiktok_service.build_consent_url(state))

@app.get("/api/oauth/tiktok/callback")
def tiktok_oauth_callback(state: str = "", code: str = "", error: str = ""):
    # Identity comes from the signed state token, not the session cookie - the
    # browser arrives here from TikTok's domain (same CSRF protection as Meta's)
    client_id = verify_oauth_state_token(state)
    if not client_id:
        _log_bad_oauth_state("tiktok", state, error)
        return RedirectResponse(url="/dashboard/?connect_error=tiktok")
    if error or not code:
        print(f"[tiktok oauth] client {client_id}: consent denied or no code (error={error})")
        return RedirectResponse(url="/dashboard/?connect_error=tiktok")

    try:
        tokens = tiktok_service.exchange_code(code)
        access_token, refresh_token = tokens["access_token"], tokens["refresh_token"]
        # user/info is DISPLAY data (name, avatar): a failure there must not
        # stop the connection — the account id comes from the token response
        user, info_log_id = {}, ""
        try:
            user, info_log_id = tiktok_service.get_user_info_with_log(access_token)
        except Exception as e:
            info_log_id = getattr(e, "log_id", "") or ""
            print(f"[tiktok oauth] client {client_id}: user/info failed (display data only): "
                  f"{type(e).__name__} log_id={info_log_id or '-'}")
        open_id, note = tiktok_service.account_open_id(tokens, user)
        if note:
            print(f"[tiktok oauth] client {client_id}: {note}")
        if not open_id:
            log_ids = f"token log_id={tokens.get('log_id') or '-'}, user/info log_id={info_log_id or '-'}"
            print(f"[tiktok oauth] client {client_id}: no open_id in the token response or user/info ({log_ids})")
            from core.chat_fallback import alert_once
            alert_once("tiktok_oauth", client_id, "tiktok_oauth:no_open_id",
                       f"client {client_id}: TikTok connect returned no open_id (account id) in the token "
                       f"response or user/info — nothing stored. {log_ids}. Give TikTok support the log_id.", 1)
            return RedirectResponse(url="/dashboard/?connect_error=tiktok_no_account")

        # The user can untick a scope on TikTok's consent screen. Stored
        # anyway (identity works); core/tiktok_grant records what THIS grant
        # covers, replacing any record of the previous grant, so a reconnect
        # clears "needs reconnect" at once. Scope names only, never a token.
        missing = tiktok_service.missing_scopes(tokens.get("scope", ""))
        if missing:
            print(f"[tiktok oauth] client {client_id}: consent did not grant {missing}")
        stored_token = tiktok_service.join_tokens(access_token, refresh_token)
        upsert_account(client_id, "tiktok", open_id, stored_token, "active")
        from core import performance, tiktok_grant
        tiktok_grant.clear(client_id)
        tiktok_grant.record_scopes(client_id, tokens.get("scope", ""))
        performance.forget_engagement(client_id, "tiktok")
        from core import client_state
        client_state.forget(client_id)
        log_activity(client_id, "tiktok_content_agent", "account_connected",
                     {"open_id": open_id, "display_name": user.get("display_name", "")}, {})
        return RedirectResponse(url="/dashboard/?connected=tiktok")
    except Exception as e:
        import traceback
        traceback.print_exc()
        return RedirectResponse(url="/dashboard/?connect_error=tiktok")

# ─── Meta execution (admin/scheduler only) ───────────────────────────────────

class MetaCreateCampaignRequest(BaseModel):
    client_id: int
    name: str
    daily_budget_ils: float
    final_url: str
    primary_text: str
    headline: str
    description: str = ""
    image_url: str = ""
    countries: list = []

@app.post("/api/meta-ads/create-campaign", dependencies=_admin_only)
def meta_ads_create_campaign(req: MetaCreateCampaignRequest):
    from agents.meta_ads_agent import create_link_campaign
    spec = req.model_dump(exclude={"client_id"})
    result = create_link_campaign(req.client_id, spec)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

@app.post("/api/meta-ads/draft-campaign", dependencies=_admin_only)
def meta_ads_draft_campaign(req: DraftCampaignRequest):
    # Researches the niche, then drafts a spec FROM that research. Creates
    # NOTHING — the returned spec goes to /api/meta-ads/create-campaign after a
    # human reviews it, and that call still creates the campaign PAUSED.
    # Plain `def`: a web-search call plus an LLM call, both blocking.
    from agents.meta_ads_agent import draft_campaign_spec
    result = draft_campaign_spec(req.client_id, req.daily_budget_ils, req.final_url,
                                 req.goal, req.force_refresh_research)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

@app.get("/api/meta-ads/scan", dependencies=_admin_only)
def meta_ads_scan():
    from agents.meta_ads_agent import run_health_scan
    try:
        return {"success": True, "data": run_health_scan()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/meta-ads/weekly-report", dependencies=_admin_only)
def meta_ads_weekly_report():
    from agents.meta_ads_agent import run_weekly_report
    try:
        return {"success": True, "data": run_weekly_report()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class MetaPublishRequest(BaseModel):
    client_id: int
    target: str            # facebook | instagram
    kind: str              # facebook: text|link|photo|video, instagram: photo|reel|story
    message: str = ""      # post text / caption
    media_url: str = ""    # PUBLIC http(s) URL - Meta fetches it server-side
    link: str = ""         # for facebook 'link' kind

@app.post("/api/meta-content/publish", dependencies=_admin_only)
def meta_content_publish(req: MetaPublishRequest):
    from agents.meta_content_agent import publish
    spec = req.model_dump(exclude={"client_id"})
    result = publish(req.client_id, spec)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

class MetaReplyRequest(BaseModel):
    client_id: int
    comment_id: str
    message: str
    target: str = "facebook"

@app.post("/api/meta-content/reply", dependencies=_admin_only)
def meta_content_reply(req: MetaReplyRequest):
    from agents.meta_content_agent import reply_to_comment
    result = reply_to_comment(req.client_id, req.comment_id, req.message, req.target)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "unknown error"))
    return {"success": True, "data": result}

@app.get("/api/meta-content/inbox", dependencies=_admin_only)
def meta_content_inbox(client_id: int):
    from agents.meta_content_agent import get_inbox
    try:
        return {"success": True, "data": get_inbox(client_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/meta-content/scan", dependencies=_admin_only)
def meta_content_scan():
    # Two scans per scheduler hit: the comment/DM inbox sweep, plus detection
    # of self-created Pages for guided no-Page clients (see the meta skill's
    # No-Page flow) - piggybacked here so no extra Cloud Scheduler job exists
    from agents.meta_content_agent import run_inbox_scan, run_page_detection_scan
    try:
        return {"success": True, "data": {"inbox": run_inbox_scan(),
                                          "page_detection": run_page_detection_scan()}}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/meta-content/engagement", dependencies=_admin_only)
def meta_content_engagement(client_id: int):
    from agents.meta_content_agent import get_engagement_summary
    try:
        return {"success": True, "data": get_engagement_summary(client_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─── TikTok execution (admin/scheduler only) ─────────────────────────────────

class TikTokPublishRequest(BaseModel):
    client_id: int
    drive_file_id: str
    caption: str = ""

@app.post("/api/tiktok-content/publish", dependencies=_admin_only)
def tiktok_content_publish(req: TikTokPublishRequest):
    # Plain `def`: downloads from Drive + uploads to TikTok are blocking HTTP
    from agents.tiktok_content_agent import publish
    result = publish(req.client_id, {"drive_file_id": req.drive_file_id, "caption": req.caption})
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

@app.get("/api/tiktok-content/engagement", dependencies=_admin_only)
def tiktok_content_engagement(client_id: int):
    from agents.tiktok_content_agent import get_engagement_summary
    try:
        return {"success": True, "data": get_engagement_summary(client_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─── YouTube execution (admin/scheduler only) ────────────────────────────────

class YoutubePublishRequest(BaseModel):
    client_id: int
    drive_file_id: str
    title: str
    description: str = ""

@app.post("/api/youtube-content/publish", dependencies=_admin_only)
def youtube_content_publish(req: YoutubePublishRequest):
    # Plain `def`: downloads from Drive + uploads to YouTube are blocking HTTP
    from agents.youtube_content_agent import publish
    result = publish(req.client_id, {"drive_file_id": req.drive_file_id,
                                     "title": req.title, "description": req.description})
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

@app.get("/api/youtube-content/engagement", dependencies=_admin_only)
def youtube_content_engagement(client_id: int):
    from agents.youtube_content_agent import get_engagement_summary
    try:
        return {"success": True, "data": get_engagement_summary(client_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/youtube-content/scan", dependencies=_admin_only)
def youtube_content_scan():
    # Channel-creation detection for guided no-channel clients (see the
    # youtube skill's "No-channel flow") - no existing YouTube scan to
    # piggyback on, so this is its own scheduled job
    from agents.youtube_content_agent import run_channel_detection_scan
    try:
        return {"success": True, "data": run_channel_detection_scan()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─── Website (client connects their WordPress site — no OAuth, App Password) ─

class WebsiteConnectRequest(BaseModel):
    site_url: str
    username: str
    app_password: str

@app.post("/api/website/connect")
def website_connect(req: WebsiteConnectRequest, request: Request):
    # Session-gated like the OAuth starts; plain `def` — validation makes
    # blocking httpx calls to the client's site
    client_id = _require_session(request)
    from agents.website_agent import connect_site
    result = connect_site(client_id, req.site_url, req.username, req.app_password)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "connection failed"))
    return {"success": True, "data": result}

# One-click connect via WordPress core's "Authorize Application" screen - the
# DEFAULT path; /api/website/connect above stays as the manual fallback. See
# website_agent's "One-click connect" section (credential rotation) and
# wordpress_service.discover_authorization (when the screen can't work).
WP_CONNECT_NONCE_COOKIE = "wp_connect_nonce"
WP_CONNECT_COOKIE_PATH = "/api/website/authorize"

class WebsiteAuthorizeStartRequest(BaseModel):
    site_url: str

@app.post("/api/website/authorize/start")
def website_authorize_start(req: WebsiteAuthorizeStartRequest, request: Request):
    # JSON, not a redirect: the site must be probed first, and a failure needs
    # to come back as a localized code on the card, not a dead page. Plain
    # `def` - the probe is a blocking request to the client's site.
    client_id = _require_session(request)
    from urllib.parse import quote
    from agents.website_agent import start_authorize_connect
    from core.wordpress_service import normalize_site_url
    if not (req.site_url or "").strip():
        raise HTTPException(status_code=400, detail={"code": "ERR_SITE_URL_REQUIRED"})
    try:
        typed = normalize_site_url(req.site_url)
    except ValueError:
        raise HTTPException(status_code=400, detail={"code": "ERR_SITE_URL_REQUIRED"})
    base = os.environ.get("PUBLIC_APP_URL", "https://app.uallak.com").rstrip("/")
    nonce = secrets.token_urlsafe(24)

    def return_urls(resolved_site: str):
        # Signed against the site as RESOLVED by the probe (www / https
        # redirects) - the exact URL the callback will store
        state = quote(create_wp_connect_state(client_id, resolved_site, nonce), safe="")
        return (f"{base}/api/website/authorize/callback?state={state}",
                f"{base}/api/website/authorize/rejected?state={state}")

    result = start_authorize_connect(client_id, typed, return_urls)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail={"code": result.get("code", "ERR_WP_AUTHORIZE_FAILED")})
    response = JSONResponse({"success": True, "data": {
        "authorize_url": result["authorize_url"], "discovered": result["discovered"]}})
    # Lax: the callback is a top-level navigation back FROM the client's site
    response.set_cookie(WP_CONNECT_NONCE_COOKIE, nonce, max_age=30 * 60, httponly=True,
                        secure=True, samesite="lax", path=WP_CONNECT_COOKIE_PATH)
    return response

def _wp_connect_state(request: Request, state: str):
    # Session AND signed state AND nonce cookie must all agree - a link crafted
    # elsewhere (carrying someone else's site + password) fails here
    try:
        session_client = _require_session(request)
    except HTTPException:
        return None
    pinned = verify_wp_connect_state(state, request.cookies.get(WP_CONNECT_NONCE_COOKIE, ""))
    if not pinned or pinned[0] != session_client:
        return None
    return pinned

def _wp_connect_redirect(url: str):
    response = RedirectResponse(url=url, status_code=303)
    response.delete_cookie(WP_CONNECT_NONCE_COOKIE, path=WP_CONNECT_COOKIE_PATH)
    # The request that got us here carried the password in its URL - nothing
    # downstream gets it as a Referer
    response.headers["Referrer-Policy"] = "no-referrer"
    return response

@app.get("/api/website/authorize/callback")
def website_authorize_callback(request: Request, state: str = "", user_login: str = "",
                               password: str = "", success: str = ""):
    # WordPress appends site_url/user_login/password on approval. site_url is
    # deliberately IGNORED - the site comes from our signed state. Plain `def`:
    # rotation + validation are blocking calls to the client's site.
    pinned = _wp_connect_state(request, state)
    if not pinned:
        return _wp_connect_redirect("/dashboard/?connect_error=wordpress_expired")
    client_id, site_url = pinned
    from agents.website_agent import complete_authorize_connect, record_authorize_rejected
    if success == "false" or not password:
        # Older/odd WP builds can bounce a denial back to success_url
        record_authorize_rejected(client_id, site_url)
        return _wp_connect_redirect("/dashboard/?connect_error=wordpress_rejected")
    try:
        result = complete_authorize_connect(client_id, site_url, user_login, password)
    except Exception as e:
        print(f"[wordpress authorize] client {client_id}: {type(e).__name__}: {e}")
        alert("website_agent", [f"client {client_id}: WordPress one-click connect failed after "
                                f"approval - {type(e).__name__}: {e}"])
        return _wp_connect_redirect("/dashboard/?connect_error=wordpress_platform")
    if not result.get("success"):
        return _wp_connect_redirect("/dashboard/?connect_error=wordpress")
    return _wp_connect_redirect("/dashboard/?connected=wordpress")

@app.get("/api/website/authorize/rejected")
def website_authorize_rejected(request: Request, state: str = ""):
    pinned = _wp_connect_state(request, state)
    if pinned:
        from agents.website_agent import record_authorize_rejected
        record_authorize_rejected(pinned[0], pinned[1])
    return _wp_connect_redirect("/dashboard/?connect_error=wordpress_rejected")

# ─── Existing site: detect, then one of TWO paths ────────────────────────────
# Session-gated. Detection is anonymous (no credentials) so a client never has
# to know what their own site is built with — see website_agent's section note
# for why an uncertain result routes to a rebuild rather than a migration.

class ExistingSiteRequest(BaseModel):
    site_url: str

@app.post("/api/client/website/assess")
def client_website_assess(req: ExistingSiteRequest, request: Request):
    # Plain `def`: several blocking HTTP probes against the client's site.
    client_id = _require_session(request)
    from agents.website_agent import assess_existing_site
    if not (req.site_url or "").strip():
        raise HTTPException(status_code=400, detail={"code": "ERR_SITE_URL_REQUIRED"})
    return {"success": True, "data": assess_existing_site(client_id, req.site_url)}

@app.post("/api/client/website/migrate-request")
def client_website_migrate_request(request: Request):
    # Records the request and alerts the team with the runbook. Does NOT run a
    # migration — InstaWP's own flow needs an interactive human authorization
    # our backend cannot perform (see the website skill).
    client_id = _require_session(request)
    from agents.website_agent import request_migration
    result = request_migration(client_id)
    if not result.get("success"):
        code = result.get("code")
        raise HTTPException(status_code=400,
                            detail={"code": code} if code else result.get("errors", ["failed"]))
    return {"success": True, "data": result}

@app.post("/api/client/website/rebuild")
def client_website_rebuild(request: Request, background_tasks: BackgroundTasks):
    # Non-WordPress path. Captures the old site as REFERENCE, then goes through
    # the SAME request_self_provision gate as the "I have no site" button —
    # entitlement check, duplicate guard and billable-trigger audit included.
    client_id = _require_session(request)
    from agents.website_agent import start_rebuild_from_existing
    result = start_rebuild_from_existing(client_id, background_tasks)
    if not result.get("success"):
        code = result.get("code")
        detail = {"code": code} if code else result.get("errors", ["unknown error"])
        raise HTTPException(status_code=400, detail=detail)
    return {"success": True, "data": result}

@app.post("/api/website/self-provision")
def website_self_provision(request: Request, background_tasks: BackgroundTasks):
    # The client's "הקימו לי אתר" alternative to /connect when they have no
    # site at all. Deliberately no request body — see
    # website_agent.request_self_provision's docstring for why no params are
    # needed or wanted from the client here. Returns immediately; the actual
    # provisioning runs in background_tasks (real minutes) and reports back
    # via dashboard chat + website_provision_status in /api/dashboard.
    client_id = _require_session(request)
    from agents.website_agent import request_self_provision
    result = request_self_provision(client_id, background_tasks)
    if not result.get("success"):
        # {"code": "ERR_X"} pattern (see the i18n skill) whenever the agent
        # gave us one — e.g. ERR_WEBSITE_NOT_IN_PACKAGE, the real-money
        # entitlement gate — so the dashboard can localize it instead of
        # showing raw English straight through.
        code = result.get("code")
        detail = {"code": code} if code else result.get("errors", ["unknown error"])
        raise HTTPException(status_code=400, detail=detail)
    return {"success": True, "data": result}

# ─── Website execution (admin/scheduler only) ────────────────────────────────

class WebsitePublishRequest(BaseModel):
    client_id: int
    kind: str = "post"     # post | page
    title: str
    content: str           # HTML body - already generated, this is just the pipe
    excerpt: str = ""
    slug: str = ""
    status: str = "draft"  # draft by default - a human publishes (like PAUSED campaigns)

@app.post("/api/website/publish", dependencies=_admin_only)
def website_publish(req: WebsitePublishRequest):
    from agents.website_agent import publish_content
    result = publish_content(req.client_id, req.model_dump(exclude={"client_id"}))
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

class WebsiteUpdateRequest(BaseModel):
    client_id: int
    content_type: str = "post"  # post | page
    content_id: int
    fields: dict                # whitelisted in the agent (title/content/excerpt/slug/status/featured_media)

@app.post("/api/website/update", dependencies=_admin_only)
def website_update(req: WebsiteUpdateRequest):
    from agents.website_agent import update_content
    result = update_content(req.client_id, req.content_type, req.content_id, req.fields)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

class WebsiteAltTextRequest(BaseModel):
    client_id: int
    media_id: int
    alt_text: str

@app.post("/api/website/alt-text", dependencies=_admin_only)
def website_alt_text(req: WebsiteAltTextRequest):
    from agents.website_agent import update_alt_text
    result = update_alt_text(req.client_id, req.media_id, req.alt_text)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

class WebsiteSeoPluginRequest(BaseModel):
    client_id: int

@app.post("/api/website/install-seo-plugin", dependencies=_admin_only)
def website_install_seo_plugin(req: WebsiteSeoPluginRequest):
    from agents.website_agent import install_seo_plugin
    result = install_seo_plugin(req.client_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

class WebsiteProvisionRequest(BaseModel):
    client_id: int
    site_name: str = ""       # optional subdomain hint; InstaWP auto-names if empty
    logo_url: str = ""        # existing client logo (public URL) - drives brand palette
    industry_hint: str = ""   # NEUTRAL_PALETTES key fallback when no logo (e.g. "food", "b2b")

@app.post("/api/website/provision", dependencies=_admin_only)
def website_provision(req: WebsiteProvisionRequest):
    # Creates a BILLABLE hosted site (reserved InstaWP clone). This admin
    # endpoint is for manual overrides (custom site_name/logo/industry_hint);
    # the client's own self-service trigger is the narrower, parameter-free
    # POST /api/website/self-provision above. Plain `def`: provisioning polls
    # for minutes.
    from agents.website_agent import provision_site
    result = provision_site(req.client_id, req.site_name, req.logo_url, req.industry_hint)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

@app.get("/api/website/standards", dependencies=_admin_only)
def website_standards(client_id: int, auto_install_plugins: bool = True):
    from agents.website_agent import run_standards_check
    try:
        return {"success": True, "data": run_standards_check(client_id, auto_install_plugins)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/website/tracking", dependencies=_admin_only)
def website_tracking(client_id: int):
    # Tracking-tag audit (GA4/Pixel/GTM presence + expected-pixel discovery +
    # live conversion-event status when the GTM API consent exists) — the
    # data-quality gate for budget_agent's cross-platform comparison
    from agents.website_agent import get_tracking_status
    try:
        return {"success": True, "data": get_tracking_status(client_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/website/conversion-tracking", dependencies=_admin_only)
def website_conversion_tracking(client_id: int):
    from agents.website_agent import get_conversion_tracking_status
    try:
        return {"success": True, "data": get_conversion_tracking_status(client_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class ConfigureConversionRequest(BaseModel):
    client_id: int
    ga4_measurement_id: str = ""  # optional — falls back to the id detected on the site

@app.post("/api/website/configure-conversion", dependencies=_admin_only)
def website_configure_conversion(req: ConfigureConversionRequest):
    # Creates + PUBLISHES the form-submit → GA4 generate_lead setup in the
    # client's own GTM container. Refuses when a lead tag is already live
    # (double-counting guard). Plain `def`: multiple GTM API calls.
    from agents.website_agent import configure_lead_conversion
    result = configure_lead_conversion(req.client_id, req.ga4_measurement_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

class WebsiteTrackingInstallRequest(BaseModel):
    client_id: int
    gtm_container_id: str = ""     # GTM-first: pass this ALONE when possible
    ga4_measurement_id: str = ""   # direct-install fallback (no GTM container)
    meta_pixel_id: str = ""        # direct-install fallback

@app.post("/api/website/install-tracking", dependencies=_admin_only)
def website_install_tracking(req: WebsiteTrackingInstallRequest):
    # Admin-triggered with explicit ids — never from a standing scan (a scan
    # can't know the client's container/property/pixel ids). Result includes
    # verified_on_homepage: False means the widget saved but the tag does NOT
    # render (unfiltered_html stripping / non-rendering sidebar) - manual look.
    from agents.website_agent import install_tracking_tags
    result = install_tracking_tags(req.client_id, req.gtm_container_id,
                                   req.ga4_measurement_id, req.meta_pixel_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

class WebsiteLeadFormRequest(BaseModel):
    client_id: int
    page_id: int = 0   # optional override; 0 = find the contact page automatically

@app.post("/api/website/install-lead-form", dependencies=_admin_only)
def website_install_lead_form(req: WebsiteLeadFormRequest):
    # Manual/repair path only. Every site WE provision gets this automatically
    # at build time (provision_site → install_lead_capture_form), which is the
    # whole point — the client never touches an embed snippet. This endpoint is
    # for a site the client connected themselves, or a re-run after a fix.
    # Plain `def`: several blocking WP round trips.
    from agents.website_agent import install_lead_capture_form
    result = install_lead_capture_form(req.client_id, req.page_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

@app.get("/api/website/lead-capture-status", dependencies=_admin_only)
def website_lead_capture_status(client_id: int):
    # LIVE check — fetches the contact page anonymously and confirms it posts
    # to THIS client's capture URL. verified against the client's own token, so
    # a form left over from another client reports as a problem, not a success.
    from agents.website_agent import get_lead_capture_status
    try:
        return {"success": True, "data": get_lead_capture_status(client_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class WebsiteLandscapeRequest(BaseModel):
    client_id: int
    force_refresh: bool = False   # re-run the web search instead of using the 30-day cache

@app.post("/api/website/research-landscape", dependencies=_admin_only)
def website_research_landscape(req: WebsiteLandscapeRequest):
    # Competitor research (organic AND paid results) → a structured site build
    # brief. Runs automatically at the end of provision_site; this is the
    # re-run/repair path. Changes NOTHING on the site — v1 records the brief.
    # Plain `def`: web-search + LLM, both blocking.
    from agents.website_agent import research_site_landscape
    result = research_site_landscape(req.client_id, req.force_refresh)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

@app.get("/api/website/blueprint", dependencies=_admin_only)
def website_blueprint(client_id: int):
    # The most recent recorded build brief. Read-only and free — never triggers
    # new research (that is the POST above).
    from agents.website_agent import get_site_blueprint
    try:
        return {"success": True, "data": get_site_blueprint(client_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class WebsiteBrandRequest(BaseModel):
    client_id: int
    logo_url: str = ""
    industry_hint: str = ""

@app.post("/api/website/brand", dependencies=_admin_only)
def website_brand(req: WebsiteBrandRequest):
    # Re-runnable: when a client's logo arrives later (or a future
    # logo-generation agent produces one), call this to swap the neutral
    # palette for the real brand identity
    from agents.website_agent import apply_brand_identity
    return {"success": True,
            "data": apply_brand_identity(req.client_id, req.logo_url, req.industry_hint)}

class WebsitePopulateRequest(BaseModel):
    client_id: int
    items: list  # publish_content specs: [{kind,title,content,excerpt,slug,status}]

@app.post("/api/website/populate", dependencies=_admin_only)
def website_populate(req: WebsitePopulateRequest):
    from agents.website_agent import populate_site
    result = populate_site(req.client_id, req.items)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

@app.get("/api/website/overview", dependencies=_admin_only)
def website_overview(client_id: int):
    from agents.website_agent import get_site_overview
    try:
        return {"success": True, "data": get_site_overview(client_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/competitor-research", dependencies=_admin_only)
def competitor_research_inspect(client_id: int, lens: str, force_refresh: bool = False):
    # The raw research one lens produced, for inspecting/priming the cache
    # (media and ads agents consume this internally — this is how a human sees
    # what they were given). force_refresh=true spends a new web-search fee.
    from core import competitor_research
    try:
        return {"success": True, "data": competitor_research.research(
            client_id, lens, force_refresh=force_refresh)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/website/scan", dependencies=_admin_only)
def website_scan():
    from agents.website_agent import run_health_scan
    try:
        return {"success": True, "data": run_health_scan()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─── Organic SEO (admin/scheduler only — strategy routes to Johnny) ───────────

class SeoConnectToolRequest(BaseModel):
    client_id: int
    tool: str      # seoptimer | semrush | ahrefs (old per-client-key model; see seo_tools_service)
    api_key: str

@app.post("/api/seo/connect-tool", dependencies=_admin_only)
def seo_connect_tool(req: SeoConnectToolRequest):
    from agents.seo_agent import connect_tool
    result = connect_tool(req.client_id, req.tool, req.api_key)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

@app.get("/api/seo/audit", dependencies=_admin_only)
def seo_audit(client_id: int):
    # Free own-site audit (WP REST) — no tool units, no LLM
    from agents.seo_agent import audit_site
    try:
        return {"success": True, "data": audit_site(client_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/seo/research", dependencies=_admin_only)
def seo_research(client_id: int, force_refresh: bool = False):
    # Costs real money (client's tool units / web-search fee) — cached 7 days;
    # force_refresh burns a fresh run
    from agents.seo_agent import run_market_research
    result = run_market_research(client_id, force_refresh=force_refresh)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "research failed"))
    return {"success": True, "data": result}

class SeoStrategyRequest(BaseModel):
    client_id: int

@app.post("/api/seo/strategy", dependencies=_admin_only)
def seo_strategy(req: SeoStrategyRequest):
    from agents.seo_agent import build_strategy
    result = build_strategy(req.client_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "strategy failed"))
    return {"success": True, "data": result}

class SeoWriteArticleRequest(BaseModel):
    client_id: int
    topic: str
    target_keyword: str = ""
    notes: str = ""

@app.post("/api/seo/write-article", dependencies=_admin_only)
def seo_write_article(req: SeoWriteArticleRequest):
    # Creates a WP DRAFT via website_agent — human publishes. Weekly cap +
    # quality gate enforced inside the agent (iron rules). Manual/ad-hoc use
    # only now — approved-strategy topics write themselves automatically
    # (see approve_strategy / run_seo_cycle).
    from agents.seo_agent import write_article
    result = write_article(req.client_id, req.topic, req.target_keyword, req.notes)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

@app.post("/api/seo/approve-strategy", dependencies=_admin_only)
def seo_approve_strategy(req: SeoStrategyRequest):
    """Johnny's one remaining manual step for organic SEO — approving
    immediately writes as many queued articles as the weekly cap allows;
    run_seo_cycle advances the rest automatically each week from here."""
    from agents.seo_agent import approve_strategy
    result = approve_strategy(req.client_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

@app.get("/api/seo/promotable", dependencies=_admin_only)
def seo_promotable(client_id: int, limit: int = 5):
    # Published articles ready for social cross-promotion (future media agent
    # calls agents/seo_agent.get_recent_articles_for_promotion directly)
    from agents.seo_agent import get_recent_articles_for_promotion
    return {"success": True, "data": get_recent_articles_for_promotion(client_id, limit)}

@app.get("/api/seo/cycle", dependencies=_admin_only)
def seo_cycle():
    from agents.seo_agent import run_seo_cycle
    try:
        return {"success": True, "data": run_seo_cycle()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─── Media agent (generation + Drive org; admin/scheduler except folder link) ─

class MediaConnectAccountRequest(BaseModel):
    api_key: str  # the CLIENT'S Higgsfield Cloud API key (their plan, their card)

@app.post("/api/media/connect")
def media_connect_account(req: MediaConnectAccountRequest, request: Request):
    # Session-gated like /api/website/connect - the client pastes their OWN
    # Higgsfield API key (created at cloud.higgsfield.ai/api-keys on their own
    # paid plan), same self-service pattern as the WordPress Application
    # Password card. Plain `def`: no blocking calls here, but consistent with
    # the other connect endpoints.
    client_id = _require_session(request)
    from agents.media_agent import connect_generation_account
    result = connect_generation_account(client_id, req.api_key)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

class MediaGenerateRequest(BaseModel):
    client_id: int
    brief: str
    platform: str = "instagram"

@app.post("/api/media/generate-image", dependencies=_admin_only)
def media_generate_image(req: MediaGenerateRequest):
    # Plain `def`: prompt-craft LLM + Imagen + Drive upload, all blocking
    from agents.media_agent import generate_image
    result = generate_image(req.client_id, req.brief, req.platform)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

@app.post("/api/media/generate-video", dependencies=_admin_only)
def media_generate_video(req: MediaGenerateRequest):
    # Plain `def` and SLOW (Veo polls up to ~10 min) — the most expensive
    # call in the system; caps + cost tracking live in media_gen_service
    from agents.media_agent import generate_video
    result = generate_video(req.client_id, req.brief, req.platform)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

class FilmingKitRequest(BaseModel):
    client_id: int
    topic: str

@app.post("/api/media/filming-kit", dependencies=_admin_only)
def media_filming_kit(req: FilmingKitRequest):
    from agents.media_agent import create_filming_kit
    result = create_filming_kit(req.client_id, req.topic)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

class MediaTrendExamplesRequest(BaseModel):
    client_id: int

@app.post("/api/media/trend-examples", dependencies=_admin_only)
def media_trend_examples(req: MediaTrendExamplesRequest):
    # Sends the client real links to content working in their niche, into the
    # MEDIA specialist's chat thread. Plain `def` (web search + LLM, blocking).
    # `sent: False` is a normal success — it means research found no link it
    # could stand behind, or every link was already sent recently.
    from agents.media_agent import send_trend_examples
    result = send_trend_examples(req.client_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

class MediaPublishPrepRequest(BaseModel):
    client_id: int
    file_id: str

@app.post("/api/media/prepare-publish", dependencies=_admin_only)
def media_prepare_publish(req: MediaPublishPrepRequest):
    # Returns a public URL for a single Drive file — feed it to
    # /api/meta-content/publish as media_url
    from agents.media_agent import prepare_for_publishing
    return {"success": True, "data": prepare_for_publishing(req.client_id, req.file_id)}

@app.get("/api/media/weekly-checkin", dependencies=_admin_only)
def media_weekly_checkin():
    # THE sacred cadence: Cloud Scheduler, Saturdays 20:00 Asia/Jerusalem
    from agents.media_agent import run_weekly_media_checkin
    try:
        return {"success": True, "data": run_weekly_media_checkin()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/media/sync-site-folders", dependencies=_admin_only)
def media_sync_site_folders(client_id: int):
    from agents.media_agent import sync_website_media_folders
    return {"success": True, "data": {"pages": sync_website_media_folders(client_id)}}

# ─── Content docs (long-form scripts/homework as Google Docs) ────────────────

class ContentDocDeliverRequest(BaseModel):
    client_id: int
    title: str
    html_content: str
    doc_kind: str = "content"  # free-form: "script" | "homework" | "instructions" | ...
    body: str = ""             # optional override for the approval card's ask; Hebrew default otherwise

@app.post("/api/content-docs/deliver", dependencies=_admin_only)
def content_docs_deliver(req: ContentDocDeliverRequest):
    from agents.content_docs_agent import deliver_doc
    result = deliver_doc(req.client_id, req.title, req.html_content, req.doc_kind, req.body)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

@app.get("/api/admin/clients/{client_id}/content-docs")
def admin_content_docs(client_id: int, request: Request):
    """Admin dashboard drawer's visibility into delivered docs (session
    cookie, not X-Admin-Key — same browser-facing pattern as the SEO
    strategy/pending endpoints)."""
    _require_admin(request)
    from agents.content_docs_agent import list_delivered_docs
    return {"success": True, "data": list_delivered_docs(client_id)}

# ─── Avatar agent (distinct paid add-on — HeyGen twins + ElevenLabs voices) ──

class AvatarConnectRequest(BaseModel):
    # LEGACY (both keys in one form). Each key given is connected on its own
    # via core/vendor_tools; the dashboard uses /api/client/tools/{tool}/*
    heygen_key: str = ""
    elevenlabs_key: str = ""

@app.post("/api/avatar/connect")
def avatar_connect(req: AvatarConnectRequest, request: Request):
    # Session-gated self-service, same pattern as the Higgsfield card: the
    # client's OWN accounts, their payment methods, their keys
    client_id = _require_session(request)
    from agents.avatar_agent import connect_accounts
    result = connect_accounts(client_id, req.heygen_key, req.elevenlabs_key)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

class AvatarConsentRequest(BaseModel):
    scope: str  # likeness | voice

@app.post("/api/avatar/consent")
def avatar_consent(req: AvatarConsentRequest, request: Request):
    # The MANDATORY recorded consent step - the client's own explicit
    # confirmation (checkbox in the dashboard card), logged with statement
    # version + timestamp. Every creation path re-checks it server-side.
    client_id = _require_session(request)
    from agents.avatar_agent import record_consent
    result = record_consent(client_id, req.scope)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

class AvatarSourceRequest(BaseModel):
    client_id: int
    kind: str = "avatar"  # avatar | voice

@app.post("/api/avatar/request-source", dependencies=_admin_only)
def avatar_request_source(req: AvatarSourceRequest):
    from agents.avatar_agent import request_source_kit
    result = request_source_kit(req.client_id, req.kind)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

class AvatarCreateRequest(BaseModel):
    client_id: int
    avatar_name: str = ""

@app.post("/api/avatar/create", dependencies=_admin_only)
def avatar_create(req: AvatarCreateRequest):
    # Consent-gated in the agent; plain `def` - Drive + HeyGen uploads block
    from agents.avatar_agent import create_avatar
    result = create_avatar(req.client_id, req.avatar_name)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

class AvatarVoiceRequest(BaseModel):
    client_id: int
    file_ids: list[str] = []   # which recordings to clone from (default: the newest five)

@app.post("/api/avatar/create-voice", dependencies=_admin_only)
def avatar_create_voice(req: AvatarVoiceRequest):
    # Voice-cloning consent is re-checked inside; without it nothing is cloned
    from agents.avatar_agent import create_voice_clone
    result = create_voice_clone(req.client_id, req.file_ids or None)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=_creative_refusal(result))
    return {"success": True, "data": result}

class AvatarTierRequest(BaseModel):
    client_id: int
    tier_id: str  # basic | advanced | enhanced | custom (PRICING["avatar"])

@app.post("/api/avatar/set-tier", dependencies=_admin_only)
def avatar_set_tier(req: AvatarTierRequest):
    from agents.avatar_agent import set_tier
    result = set_tier(req.client_id, req.tier_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

class AvatarVideoRequest(BaseModel):
    client_id: int
    script_text: str = ""
    avatar_id: str = ""
    heygen_voice_id: str = ""
    provider: str = ""          # heygen | higgsfield (default: the client's choice)
    voice_kind: str = ""        # recording | clone | "" (HeyGen stock voice / legacy)
    voice_file_id: str = ""     # the recording, when voice_kind = recording

@app.post("/api/avatar/generate-video", dependencies=_admin_only)
def avatar_generate_video(req: AvatarVideoRequest):
    # SLOW (a render polls up to ~15 min). Tier + minutes + consent gates in
    # the agent; the provider layer (core/avatar_video) names a missing tool
    from agents.avatar_agent import generate_avatar_video
    voice = ({"kind": req.voice_kind, "file_id": req.voice_file_id} if req.voice_kind else None)
    result = generate_avatar_video(req.client_id, req.script_text, req.avatar_id,
                                   req.heygen_voice_id, req.provider, voice)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=_creative_refusal(result))
    return {"success": True, "data": result}

def _creative_refusal(result: dict) -> dict:
    # The plain Hebrew sentence (e.g. an ElevenLabs error) and which tool is
    # missing travel with the codes — never a key or a vendor response body
    return {"errors": result.get("errors", ["unknown error"]), "code": result.get("code", ""),
            "message_he": result.get("message_he", ""), "missing": result.get("missing", [])}

@app.get("/api/avatar/usage", dependencies=_admin_only)
def avatar_usage(client_id: int):
    from agents.avatar_agent import get_monthly_usage
    return {"success": True, "data": get_monthly_usage(client_id)}

@app.get("/api/avatar/list", dependencies=_admin_only)
def avatar_list(client_id: int):
    # The multi-avatar picker's data source - pass a chosen avatar_id to
    # /api/avatar/generate-video (default only auto-picks when exactly one)
    from agents.avatar_agent import list_ready_avatars
    return {"success": True, "data": {"avatars": list_ready_avatars(client_id)}}

# ─── Creative tools, one card per tool (core/vendor_tools) ──────────────────

_TOOL_ERRORS_HE = {
    "unknown_tool": "הכלי הזה לא מוכר.",
    "empty_key": "צריך להדביק את המפתח לפני החיבור.",
    "consent_required": "כדי לחבר את הדמות המצולמת שלך צריך לסמן את האישור לשימוש בדמות שלך.",
    "key_rejected": "הכלי דחה את המפתח. כדאי להעתיק אותו שוב מהחשבון שלך ולנסות שוב.",
    "not_connected": "הכלי הזה עוד לא מחובר.",
    "unknown_provider": "ספק הווידאו הזה לא מוכר.",
}

def _tool_error(e) -> HTTPException:
    code = getattr(e, "code", "") or "unknown"
    return HTTPException(status_code=400, detail={"code": code,
                         "message_he": _TOOL_ERRORS_HE.get(code, "משהו לא הצליח. ננסה שוב בעוד רגע.")})

def _creative_state_changed(client_id: int):
    # The agents' snapshot (client_state.creative_tools / .voice) must not
    # say "not connected" for two minutes after the client just connected
    from core import client_state
    client_state.forget(client_id)

class ToolConnectRequest(BaseModel):
    api_key: str
    consent: bool = False

class ToolProviderRequest(BaseModel):
    provider: str

@app.get("/api/client/tools")
def client_tools(request: Request):
    client_id = _require_session(request)
    from core import avatar_video, vendor_tools
    return {"success": True, "data": {"tools": vendor_tools.statuses(client_id),
                                      "providers": avatar_video.providers_view(client_id)}}

@app.post("/api/client/tools/provider")
def client_tools_provider(req: ToolProviderRequest, request: Request):
    client_id = _require_session(request)
    from core import vendor_tools
    try:
        vendor_tools.set_default_provider(client_id, req.provider)
    except vendor_tools.ToolError as e:
        raise _tool_error(e)
    _creative_state_changed(client_id)
    return {"success": True, "data": {"provider": req.provider}}

@app.post("/api/client/tools/{tool}/connect")
def client_tool_connect(tool: str, req: ToolConnectRequest, request: Request):
    # The key is never echoed back or logged; a rejected key is not stored
    client_id = _require_session(request)
    from core import vendor_tools
    try:
        result = vendor_tools.connect(client_id, tool, req.api_key, req.consent)
    except vendor_tools.ToolError as e:
        raise _tool_error(e)
    _creative_state_changed(client_id)
    return {"success": True, "data": result}

@app.post("/api/client/tools/{tool}/test")
def client_tool_test(tool: str, request: Request):
    client_id = _require_session(request)
    from core import vendor_tools
    try:
        result = vendor_tools.test(client_id, tool)
    except vendor_tools.ToolError as e:
        raise _tool_error(e)
    _creative_state_changed(client_id)
    return {"success": True, "data": result}

@app.post("/api/client/tools/{tool}/disconnect")
def client_tool_disconnect(tool: str, request: Request):
    # Deletes ONLY this tool's key - every other tool stays connected
    client_id = _require_session(request)
    from core import vendor_tools
    try:
        result = vendor_tools.disconnect(client_id, tool)
    except vendor_tools.ToolError as e:
        raise _tool_error(e)
    _creative_state_changed(client_id)
    return {"success": True, "data": result}

# ─── Voice recordings (core/voice_recordings) ───────────────────────────────

@app.get("/api/client/voice")
def client_voice_list(request: Request):
    client_id = _require_session(request)
    from core import voice_recordings as vr
    try:
        return {"success": True, "data": vr.for_agent(client_id) | {"recordings": vr.list_recordings(client_id)}}
    except Exception as e:
        print(f"[voice] client {client_id}: list failed: {type(e).__name__}")
        raise HTTPException(status_code=503, detail={"code": "storage", "message_he": vr.PLAIN_HE["storage"]})

@app.post("/api/client/voice/upload")
async def client_voice_upload(request: Request):
    # Raw body (no python-multipart in the image); name + duration in headers
    from urllib.parse import unquote
    from starlette.concurrency import run_in_threadpool
    from core import voice_recordings as vr
    client_id = _require_session(request)
    try:
        if int(request.headers.get("content-length") or 0) > vr.MAX_MB * 1024 * 1024:
            raise HTTPException(status_code=413, detail={"code": "too_big", "message_he": vr.PLAIN_HE["too_big"]})
    except ValueError:
        pass
    body = await request.body()
    name = unquote(request.headers.get("x-file-name") or "")
    try:
        row = await run_in_threadpool(vr.upload, client_id, name, body, request.headers.get("x-duration-seconds"))
    except vr.VoiceError as e:
        raise HTTPException(status_code=413 if e.code == "too_big" else 400,
                            detail={"code": e.code, "message_he": vr.PLAIN_HE.get(e.code, vr.PLAIN_HE["storage"])})
    except Exception as e:
        # The folder listing (file-count cap) could not be read
        print(f"[voice] client {client_id}: upload failed: {type(e).__name__}")
        raise HTTPException(status_code=503, detail={"code": "storage", "message_he": vr.PLAIN_HE["storage"]})
    _creative_state_changed(client_id)
    return {"success": True, "data": row}

class VoiceDeleteRequest(BaseModel):
    file_ids: list[str] = []
    all: bool = False
    delete_clone: bool = False

@app.post("/api/client/voice/delete")
def client_voice_delete(req: VoiceDeleteRequest, request: Request):
    client_id = _require_session(request)
    from core import voice_recordings as vr
    try:
        ids = [r["id"] for r in vr.list_recordings(client_id)] if req.all else req.file_ids
        result = vr.delete(client_id, ids)
    except Exception as e:
        print(f"[voice] client {client_id}: delete failed: {type(e).__name__}")
        raise HTTPException(status_code=503, detail={"code": "storage", "message_he": vr.PLAIN_HE["storage"]})
    if req.delete_clone:
        from agents.avatar_agent import delete_voice_clone
        clone = delete_voice_clone(client_id)
        if clone.get("success") is False:
            # The client asked to delete their voice: a failure is the TEAM's
            # job now (alert); the client reads only "the team is on it"
            alert("avatar_agent", [f"client {client_id}: asked to delete the voice clone but it was not "
                                   f"deleted ({clone.get('code', 'error')}) — delete it from their account by hand"])
        result["clone"] = {"success": clone.get("success"), "deleted": clone.get("deleted", False)}
    _creative_state_changed(client_id)
    return {"success": True, "data": result}

# ─── Upload from the chat (core/chat_uploads) ───────────────────────────────
# One fixed-size chunk per request, forwarded straight into a Drive resumable
# session: no size limit of ours, memory bounded by one chunk, and no request
# near the ~300 s timeout. Picking from the client's own Google Drive / Google
# Photos is NOT here: it needs new OAuth scopes — a later verification round.

_CHAT_UPLOAD_STATUS = {"empty": 400, "bad_chunk": 400, "unsupported": 415, "archive": 415, "unsafe": 415,
                       "too_big": 413, "expired": 410, "not_configured": 503, "storage": 503}

def _chat_upload_refused(e) -> HTTPException:
    from core import chat_uploads as cu
    return HTTPException(status_code=_CHAT_UPLOAD_STATUS.get(e.code, 400),
                         detail={"code": e.code, "message_he": cu.PLAIN_HE.get(e.code, cu.PLAIN_HE["storage"])})

@app.post("/api/client/chat-upload")
async def client_chat_upload(request: Request):
    # Raw body = ONE chunk; the name, total size, offset and the sealed
    # session token travel in headers (no python-multipart in the image)
    from urllib.parse import unquote
    from starlette.concurrency import run_in_threadpool
    from core import chat_uploads as cu
    client_id = _require_session(request)
    limit = cu.chunk_bytes()
    try:
        offset = int(request.headers.get("x-chunk-offset") or 0)
        total = int(request.headers.get("x-file-size") or 0)
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        raise _chat_upload_refused(cu.UploadRefused("bad_chunk"))
    if declared > limit:
        raise _chat_upload_refused(cu.UploadRefused("bad_chunk"))
    body = bytearray()
    async for piece in request.stream():
        body += piece
        if len(body) > limit:
            raise _chat_upload_refused(cu.UploadRefused("bad_chunk"))
    data = bytes(body)
    token = request.headers.get("x-upload-token") or ""
    try:
        if token:
            payload = cu.unseal(token, client_id)
            if not payload:
                raise cu.UploadRefused("expired")
        else:
            if offset != 0:
                raise cu.UploadRefused("bad_chunk")
            payload = await run_in_threadpool(
                cu.begin, client_id, cu.valid_persona(request.headers.get("x-persona") or ""),
                unquote(request.headers.get("x-file-name") or ""), total, data,
                request.headers.get("x-file-type") or "", request.headers.get("x-duration-seconds"))
            token = cu.seal(payload, client_id)
        answer = await run_in_threadpool(cu.send, client_id, payload, offset, data)
    except cu.UploadRefused as e:
        raise _chat_upload_refused(e)
    out = {"token": token, "kind": payload["k"], "name": payload["n"], "done": answer["done"],
           "next_offset": answer.get("next_offset")}
    if answer["done"]:
        out["result"] = await run_in_threadpool(cu.finish, client_id, payload, answer.get("file") or {})
    return {"success": True, "data": out}

@app.post("/api/client/chat-upload/status")
async def client_chat_upload_status(request: Request):
    # The resume: how many bytes Drive holds for this session
    from starlette.concurrency import run_in_threadpool
    from core import chat_uploads as cu
    client_id = _require_session(request)
    payload = cu.unseal(request.headers.get("x-upload-token") or "", client_id)
    try:
        if not payload:
            raise cu.UploadRefused("expired")
        answer = await run_in_threadpool(cu.status, client_id, payload)
    except cu.UploadRefused as e:
        raise _chat_upload_refused(e)
    out = {"done": answer["done"], "next_offset": answer.get("next_offset"), "kind": payload["k"],
           "name": payload["n"]}
    if answer["done"]:
        out["result"] = await run_in_threadpool(cu.finish, client_id, payload, answer.get("file") or {})
    return {"success": True, "data": out}

@app.get("/api/avatar/scan", dependencies=_admin_only)
def avatar_scan():
    # Daily readiness scan (twin training takes days - clients get notified,
    # never left wondering)
    from agents.avatar_agent import run_readiness_scan
    try:
        return {"success": True, "data": run_readiness_scan()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/client/media-folder")
def client_media_folder(request: Request):
    """Session-gated: the client's own browsable Drive folder link (created +
    shared with their email on first ask). Plain `def`: Drive HTTP calls."""
    client_id = _require_session(request)
    from agents.media_agent import media_folder_status
    folder = media_folder_status(client_id)  # logs + alerts (throttled) with the fix
    if folder["status"] == "not_configured":
        raise HTTPException(status_code=503, detail={"code": "ERR_MEDIA_NOT_CONFIGURED"})
    if folder["status"] != "ok":
        raise HTTPException(status_code=503, detail={"code": "ERR_MEDIA_FOLDER_FAILED"})
    return {"success": True, "data": {"link": folder["link"]}}

# ─── The client's OWN leads (client_leads - NOT uallak's `leads` funnel) ─────
#
# See core/client_leads.py for the distinction. Everything here is scoped to
# the session's client_id, except the capture endpoint, which is public by
# necessity (it is called from a form on the client's own website).

@app.get("/api/client/leads")
def client_leads_list(request: Request, status: str = "", source: str = "",
                      q: str = "", days: int = 0):
    # Plain `def`: blocking Supabase reads
    client_id = _require_session(request)
    try:
        return {"success": True, "data": {
            "leads": client_leads_service.list_leads(
                client_id, status=status, source=source, query=q, days=days),
            "stats": client_leads_service.lead_stats(client_id),
            "statuses": list(client_leads_service.STATUSES),
            "sources": list(client_leads_service.SOURCES),
        }}
    except Exception as e:
        # The client_leads table may not exist yet (migration not run). An
        # honest empty state beats a broken section - the UI shows the
        # not-yet-configured message rather than an error.
        print(f"[client_leads] list failed for client {client_id}: {e}")
        raise HTTPException(status_code=503, detail={"code": "ERR_LEADS_UNAVAILABLE"})

class LeadStatusRequest(BaseModel):
    status: str  # new | contacted | qualified | won | lost

@app.post("/api/client/leads/{lead_id}/status")
def client_lead_set_status(lead_id: int, req: LeadStatusRequest, request: Request):
    client_id = _require_session(request)
    try:
        updated = client_leads_service.set_status(client_id, lead_id, req.status)
    except ValueError:
        raise HTTPException(status_code=400, detail={"code": "ERR_UNKNOWN_STATUS"})
    if not updated:
        # Empty result = the row isn't this client's (or doesn't exist). Same
        # 404 either way, so a guessed id can't confirm another client's lead.
        raise HTTPException(status_code=404, detail={"code": "ERR_LEAD_NOT_FOUND"})
    return {"success": True, "data": updated}

class LeadNoteRequest(BaseModel):
    note: str = ""

@app.post("/api/client/leads/{lead_id}/note")
def client_lead_set_note(lead_id: int, req: LeadNoteRequest, request: Request):
    client_id = _require_session(request)
    updated = client_leads_service.set_note(client_id, lead_id, req.note)
    if not updated:
        raise HTTPException(status_code=404, detail={"code": "ERR_LEAD_NOT_FOUND"})
    return {"success": True, "data": updated}

@app.get("/api/client/lead-capture")
def client_lead_capture_setup(request: Request):
    """How this client's leads actually arrive, in the leads section.

    For a site WE built the honest answer is now "it's already connected" —
    website_agent wires the contact form at build time and the client never
    needs the token or the markup. The snippet stays in the response for the
    clients that genuinely need it (a site we don't manage), where the UI keeps
    it collapsed behind a developer toggle instead of showing everyone raw HTML.

    Site state is read from the activity log (lead_capture_summary), NOT by
    calling the client's site — this runs on a dashboard page load."""
    client_id = _require_session(request)
    token = create_lead_capture_token(client_id)
    base = os.environ.get("PUBLIC_APP_URL", "https://app.uallak.com").rstrip("/")
    url = f"{base}/api/leads/capture/{token}"

    try:
        from agents.website_agent import lead_capture_summary
        summary = lead_capture_summary(client_id)
    except Exception as e:
        # The panel is explanatory, not load-bearing: degrade to the manual
        # snippet rather than failing the request.
        print(f"[client_leads] lead-capture summary failed for client {client_id}: {e}")
        summary = {"site_connected": False, "managed_by_us": False,
                   "auto_wired": False, "page_link": ""}

    snippet = (
        f'<form action="{url}" method="post">\n'
        '  <input name="name" placeholder="שם">\n'
        '  <input name="phone" placeholder="טלפון">\n'
        '  <input name="email" placeholder="אימייל">\n'
        '  <textarea name="message" placeholder="במה נוכל לעזור?"></textarea>\n'
        '  <!-- optional: same-site https thank-you page to return to -->\n'
        '  <input type="hidden" name="redirect" value="https://YOUR-SITE/thanks">\n'
        '  <button type="submit">שליחה</button>\n'
        '</form>'
    )
    return {"success": True, "data": {
        "capture_url": url, "form_snippet": snippet,
        # auto_wired: our website agent already connected the client's contact
        # form — the UI says so instead of showing HTML nobody asked for.
        "auto_wired": summary["auto_wired"],
        "site_connected": summary["site_connected"],
        "managed_by_us": summary["managed_by_us"],
        "form_page_link": summary["page_link"],
    }}

# ─── Landing pages (client-facing, session-gated) ────────────────────────────
# Every client gets landing_pages.MAX_PAGES_PER_CLIENT included in their base
# package regardless of tier. The ceiling is enforced in core/landing_pages.py
# (server-side); the UI's own check is a courtesy, not the control.

def _landing_unavailable(client_id: int, error: Exception):
    print(f"[landing_pages] client {client_id}: {error}")
    raise HTTPException(status_code=503, detail={"code": "ERR_LANDING_UNAVAILABLE"})

@app.get("/api/client/landing-pages")
def client_landing_pages(request: Request):
    """The Landing Pages section: pages, live URLs (or DNS-pending state),
    per-page lead counts, and the domain card."""
    client_id = _require_session(request)
    try:
        from agents.landing_page_agent import dashboard_payload
        return {"success": True, "data": dashboard_payload(client_id)}
    except Exception as e:
        # Before the migration runs this is the honest state, same shape as the
        # leads section's own not-yet-provisioned answer.
        _landing_unavailable(client_id, e)

class LandingCreateRequest(BaseModel):
    title: str
    goal: str = ""       # which offer/campaign this page is for
    generate: bool = True

@app.post("/api/client/landing-pages")
def client_landing_create(req: LandingCreateRequest, request: Request):
    # Plain `def`: an LLM copy call plus DB writes, all blocking.
    client_id = _require_session(request)
    from agents.landing_page_agent import create_page
    result = create_page(client_id, req.title, req.goal, req.generate)
    if not result.get("success"):
        # The 3-page ceiling answers with its own code so the UI can offer the
        # "ask for another" path instead of showing a generic failure.
        raise HTTPException(status_code=400,
                            detail={"code": result.get("code", "ERR_LANDING_CREATE_FAILED"),
                                    "errors": result.get("errors", [])})
    return {"success": True, "data": result}

class LandingUpdateRequest(BaseModel):
    title: str = None
    goal: str = None
    content: dict = None

@app.post("/api/client/landing-pages/{page_id}")
def client_landing_update(page_id: int, req: LandingUpdateRequest, request: Request):
    client_id = _require_session(request)
    from core import landing_pages
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    result = landing_pages.update_page(client_id, page_id, fields)
    if not result.get("success"):
        raise HTTPException(status_code=400,
                            detail={"code": result.get("code", "ERR_LANDING_UPDATE_FAILED")})
    return {"success": True, "data": result["page"]}

@app.post("/api/client/landing-pages/{page_id}/publish")
def client_landing_publish(page_id: int, request: Request):
    client_id = _require_session(request)
    from agents.landing_page_agent import publish_page
    result = publish_page(client_id, page_id)
    if not result.get("success"):
        raise HTTPException(status_code=400,
                            detail={"code": result.get("code", "ERR_LANDING_PUBLISH_FAILED")})
    return {"success": True, "data": result}

class LandingRegenerateRequest(BaseModel):
    feedback: str = ""   # the client's own words on what to change

@app.post("/api/client/landing-pages/{page_id}/regenerate")
def client_landing_regenerate(page_id: int, req: LandingRegenerateRequest, request: Request):
    client_id = _require_session(request)
    from agents.landing_page_agent import regenerate_copy
    result = regenerate_copy(client_id, page_id, req.feedback)
    if not result.get("success"):
        raise HTTPException(status_code=400,
                            detail={"code": result.get("code", "ERR_LANDING_COPY_FAILED")})
    return {"success": True, "data": result["page"]}

@app.delete("/api/client/landing-pages/{page_id}")
def client_landing_delete(page_id: int, request: Request):
    client_id = _require_session(request)
    from core import landing_pages
    if not landing_pages.delete_page(client_id, page_id):
        raise HTTPException(status_code=404, detail={"code": "ERR_LANDING_NOT_FOUND"})
    return {"success": True}

class LandingExtraRequest(BaseModel):
    reason: str = ""

@app.post("/api/client/landing-pages/request-extra")
def client_landing_request_extra(req: LandingExtraRequest, request: Request):
    # A 4th page is NOT auto-created and NOT auto-priced — this records the
    # request, alerts Johnny, and tells the client a human is looking at it.
    client_id = _require_session(request)
    from agents.landing_page_agent import request_extra_page
    return {"success": True, "data": request_extra_page(client_id, req.reason)}

class LandingDomainRequest(BaseModel):
    hostname: str        # e.g. lp.theirbusiness.co.il

@app.post("/api/client/landing-domain")
def client_landing_domain_request(req: LandingDomainRequest, request: Request):
    client_id = _require_session(request)
    from agents.landing_page_agent import request_domain
    result = request_domain(client_id, req.hostname)
    if not result.get("success"):
        raise HTTPException(status_code=400,
                            detail={"code": result.get("code", "ERR_LANDING_BAD_HOSTNAME")})
    return {"success": True, "data": result}

@app.post("/api/client/landing-domain/verify")
def client_landing_domain_verify(request: Request):
    # Client-triggered "I've added the record" check. Reports the result into
    # the dashboard chat either way — including saying when the remaining work
    # is on OUR side, so nobody is sent to re-check a correct record.
    client_id = _require_session(request)
    from agents.landing_page_agent import verify_domain
    result = verify_domain(client_id)
    if not result.get("success"):
        raise HTTPException(status_code=400,
                            detail={"code": result.get("code", "ERR_LANDING_NO_DOMAIN")})
    return {"success": True, "data": result}

class AdminLandingCreateRequest(BaseModel):
    client_id: int
    title: str
    goal: str = ""
    generate: bool = True

@app.post("/api/landing-pages/admin/create", dependencies=_admin_only)
def admin_landing_create(req: AdminLandingCreateRequest):
    # Johnny's path for creating a page on a client's behalf — including the
    # 4th page AFTER he has made the pricing decision. It goes through the same
    # ceiling check, so granting an extra page is a deliberate act (raise the
    # client's allowance or delete an unused page), never an accident.
    from agents.landing_page_agent import create_page
    result = create_page(req.client_id, req.title, req.goal, req.generate)
    if not result.get("success"):
        raise HTTPException(status_code=400,
                            detail={"code": result.get("code", "ERR_LANDING_CREATE_FAILED"),
                                    "errors": result.get("errors", [])})
    return {"success": True, "data": result}

@app.post("/api/leads/capture/{token}")
async def public_lead_capture(token: str, request: Request,
                              background_tasks: BackgroundTasks):
    """PUBLIC. A form on the client's own site posts a lead here; the signed
    token in the path says which client it belongs to (it grants nothing else
    - see core/session.create_lead_capture_token).

    Accepts JSON or a classic form post, and always answers 200 for anything
    well-signed: a real customer filling in a contact form must never be shown
    an error because of a cap or a validation rule on our side. Whether the
    row was actually stored is in the response body, for callers that care."""
    client_id = verify_lead_capture_token(token)
    if not client_id:
        raise HTTPException(status_code=404, detail={"code": "ERR_INVALID_CAPTURE_TOKEN"})
    if deletion_guard.is_deleting(client_id):
        # Same 200 contract as a capped submit: the customer never sees an
        # error, nothing is stored for a client being deleted
        return {"success": True, "stored": False}

    # Body parsed by hand rather than with request.form(): Starlette's form
    # parsing asserts on `python-multipart`, which is NOT in requirements.txt,
    # so request.form() would raise on a plain urlencoded post - the exact
    # shape a <form method="post"> sends. Parsing urlencoded ourselves keeps
    # the classic HTML form working with no new dependency. multipart/form-data
    # (a form with a file input) is therefore NOT supported - documented in
    # HANDOFF-client-dashboard-nav.md, and no contact form needs it.
    from urllib.parse import parse_qs

    payload = {}
    content_type = (request.headers.get("content-type") or "").lower()
    try:
        raw = await request.body()
        if "application/json" in content_type:
            payload = json.loads(raw or b"{}")
        elif "application/x-www-form-urlencoded" in content_type:
            payload = {key: values[0] for key, values
                       in parse_qs(raw.decode("utf-8", "replace")).items() if values}
        elif raw:
            payload = json.loads(raw)  # no/odd content-type but a JSON body
    except Exception as e:
        print(f"[lead_capture] client {client_id}: unreadable body ({e})")

    if not isinstance(payload, dict):
        payload = {}

    referer = request.headers.get("referer", "")
    payload.setdefault("source_detail", referer)
    result = client_leads_service.capture_lead(
        client_id, payload, source=str(payload.get("source") or "website_form"))

    # OPT-IN external CRM push, AFTER the lead is safely stored and after this
    # response is sent. A background task (never inline) because the customer is
    # waiting on this request: a slow or broken CRM must not delay a form submit,
    # and our row is the record either way. Clients with no CRM connected return
    # immediately from sync_lead_safe — for them nothing changes at all.
    if result.get("stored") and result.get("id"):
        from agents.crm_agent import sync_lead_safe
        background_tasks.add_task(sync_lead_safe, client_id, result["id"])

    # A plain <form method="post"> navigates the browser to whatever this
    # returns, so a thank-you page is how the customer gets back to the site.
    # The target is accepted ONLY when it is https and on the SAME HOST the
    # form was submitted from - an unauthenticated public endpoint that
    # redirects anywhere a caller names is an open redirect, and this one
    # would sit on the client's own domain.
    redirect_to = str(payload.get("redirect") or "").strip()
    if redirect_to:
        from urllib.parse import urlparse
        target, origin = urlparse(redirect_to), urlparse(referer)
        if target.scheme == "https" and target.netloc and target.netloc == origin.netloc:
            return RedirectResponse(url=redirect_to, status_code=303)
        print(f"[lead_capture] client {client_id}: ignored off-site redirect "
              f"{redirect_to[:120]!r} (referer host {origin.netloc!r})")

    return {"success": True, "stored": result["stored"]}

# ─── External CRM (client-owned, opt-in) ─────────────────────────────────────
# Session-gated client self-service, same shape as /api/website/connect and
# /api/media/connect: the client pastes a credential for an account THEY own.
# Plain `def` throughout — every one of these makes a blocking HTTP call.

@app.get("/api/client/crm")
def client_crm_status(request: Request):
    client_id = _require_session(request)
    from agents.crm_agent import get_status
    return {"success": True, "data": get_status(client_id)}

class CRMConnectRequest(BaseModel):
    vendor: str          # a key from crm_service.VENDORS
    credential: str      # HubSpot private-app token / Pipedrive API token
    extra: str = ""      # vendor-specific second field (Pipedrive company domain)

@app.post("/api/client/crm/connect")
def client_crm_connect(req: CRMConnectRequest, request: Request):
    # The credential is verified against the live CRM before it is stored, so a
    # typo fails here rather than silently on the client's first real lead.
    client_id = _require_session(request)
    from agents.crm_agent import connect
    result = connect(client_id, req.vendor, req.credential, req.extra)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "connect failed"))
    return {"success": True, "data": result}

@app.post("/api/client/crm/disconnect")
def client_crm_disconnect(request: Request):
    client_id = _require_session(request)
    from agents.crm_agent import disconnect
    return {"success": True, "data": disconnect(client_id)}

@app.get("/api/crm/retry-syncs", dependencies=_admin_only)
def crm_retry_syncs():
    # Cron. Re-pushes leads whose CRM sync failed, up to MAX_SYNC_ATTEMPTS.
    # Never touches leads that were never attempted — see crm_agent.
    from agents.crm_agent import retry_failed_syncs
    return {"success": True, "data": retry_failed_syncs()}

# ─── Personal area + human support ───────────────────────────────────────────

@app.get("/api/client/account")
def client_account(request: Request):
    """Everything the אזור אישי section shows about the account itself. The
    business/contact fields already exist on the clients row (they are
    collected at checkout) - they had simply never been shown back to the
    client, who is the person they describe."""
    client_id = _require_session(request)
    client = get_client(client_id)
    if not client:
        raise HTTPException(status_code=401, detail="Not authenticated")
    sub = _client_subscription_info(client_id)
    return {"success": True, "data": {
        "business": {
            "business_name": client.get("business_name") or "",
            "business_tax_id": client.get("business_tax_id") or "",
            "address": client.get("address") or "",
        },
        "contact": {
            "name": client.get("name") or "",
            "email": client.get("email") or "",
            "phone": client.get("phone") or "",
        },
        "subscription": {
            "package": client.get("package") or "",
            "status": client.get("status") or "",
            "client_since": client.get("created_at"),
            "monthly_fee": sub["monthly_fee"],
            "setup_fee": sub["setup_fee"],
            "has_live_subscription": bool(sub["subscription_id"]),
        },
        # platform + status only, never the stored credentials
        "connections": [{"platform": a.get("platform"), "status": a.get("status")}
                        for a in get_accounts(client_id)],
    }}

@app.get("/api/client/support")
def client_support(request: Request):
    """The human rep's WhatsApp number, read from the SAME app_settings value
    the admin dashboard edits (admin_service.DEFAULT_SETTINGS['whatsapp_number'])
    - so changing it in one place changes it everywhere. Deliberately not a
    second constant: the landing page's hardcoded wa.me links are the existing
    duplicate, and this endpoint is what lets the dashboard avoid becoming a
    third one."""
    _require_session(request)
    # Falls back to the in-code default when app_settings is unreadable
    number = admin_service.support_whatsapp_number()
    return {"success": True, "data": {
        "whatsapp_number": number,
        "whatsapp_link": client_leads_service.whatsapp_link(number),
    }}

# ─── Media assets hub ────────────────────────────────────────────────────────

# Every asset any agent has ever produced for a client is ALREADY recorded as a
# client_activity row carrying result.file_id + result.link. This is the index;
# the files themselves live in the client's Drive folder (shared read-only with
# them on first touch). Nothing here duplicates storage - it reads what the
# generating agents already wrote.
_MEDIA_ASSET_KINDS = {
    # action_type (written by the agent named in the comment) -> UI kind
    "media_image_created":       "image",     # media_agent
    "media_video_created":       "video",     # media_agent
    "avatar_video_created":      "avatar",    # avatar_agent
    "media_filming_kit_created": "script",    # media_agent
    "content_doc_created":       "document",  # content_docs_agent
}

def _client_media_assets(client_id: int, limit: int = 1000) -> list:
    """Queries client_activity by action_type rather than paging get_activity():
    the hub promises EVERYTHING ever created, and a client with a busy activity
    feed would otherwise have their oldest media fall off the end of a generic
    'last N rows' read."""
    try:
        rows = (db.table("client_activity").select("*")
                .eq("client_id", client_id)
                .in_("action_type", list(_MEDIA_ASSET_KINDS))
                .order("created_at", desc=True)
                .limit(limit).execute().data or [])
    except Exception as e:
        print(f"[media_assets] activity query failed for client {client_id}: {e}")
        return []

    assets = []
    for entry in rows:
        kind = _MEDIA_ASSET_KINDS.get(entry.get("action_type"))
        if not kind:
            continue
        details = entry.get("details") or {}
        result = entry.get("result") or {}
        link = result.get("link") or ""
        # An asset row with no Drive link is one whose upload failed after
        # generation (the agents alert on that path and keep the activity row).
        # Show it, flagged - hiding it would make the hub quietly incomplete.
        assets.append({
            "id": entry.get("id"),
            "kind": kind,
            "created_at": entry.get("created_at"),
            "platform": details.get("platform") or "",
            "title": (details.get("title") or details.get("topic")
                      or details.get("brief") or details.get("script_preview") or ""),
            "link": link,
            "available": bool(link),
            "duration_seconds": details.get("duration_seconds"),
            # Lets the Media tab merge this index with the live Drive listing
            # (core/drive_mirror) without showing the same file twice
            "file_id": result.get("file_id") or details.get("file_id") or "",
        })
    return assets


# ─── Drive mirror (core/drive_mirror.py) — the client's folder, listed live ───
# Plain `def`: Drive HTTP calls. The folder is always resolved server-side from
# the session's client id; a folder id from the browser is only ever used
# after drive_mirror proves it sits inside that client's own folder.

def _drive_mirror_response(client_id: int, folder: str, page_token: str, page: int,
                           refresh: bool) -> dict:
    from core import drive_mirror
    if page_token and not drive_mirror.page_allowed(page):
        return {"success": True, "data": {"exists": True, "folder_id": folder, "folders": [],
                                          "files": [], "next_page_token": "", "truncated": True}}
    try:
        data = drive_mirror.list_folder(client_id, folder_id=folder.strip(),
                                        page_token=page_token.strip(), refresh=refresh)
    except drive_mirror.NotInClientFolder:
        raise HTTPException(status_code=404, detail={"code": "ERR_NOT_IN_CLIENT_FOLDER"})
    from core import drive_download
    # The proxy's per-file cap, so the UI can offer "Open in Drive" up front
    # instead of a download that would be refused
    data = {**data, "download_max_mb": drive_download.max_bytes() // (1024 * 1024)}
    return {"success": True, "data": data}

def _drive_thumb_response(client_id: int, file_id: str) -> Response:
    from core import drive_mirror
    try:
        content, content_type = drive_mirror.thumbnail(client_id, file_id)
    except drive_mirror.NotInClientFolder:
        raise HTTPException(status_code=404, detail={"code": "ERR_NOT_IN_CLIENT_FOLDER"})
    except Exception as e:
        print(f"[drive_mirror] thumbnail {file_id} for client {client_id} failed: {e}")
        raise HTTPException(status_code=404, detail={"code": "ERR_NO_THUMBNAIL"})
    return Response(content=content, media_type=content_type,
                    headers={"Cache-Control": f"private, max-age={drive_mirror.THUMB_CACHE_SECONDS}"})

# Preview + direct download (core/drive_download.py). ?check=1 runs every check
# (ownership, size, rate) and answers JSON, so the page can show a calm message
# BEFORE starting a download; without it the file streams as an attachment
# (?inline=1: raster images only, for the lightbox's fallback). The file id is
# re-verified against the session's client folder on every call. Videos and
# files over the cap are served by Drive, never by us — same rule for the
# admin route: the check says open_in_drive (200, not an error) and a direct
# request is redirected to the file in Drive.
_DRIVE_LINK_RE = re.compile(r"^https://(drive|docs)\.google\.com/")

def _drive_file_response(client_id: int, file_id: str, request: Request, by: str):
    from core import drive_download as dl
    inline = request.query_params.get("inline") == "1"
    check = request.query_params.get("check") == "1"
    fmt = "office" if request.query_params.get("format") == "office" else "pdf"
    try:
        plan = dl.plan(client_id, file_id, inline=inline, fmt=fmt)
        if check:
            dl.check_rate(f"{by}:{client_id}")
            return {"success": True, "data": {"ok": True, "filename": plan["filename"],
                                              "size": plan["size"], "max_mb": plan["max_mb"]}}
        body = dl.open_stream(plan, client_id, by)
    except dl.OpenInDrive as e:
        if not check and _DRIVE_LINK_RE.match(e.web_view_link):
            return RedirectResponse(e.web_view_link, status_code=303)
        return {"success": True, "data": e.as_data()}
    except dl.DownloadRefused as e:
        if inline:
            return Response(status_code=e.status)   # an <img> just falls back
        raise HTTPException(status_code=e.status, detail={"code": e.code, **e.extra})
    return StreamingResponse(body, media_type=plan["send_mime"], headers=dl.response_headers(plan))

@app.get("/api/client/media-drive/file/{file_id}")
def client_media_drive_file(file_id: str, request: Request):
    client_id = _require_session(request)
    by = "admin" if _admin_view_client(request) is not None else "client"
    return _drive_file_response(client_id, file_id, request, by)

@app.get("/api/admin/control-room/{client_id}/media-drive/file/{file_id}")
def admin_media_drive_file(client_id: int, file_id: str, request: Request):
    _require_admin(request)
    return _drive_file_response(client_id, file_id, request, "admin")

@app.get("/api/client/media-drive")
def client_media_drive(request: Request, folder: str = "", page_token: str = "",
                       page: int = 0, refresh: bool = False):
    return _drive_mirror_response(_require_session(request), folder, page_token, page, refresh)

@app.get("/api/client/media-drive/thumb/{file_id}")
def client_media_drive_thumb(file_id: str, request: Request):
    return _drive_thumb_response(_require_session(request), file_id)

@app.get("/api/admin/control-room/{client_id}/media-drive")
def admin_media_drive(client_id: int, request: Request, folder: str = "", page_token: str = "",
                      page: int = 0, refresh: bool = False):
    _require_admin(request)
    return _drive_mirror_response(client_id, folder, page_token, page, refresh)

@app.get("/api/admin/control-room/{client_id}/media-drive/thumb/{file_id}")
def admin_media_drive_thumb(client_id: int, file_id: str, request: Request):
    _require_admin(request)
    return _drive_thumb_response(client_id, file_id)

@app.get("/api/client/media-assets")
def client_media_assets(request: Request, kind: str = ""):
    """EVERYTHING ever created for this client - not just the pending-approval
    queue the homepage shows. Approval state is a moment in an asset's life;
    this is the whole library."""
    client_id = _require_session(request)
    assets = _client_media_assets(client_id)
    if kind:
        assets = [a for a in assets if a["kind"] == kind]
    counts = {}
    for asset in assets:
        counts[asset["kind"]] = counts.get(asset["kind"], 0) + 1
    # The browsable Drive folder link is best-effort: media may not be
    # configured, and that must not empty out an otherwise valid asset list.
    # folder_status says WHY there is no link, so the Media tab can explain
    # instead of showing a silently greyed-out button.
    try:
        from agents.media_agent import media_folder_status
        # media_folder_status creates + shares the folder on first ask — a
        # write the admin view must not trigger (core/admin_view)
        folder = ({"status": "admin_view"} if _admin_view_client(request) is not None
                  else media_folder_status(client_id))
    except Exception as e:
        print(f"[media_assets] folder status unavailable for client {client_id}: {e}")
        folder = {"status": "error", "reason": "other"}
    return {"success": True, "data": {"assets": assets, "counts": counts,
                                      "folder_link": folder.get("link", ""),
                                      "folder_status": folder["status"]}}

# ─── File exports (PDF / Excel / Google Doc) ─────────────────────────────────

def _export_dataset(client_id: int, dataset: str) -> dict:
    """One dict per exportable view: {title, subtitle, columns, rows}. Adding a
    dataset is a new branch here and nothing else - the three renderers in
    core/export_service all consume this same shape."""
    client = get_client(client_id) or {}
    name = client.get("name") or f"client-{client_id}"

    if dataset == "leads":
        rows = client_leads_service.list_leads(client_id, limit=1000)
        status_he = {"new": "חדש", "contacted": "נוצר קשר", "qualified": "מתאים",
                     "won": "נסגר", "lost": "לא רלוונטי"}
        return {
            "title": "הלידים שלי",
            "subtitle": name,
            "columns": [("created_at", "תאריך"), ("name", "שם"), ("phone", "טלפון"),
                        ("email", "אימייל"), ("message", "הודעה"),
                        ("source", "מקור"), ("status", "סטטוס"), ("notes", "הערות")],
            "rows": [{**r, "status": status_he.get(r.get("status"), r.get("status", ""))}
                     for r in rows],
        }

    if dataset == "media":
        kind_he = {"image": "תמונה", "video": "סרטון", "avatar": "וידאו אווטאר",
                   "script": "תסריט צילום", "document": "מסמך"}
        return {
            "title": "המדיה שנוצרה עבורי",
            "subtitle": name,
            "columns": [("created_at", "תאריך"), ("kind", "סוג"),
                        ("platform", "פלטפורמה"), ("title", "תיאור"), ("link", "קישור")],
            "rows": [{**a, "kind": kind_he.get(a["kind"], a["kind"])}
                     for a in _client_media_assets(client_id)],
        }

    if dataset == "activity":
        return {
            "title": "פעילות המערכת",
            "subtitle": name,
            "columns": [("created_at", "תאריך"), ("agent_name", "סוכן"),
                        ("action_type", "פעולה")],
            "rows": get_activity(client_id, limit=500),
        }

    if dataset == "billing":
        sub = _client_subscription_info(client_id)
        transactions = []
        if sub["subscription_id"]:
            try:
                start = sub["checkout_at"] or (
                    datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
                transactions = list_subscription_transactions(
                    sub["subscription_id"], start, datetime.now(timezone.utc).isoformat())
            except Exception as e:
                print(f"[export] billing transactions failed for client {client_id}: {e}")
        # list_subscription_transactions already flattens PayPal's nested
        # amount_with_breakdown into {date, amount, currency, status} - the
        # same rows the billing card renders. Don't re-derive them here.
        return {
            "title": "היסטוריית חיובים",
            "subtitle": name,
            "columns": [("date", "תאריך"), ("amount", "סכום"),
                        ("currency", "מטבע"), ("status", "סטטוס")],
            "rows": transactions,
        }

    raise HTTPException(status_code=404, detail={"code": "ERR_UNKNOWN_DATASET"})

@app.get("/api/client/export/{dataset}")
def client_export(dataset: str, request: Request, format: str = "xlsx"):
    """format=xlsx  -> a real .xlsx download (written by core/export_service,
                       stdlib zipfile - no new dependency).
       format=pdf   -> a print-styled HTML page that opens its own print
                       dialog; the client saves it as PDF from there. See
                       core/export_service's docstring for why the PDF is not
                       rendered server-side (Hebrew needs an embedded font +
                       bidi shaping that the container does not have)."""
    # Plain `def`: the billing dataset makes blocking PayPal calls
    client_id = _require_session(request)
    data = _export_dataset(client_id, dataset)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if format == "pdf":
        return Response(
            content=export_service.print_html(
                data["title"], data["subtitle"], data["columns"], data["rows"]),
            media_type="text/html; charset=utf-8",
        )
    if format == "xlsx":
        return Response(
            content=export_service.to_xlsx(data["title"], data["columns"], data["rows"]),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition":
                     f'attachment; filename="uallak-{dataset}-{stamp}.xlsx"'},
        )
    raise HTTPException(status_code=400, detail={"code": "ERR_UNKNOWN_FORMAT"})

def _export_doc_to_drive(client_id: int, client: dict, title: str, html: str) -> dict:
    """A native Google Doc in the client's exports folder, wherever their
    files live (core/drive_storage). In OUR Shared Drive it is shared with
    the client as a writer (their OWN data — edit rights to copy it
    comfortably); in their own Drive it is already theirs."""
    from agents.media_agent import _subfolder
    from core import drive_storage
    with drive_storage.scope(client_id):
        uploaded = drive_service.upload_google_doc(_subfolder(client_id, "exports"), title, html)
        if client.get("email") and drive_storage.location(client_id)["kind"] != drive_storage.CLIENT:
            drive_service.share_with_user(uploaded["id"], client["email"], role="writer")
    return uploaded

@app.post("/api/client/export/{dataset}/google-doc")
def client_export_google_doc(dataset: str, request: Request):
    """Creates a REAL, editable Google Doc of the dataset and returns its link.

    OWNERSHIP CAVEAT, stated plainly because it is a real limitation: the Doc
    is created by OUR service account, in uallak's Drive, and then shared with
    the client's email as a writer. It is not created IN the client's own
    Drive, because no client-side Google OAuth in this codebase carries a
    Drive scope - the client Google flows (Ads, GTM, YouTube) request
    advertising scopes only. The client can 'Make a copy' to own it
    outright. Round 2 (DRIVE_FILE_ENABLED, off) closes that gap: once the
    client's files live in their OWN Drive (core/drive_storage), the Doc is
    created there, owned by them, and nothing is shared."""
    # Plain `def`: Drive HTTP calls
    client_id = _require_session(request)
    from core import drive_storage
    # Media gate only - the offboarding ARCHIVE folder has nothing to do with this
    if not drive_storage.configured(client_id):
        raise HTTPException(status_code=503, detail={"code": "ERR_MEDIA_NOT_CONFIGURED"})

    data = _export_dataset(client_id, dataset)
    client = get_client(client_id) or {}
    try:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
        uploaded = _export_doc_to_drive(client_id, client, f"{data['title']} — {stamp}",
                                        export_service.doc_html(data["title"], data["subtitle"],
                                                                data["columns"], data["rows"]))
    except Exception as e:
        print(f"[export] google doc failed for client {client_id}/{dataset}: {e}")
        raise HTTPException(status_code=502, detail={"code": "ERR_GOOGLE_DOC_FAILED"})

    log_activity(client_id, "export_service", "export_google_doc_created",
                 {"dataset": dataset}, {"link": uploaded.get("webViewLink", "")})
    return {"success": True, "data": {"link": uploaded.get("webViewLink", ""),
                                      "shared_with": client.get("email", "")}}

# ─── Ownership & portability (core/ownership) ────────────────────────────────
# Everything the business owns, our access to it, and leaving: the
# "החשבונות והנכסים שלי" page, "ייצוא הכל", the handover document and the
# admin offboarding kit. No route here ever reads a secret.

@app.get("/api/client/ownership")
def client_ownership(request: Request, lang: str = "he"):
    # Plain `def`: blocking Supabase reads
    client_id = _require_session(request)
    from core import ownership
    accounts = get_accounts(client_id)
    return {"success": True, "data": {
        "accounts": ownership.accounts_view(client_id, _DISCONNECT_GROUPS, lang, accounts=accounts),
        "assets": ownership.assets_view(client_id, lang, accounts=accounts),
    }}

def _handover_file(doc: dict, fmt: str, client_id: int) -> Response:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if fmt == "pdf":
        return Response(content=export_service.print_html(doc["title"], doc["subtitle"], doc["columns"],
                                                          doc["rows"], summary=doc["summary"]),
                        media_type="text/html; charset=utf-8")
    if fmt == "docx":
        return Response(content=export_service.to_docx(doc["title"], doc["subtitle"], doc["columns"],
                                                       doc["rows"], summary=doc["summary"]),
                        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        headers={"Content-Disposition":
                                 f'attachment; filename="uallak-handover-{client_id}-{stamp}.docx"'})
    raise HTTPException(status_code=400, detail={"code": "ERR_UNKNOWN_FORMAT"})

@app.get("/api/client/ownership/handover")
def client_ownership_handover(request: Request, format: str = "pdf"):
    """The handover document for THIS session's client, any time — not only
    when leaving: knowing what you own is the point."""
    client_id = _require_session(request)
    from core import ownership
    doc = ownership.handover(client_id, _DISCONNECT_GROUPS, get_client(client_id) or {})
    return _handover_file(doc, format, client_id)

def _export_all_zip(client_id: int) -> bytes:
    """"ייצוא הכל": one .zip of files in the formats we already export —
    Excel per dataset, the full record as JSON, the handover document — built
    for the SESSION's client only. A part that fails is listed in the README
    instead of sinking the rest. Videos and media are LINKS (they never pass
    through our server — core/drive_download)."""
    import io
    import zipfile
    from core import landing_pages, outreach_tracker, ownership
    files, missing = {}, []

    def add(name: str, build):
        try:
            content = build()
        except Exception as e:
            print(f"[export_all] client {client_id}: {name} failed: {type(e).__name__}")
            missing.append(name)
            return
        if content is not None:
            files[name] = content

    def dataset(name: str):
        data = _export_dataset(client_id, name)
        return export_service.to_xlsx(data["title"], data["columns"], data["rows"])

    def outreach():
        rows = outreach_tracker.page(client_id)["rows"]
        if not rows:
            return None
        columns, table = outreach_tracker.export_table(rows)
        return export_service.to_xlsx("פניות לעסקים", columns, table)

    def landing():
        pages = landing_pages.list_pages(client_id)
        if not pages:
            return None
        rows = []
        for p in pages:
            content = p.get("content") or {}
            rows.append({"created_at": p.get("created_at"), "title": p.get("title"), "slug": p.get("slug"),
                         "status": p.get("status"), "headline": content.get("headline"),
                         "subheadline": content.get("subheadline"),
                         "benefits": " • ".join(content.get("benefits") or []),
                         "sections": " • ".join(f"{s.get('title', '')}: {s.get('body', '')}"
                                                for s in content.get("sections") or []),
                         "cta_text": content.get("cta_text")})
        return export_service.to_xlsx("דפי נחיתה", [
            ("created_at", "תאריך"), ("title", "שם"), ("slug", "כתובת"), ("status", "סטטוס"),
            ("headline", "כותרת"), ("subheadline", "כותרת משנה"), ("benefits", "יתרונות"),
            ("sections", "חלקים"), ("cta_text", "כפתור")], rows)

    def handover():
        doc = ownership.handover(client_id, _DISCONNECT_GROUPS, get_client(client_id) or {})
        return export_service.to_docx(doc["title"], doc["subtitle"], doc["columns"], doc["rows"],
                                      summary=doc["summary"])

    add("leads.xlsx", lambda: dataset("leads"))
    add("media-and-videos.xlsx", lambda: dataset("media"))
    add("outreach.xlsx", outreach)
    add("landing-pages.xlsx", landing)
    add("activity-report.xlsx", lambda: dataset("activity"))
    add("billing.xlsx", lambda: dataset("billing"))
    add("handover.docx", handover)
    add("full-record.json", lambda: json.dumps(_build_client_export(client_id), ensure_ascii=False,
                                                indent=2, default=str).encode("utf-8"))

    readme = ["ייצוא הכל — uallak",
              "כל הקבצים כאן שייכים לעסק שלך. אין בהם אף מפתח או סיסמה.",
              "תמונות וסרטונים: הקובץ media-and-videos.xlsx כולל קישור לכל קובץ; הסרטונים עצמם נמצאים "
              "ב-Drive, ב-YouTube או ב-TikTok שלך.",
              "", "Export everything — uallak. Every file here belongs to your business; no key or password "
              "is included. Media and videos are listed with a link to each file."]
    if missing:
        readme += ["", "לא נכללו הפעם (אפשר לנסות שוב בעוד רגע): " + ", ".join(missing)]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", ("﻿" + "\n".join(readme)).encode("utf-8"))
        for name, content in files.items():
            z.writestr(name, content)
    return buffer.getvalue()

@app.get("/api/client/export-all")
def client_export_all(request: Request):
    # Plain `def`: PayPal + ad platforms + Supabase, all blocking
    client_id = _require_session(request)
    content = _export_all_zip(client_id)
    log_activity(client_id, "export_service", "export_all_downloaded", {}, {})
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return Response(content=content, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="uallak-export-all-{stamp}.zip"'})

class OffboardingKitRequest(BaseModel):
    confirm: str = ""     # the client id, typed

_OFFBOARDING_KIT_CHAT = ("הכנו עבורך מסמך העברה עם כל החשבונות והנכסים של העסק, ומה עושים עם כל אחד. "
                         "הסרנו את הגישה של uallak מכל החשבונות שחיברת — שום דבר שלך אצל הספקים לא נמחק. "
                         "את המסמך ואת כל המידע שלך אפשר להוריד בכל רגע בעמוד \"החשבונות והנכסים שלי\".")

@app.post("/api/admin/clients/{client_id}/offboarding-kit")
def admin_offboarding_kit(client_id: int, req: OffboardingKitRequest, request: Request):
    """Admin offboarding kit: the handover document FIRST (a snapshot while
    our access still exists), then our access is removed everywhere through
    the client's own disconnect path. Never deletes the client's data at a
    vendor, never touches billing, status or records — closure/transfer and
    deletion are separate actions. Plain `def`: provider revoke calls."""
    _require_admin(request)
    from core import ownership
    client = get_client(client_id)
    if not client:
        raise HTTPException(status_code=404, detail={"code": "ERR_CLIENT_NOT_FOUND"})
    if (req.confirm or "").strip() != str(client_id):
        raise HTTPException(status_code=400, detail={"code": "ERR_CONFIRM_MISMATCH"})

    doc = ownership.handover(client_id, _DISCONNECT_GROUPS, client)
    html = export_service.print_html(doc["title"], doc["subtitle"], doc["columns"], doc["rows"],
                                     summary=doc["summary"])
    doc_link = ""
    from core import drive_storage
    if drive_storage.configured(client_id):
        try:
            uploaded = _export_doc_to_drive(
                client_id, client, doc["title"],
                export_service.doc_html(doc["title"], doc["subtitle"], doc["columns"], doc["rows"]))
            doc_link = uploaded.get("webViewLink", "")
        except Exception as e:
            print(f"[offboarding_kit] client {client_id}: Google Doc not created: {type(e).__name__}")

    result = ownership.revoke_all(client_id, _DISCONNECT_GROUPS, _disconnect_platform)
    not_confirmed = [r["group"] for r in result["revoked"] if r["provider_revoked"] is False]
    if result["failed"] or not_confirmed:
        alert("client_offboarding", [
            f"client {client_id}: offboarding kit - access removal needs a manual check: "
            f"failed {[f['group'] for f in result['failed']]}, not confirmed at the provider {not_confirmed}"])
    log_activity(client_id, "ownership", "offboarding_kit_created",
                 {"revoked": [r["group"] for r in result["revoked"]],
                  "failed": [f["group"] for f in result["failed"]], "not_confirmed": not_confirmed},
                 {"doc_link": doc_link})
    try:
        log_communication(client_id, "outbound", "dashboard_chat", _OFFBOARDING_KIT_CHAT)
    except Exception as e:
        print(f"[offboarding_kit] client {client_id}: chat note not posted: {type(e).__name__}")
    return {"success": True, "data": {"html": html, "doc_link": doc_link, "revoked": result["revoked"],
                                      "failed": result["failed"], "not_confirmed": not_confirmed}}

# ─── Outreach page (core/outreach_tracker) ───────────────────────────────────
# One row per business the client's Gmail agent found / drafted for / wrote
# to. The client's marks ("replied", "asked to be removed", "bounced") are the
# only way we learn about replies: we hold gmail.send and never read mail.

class OutreachKeysRequest(BaseModel):
    keys: list[str] = []
    mark: str = ""

class OutreachExportRequest(BaseModel):
    format: str = "csv"          # csv | sheets | xlsx | pdf | docx
    status: str = ""
    q: str = ""
    keys: list[str] = []         # the selected rows (else: the filtered ones)
    client_id: int | None = None  # admin export only

_OUTREACH_FORMATS = {
    "csv": ("csv", "text/csv; charset=utf-8"),
    "sheets": ("google-sheets.csv", "text/csv; charset=utf-8"),
    "xlsx": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "docx": ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "pdf": (None, "text/html; charset=utf-8"),
}

def _outreach_file(fmt: str, subtitle: str, columns: list, rows: list, summary: list) -> Response:
    """The same rows in every format. No Google API is involved: Sheets/Docs
    open these files by import — no Drive/Sheets/Docs scope."""
    if fmt not in _OUTREACH_FORMATS:
        raise HTTPException(status_code=400, detail={"code": "ERR_UNKNOWN_FORMAT"})
    title = "פניות לעסקים"
    extension, media_type = _OUTREACH_FORMATS[fmt]
    if fmt == "pdf":
        return Response(content=export_service.print_html(title, subtitle, columns, rows, summary=summary),
                        media_type=media_type)
    if fmt in ("csv", "sheets"):
        content = export_service.to_csv(columns, rows, bom=(fmt == "csv"))
    elif fmt == "xlsx":
        content = export_service.to_xlsx(title, columns, rows)
    else:
        content = export_service.to_docx(title, subtitle, columns, rows, summary=summary)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return Response(content=content, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="uallak-outreach-{stamp}.{extension}"'})

@app.get("/api/client/outreach")
def client_outreach(request: Request):
    # Plain `def`: several blocking Supabase reads
    client_id = _require_session(request)
    from core import gmail_agent_plan, outreach_tracker
    data = outreach_tracker.page(client_id)
    return {"success": True, "data": {**data, "in_plan": gmail_agent_plan.entitled(client_id)}}

@app.post("/api/client/outreach/mark")
def client_outreach_mark(req: OutreachKeysRequest, request: Request):
    # No package check on purpose: a client who left the package must still
    # be able to say "asked to be removed"
    client_id = _require_session(request)
    from core import outreach_tracker
    try:
        result = outreach_tracker.mark(client_id, req.keys[:200], req.mark, by="client")
    except outreach_tracker.OutreachError as e:
        raise HTTPException(status_code=503 if e.code == "not_ready" else 400, detail={"code": e.code})
    return {"success": True, "data": result}

@app.post("/api/client/outreach/delete")
def client_outreach_delete(req: OutreachKeysRequest, request: Request):
    client_id = _require_session(request)
    from core import outreach_tracker
    return {"success": True, "data": outreach_tracker.delete(client_id, req.keys[:200])}

@app.post("/api/client/outreach/export")
def client_outreach_export(req: OutreachExportRequest, request: Request):
    # Tenant isolation: rows are built for the SESSION's client only; the
    # selection is applied after that, so another client's key matches nothing
    client_id = _require_session(request)
    from core import outreach_tracker
    data = outreach_tracker.page(client_id)
    rows = outreach_tracker.filter_rows(data["rows"], req.status, req.q, req.keys or None,
                                        client_id=client_id)
    columns, table = outreach_tracker.export_table(rows)
    client = get_client(client_id) or {}
    return _outreach_file(req.format, client.get("business_name") or client.get("name") or "",
                          columns, table, outreach_tracker.summary_lines(rows, data["cap"]))

def _outreach_client_names(rows: list) -> dict:
    names = {}
    for cid in {r["client_id"] for r in rows}:
        client = get_client(cid) or {}
        names[cid] = client.get("business_name") or client.get("name") or ""
    return names

@app.get("/api/admin/outreach")
def admin_outreach(request: Request, client_id: int | None = None):
    _require_admin(request)
    from core import outreach_tracker
    data = outreach_tracker.collect(client_id)
    names = _outreach_client_names(data["rows"])
    for row in data["rows"]:
        row["client_name"] = names.get(row["client_id"], "")
    return {"success": True, "data": {"rows": data["rows"], "gaps": data["gaps"],
                                      "counters": outreach_tracker.counters(data["rows"]),
                                      "sending_enabled": outreach_tracker._sending_enabled()}}

@app.post("/api/admin/outreach/export")
def admin_outreach_export(req: OutreachExportRequest, request: Request):
    _require_admin(request)
    from core import outreach_tracker
    data = outreach_tracker.collect(req.client_id)
    rows = outreach_tracker.filter_rows(data["rows"], req.status, req.q, req.keys or None,
                                        client_id=req.client_id)
    columns, table = outreach_tracker.export_table(rows, admin=True,
                                                   client_names=_outreach_client_names(rows))
    subtitle = f"לקוח #{req.client_id}" if req.client_id else "כל הלקוחות"
    return _outreach_file(req.format, subtitle, columns, table, outreach_tracker.summary_lines(rows))

# ─── Proactive engagement (suggestions, sales alerts, urgent notifications) ──

@app.get("/api/client/suggestions")
async def client_suggestions(request: Request):
    client_id = _require_session(request)
    from agents.engagement_agent import get_suggestions
    pending = get_suggestions(client_id, status="pending")
    return {"success": True, "data": [
        {"id": s["id"], "kind": s.get("kind"), "title": s.get("title"),
         "body": s.get("body"), "created_at": s.get("created_at")}
        for s in pending
    ]}

class SuggestionDecideRequest(BaseModel):
    decision: str  # approved | rejected

@app.post("/api/client/suggestions/{suggestion_id}/decide")
async def client_suggestion_decide(suggestion_id: int, req: SuggestionDecideRequest,
                                   request: Request, background_tasks: BackgroundTasks):
    client_id = _require_session(request)
    from agents.engagement_agent import decide_suggestion
    # Approving a media suggestion can kick off generation that takes up to
    # ~10 minutes (media_gen_service polling) - never block this request on
    # it. background_tasks runs the actual work AFTER the response is sent.
    result = decide_suggestion(client_id, suggestion_id, req.decision, background_tasks)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "decide failed"))
    return {"success": True, "data": result}

class ClientProfileRequest(BaseModel):
    owner_gender: str | None = None   # male | female — the client's own one-tap choice in
                                      # the dashboard (never inferred from their name)
    profile_image: str | None = None  # data:image/... URL, resized in the browser before
                                      # upload; "" removes the picture
    language: str | None = None       # he|en|fr|ar|ru — kept in sync with uallakI18n's
                                      # switcher choice; the stored preference outbound
                                      # emails use (no live message to detect language from)

# ~200KB of base64 ≈ a 150KB image - far more than the browser-side resize
# produces, just a hard stop against arbitrary-size uploads into the DB
MAX_PROFILE_IMAGE_CHARS = 200_000

@app.post("/api/client/profile")
async def client_profile(req: ClientProfileRequest, request: Request):
    client_id = _require_session(request)
    changes = {}
    if req.owner_gender is not None:
        if req.owner_gender not in ("male", "female"):
            raise HTTPException(status_code=400, detail="owner_gender must be male|female")
        changes["owner_gender"] = req.owner_gender
    if req.profile_image is not None:
        if req.profile_image and not req.profile_image.startswith("data:image/"):
            raise HTTPException(status_code=400, detail="profile_image must be a data:image/ URL")
        if len(req.profile_image) > MAX_PROFILE_IMAGE_CHARS:
            raise HTTPException(status_code=400, detail="profile_image too large")
        changes["profile_image"] = req.profile_image or None
    if req.language is not None:
        if req.language not in ("he", "en", "fr", "ar", "ru"):
            raise HTTPException(status_code=400, detail="language must be he|en|fr|ar|ru")
        changes["language"] = req.language
    if not changes:
        raise HTTPException(status_code=400, detail="nothing to update")
    try:
        db.table("clients").update(changes).eq("id", client_id).execute()
    except Exception as e:
        # clients.language may not exist in Supabase yet - retry without it
        # rather than failing the whole request (e.g. a profile_image update
        # bundled in the same call must still succeed)
        if "language" in changes:
            print(f"[client_profile] update without language (column missing?): {e}")
            changes.pop("language")
            if changes:
                db.table("clients").update(changes).eq("id", client_id).execute()
        else:
            raise
    return {"success": True}

@app.get("/api/engagement/weekly", dependencies=_admin_only)
def engagement_weekly():
    # Plain `def`: one blocking LLM call per active client
    from agents.engagement_agent import run_weekly_engagement
    try:
        return {"success": True, "data": run_weekly_engagement()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/engagement/daily", dependencies=_admin_only)
def engagement_daily():
    from agents.engagement_agent import run_daily_engagement
    try:
        return {"success": True, "data": run_daily_engagement()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class WhatsAppNotifyRequest(BaseModel):
    client_id: int
    message: str

@app.post("/api/notify/whatsapp", dependencies=_admin_only)
def notify_whatsapp(req: WhatsAppNotifyRequest):
    # Manual/automation SOS entry point - same ladder as the health scans use
    from agents.engagement_agent import notify_client_urgent
    return {"success": True, "data": notify_client_urgent(req.client_id, req.message)}

# ─── Admin dashboard (browser session, separate from X-Admin-Key) ────────────

def _require_admin(request: Request):
    if not verify_admin_session_token(request.cookies.get("admin_session")):
        raise HTTPException(status_code=401, detail="Not authenticated")

class AdminLoginRequest(BaseModel):
    password: str

@app.post("/api/admin/login")
def admin_login(req: AdminLoginRequest, response: Response):
    admin_password = os.environ.get("ADMIN_PASSWORD", "")
    if not admin_password or not secrets.compare_digest(req.password.encode(), admin_password.encode()):
        time.sleep(1)  # cheap brute-force damper (runs in the threadpool, not the event loop)
        raise HTTPException(status_code=401, detail="סיסמה שגויה")
    response.set_cookie(
        key="admin_session",
        value=create_admin_session_token(),
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=7 * 24 * 60 * 60,
        path="/",
    )
    return {"success": True}

@app.post("/api/admin/logout")
async def admin_logout(response: Response):
    response.delete_cookie("admin_session", path="/")
    response.delete_cookie(admin_view.COOKIE, path="/")   # dead without the session anyway
    return {"success": True}

@app.get("/api/admin/overview")
def admin_overview(request: Request):
    _require_admin(request)
    return {"success": True, "data": admin_service.get_overview()}

@app.get("/api/admin/clients")
def admin_clients(request: Request):
    _require_admin(request)
    return {"success": True, "data": admin_service.list_clients_admin()}

# ─── Admin "enter as client" (core/admin_view) ───────────────────────────────
# Read-only view of the client's own dashboard. Sets ONLY the admin_view
# cookie; never a client session, never touches the client's credentials.

@app.post("/api/admin/clients/{client_id}/view-as")
def admin_view_as_start(client_id: int, request: Request, response: Response):
    _require_admin(request)
    client = get_client(client_id)
    if _locked_out(client):
        raise HTTPException(status_code=404, detail={"code": "ERR_CLIENT_NOT_VIEWABLE"})
    try:
        _admin_view_audit(request, "view_as_start", client_id)
    except admin_view.AuditUnavailable:
        raise HTTPException(status_code=503, detail={"code": "ERR_AUDIT_UNAVAILABLE"})
    response.set_cookie(
        key=admin_view.COOKIE,
        value=create_admin_view_token(client_id, request.cookies.get("admin_session")),
        httponly=True, secure=True, samesite="lax",
        max_age=2 * 60 * 60, path="/",
    )
    return {"success": True, "redirect": "/dashboard/"}

@app.post("/api/admin/view-as/exit")
def admin_view_as_exit(request: Request, response: Response):
    # Always clears the cookie; the stop is audited when there was a live view
    viewed = _admin_view_client(request)
    if viewed is not None:
        try:
            _admin_view_audit(request, "view_as_stop", viewed)
        except admin_view.AuditUnavailable:
            alert("admin_view", [f"client {viewed}: admin view ended but the stop was not audited"])
    response.delete_cookie(admin_view.COOKIE, path="/")
    return {"success": True, "redirect": "/admin/"}

@app.get("/api/admin/view-as/audit")
def admin_view_as_audit(request: Request, limit: int = 100):
    _require_admin(request)
    try:
        return {"success": True, "data": admin_view.recent(max(1, min(int(limit), 500)))}
    except Exception as e:
        print(f"[admin_view] audit read failed: {type(e).__name__}")
        raise HTTPException(status_code=503, detail={"code": "ERR_AUDIT_UNAVAILABLE"})

class AdminNewClientRequest(BaseModel):
    name: str
    email: str
    phone: str = ""
    business_name: str = ""
    services: list = []        # keys of client_auth.GRANTABLE_SERVICES
    builds_site: bool = False  # grant the new-site build (real InstaWP cost to uallak)
    language: str = "he"
    send_invite: bool = True

@app.post("/api/admin/clients/new")
def admin_new_client(req: AdminNewClientRequest, request: Request):
    """The admin dashboard's "New Client": a billing-exempt account (uallak's
    own business, internal use) that skips PayPal entirely. Same dashboard and
    connection cards as a paying client - the services grant is what the
    package-derived checks read (see core/client_auth)."""
    _require_admin(request)
    try:
        result = client_auth.create_exempt_client(req.name, req.email, req.phone, req.business_name,
                                                  req.services, req.builds_site, req.language)
    except client_auth.AuthNotReadyError as e:
        raise _auth_not_ready(e)
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result)
    client = result["client"]
    invited = False
    if req.send_invite:
        try:
            from core.email_service import send_account_setup_invite
            send_account_setup_invite(client["email"], client.get("name", ""), client["id"], "welcome")
            invited = True
        except Exception as e:
            print(f"[admin_new_client] invite email failed for client {client['id']}: {e}")
    return {"success": True, "data": {"client": public_client(client), "invite_sent": invited}}

@app.post("/api/admin/clients/{client_id}/password-reset")
def admin_client_password_reset(client_id: int, request: Request):
    """Clears the client's password, signs them out everywhere, and emails a
    link to set a new one. The admin never sets or sees a password."""
    _require_admin(request)
    client = get_client(client_id)
    if not client or client.get("status") in OFFBOARDED_STATUSES or not client.get("email"):
        raise HTTPException(status_code=404, detail="Client not found or has no email")
    try:
        client_auth.admin_reset_password(client)
    except client_auth.AuthNotReadyError as e:
        raise _auth_not_ready(e)
    from core.email_service import send_account_setup_invite
    try:
        send_account_setup_invite(client["email"], client.get("name", ""), client_id, "reset")
    except Exception as e:
        # The reset itself landed; say plainly that the email didn't
        return {"success": True, "data": {"email_sent": False, "error": str(e)}}
    return {"success": True, "data": {"email_sent": True}}

# ─── CRM: leads with source attribution ──────────────────────────────────────
# Browser-session guarded like the rest of the admin dashboard, NOT X-Admin-Key
# (the existing /api/leads above is the server-to-server one and stays as is).

@app.get("/api/admin/leads")
def admin_leads(request: Request):
    _require_admin(request)
    return {"success": True, "data": lead_tracking.list_leads()}

@app.get("/api/admin/platform-usage")
def admin_platform_usage(request: Request):
    # uallak's OWN infrastructure-cost signal. Deliberately not joined to the
    # client lead-volume tiers anywhere: a client's tier is value-based and
    # says nothing about what they cost us to serve.
    _require_admin(request)
    from core.lead_volume import get_platform_usage
    return {"success": True, "data": get_platform_usage()}

# ─── uallak's own operating costs (admin-only, never client-facing) ──────────

@app.get("/api/admin/operating-costs")
def admin_operating_costs(request: Request):
    # "What am I actually spending" across every service uallak pays for.
    # Distinct from /api/admin/overview (revenue+margin) and from budget_agent
    # (one client's picture). Plain `def`: blocking Supabase reads.
    _require_admin(request)
    from core.operating_costs import get_operating_costs
    data = get_operating_costs()
    # The Supabase footprint is the usage half of the Supabase cost line — shown
    # beside it rather than duplicated, and never fatal if it can't be measured.
    try:
        from core.lead_volume import get_platform_usage
        data["supabase_usage"] = get_platform_usage()
    except Exception as e:
        print(f"[api] supabase usage unavailable for cost view: {e}")
        data["supabase_usage"] = None
    return {"success": True, "data": data}

class OperatingCostRequest(BaseModel):
    amount_ils: float
    cadence: str = "monthly"   # 'monthly' | 'yearly'
    note: str = ""

@app.put("/api/admin/operating-costs/{service_key}")
def admin_set_operating_cost(request: Request, service_key: str, req: OperatingCostRequest):
    # Only MANUALLY maintained services are writable — a measured or derived
    # number must never be overridable by hand, or the label stops meaning
    # anything (operating_costs.set_manual_cost enforces this).
    _require_admin(request)
    from core.operating_costs import set_manual_cost
    result = set_manual_cost(service_key, req.amount_ils, req.cadence, req.note)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result)
    return {"success": True, "data": result}

@app.get("/api/admin/leads/{lead_id}")
def admin_lead_detail(request: Request, lead_id: str):
    _require_admin(request)
    data = lead_tracking.get_lead(lead_id)
    if not data:
        raise HTTPException(status_code=404, detail="lead not found")
    return {"success": True, "data": data}

class AdminLeadStatusRequest(BaseModel):
    status: str   # in_progress | converted | declined ('declined' is the manual one)

@app.post("/api/admin/leads/{lead_id}/status")
def admin_lead_status(request: Request, lead_id: str, req: AdminLeadStatusRequest):
    _require_admin(request)
    result = lead_tracking.set_status(lead_id, req.status)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "bad status"))
    return {"success": True, "data": result}

@app.get("/api/admin/clients/{client_id}")
def admin_client_detail(client_id: int, request: Request):
    _require_admin(request)
    data = admin_service.get_client_admin(client_id)
    if not data:
        raise HTTPException(status_code=404, detail="Client not found")
    return {"success": True, "data": data}

class AdminMessageRequest(BaseModel):
    text: str

@app.post("/api/admin/clients/{client_id}/message")
def admin_send_chat_message(client_id: int, req: AdminMessageRequest, request: Request):
    _require_admin(request)
    # Lands in the client's dashboard support-chat history (same channel the
    # support agent writes to), so they see it next time they open the chat
    entry = log_communication(client_id, "outbound", "dashboard_chat", req.text)
    return {"success": True, "data": entry}

@app.get("/api/admin/clients/{client_id}/budget")
def admin_client_budget(client_id: int, request: Request):
    """The full per-client financial picture (budget_agent) - our own
    numbers reused from admin_service, real ad spend, and honestly-labeled
    client-paid external tool visibility. Heavier than /api/admin/clients/{id}
    (live ad-platform calls, cached client-side in the agents), fetched as
    its own lazy-loaded drawer section."""
    _require_admin(request)
    from agents.budget_agent import get_financial_picture
    data = get_financial_picture(client_id)
    if not data.get("available"):
        raise HTTPException(status_code=404, detail="No financial picture available for this client")
    return {"success": True, "data": data}

@app.get("/api/admin/clients/{client_id}/budget/trend")
def admin_client_budget_trend(client_id: int, request: Request):
    _require_admin(request)
    from agents.budget_agent import get_trend
    return {"success": True, "data": get_trend(client_id)}

@app.post("/api/admin/clients/{client_id}/budget/narrative")
def admin_client_budget_narrative(client_id: int, request: Request):
    """On-demand only (not run automatically in the weekly scan) - burns one
    Claude call, so the drawer's narrative button fetches it lazily rather
    than every drawer-open generating one."""
    _require_admin(request)
    from agents.budget_agent import generate_narrative
    return {"success": True, "data": generate_narrative(client_id)}

# ─── Control Room (core/control_room) — admin browser session only ──────────
# Its own area of the admin dashboard. The admin<->agent chat lives in the
# admin_agent_chat table, never in anything the client's chat or data export
# reads; nothing here has a WhatsApp path.

@app.get("/api/admin/control-room")
def admin_control_room_list(request: Request):
    _require_admin(request)
    from core import control_room
    return {"success": True, "data": control_room.list_overview()}

@app.get("/api/admin/control-room/{client_id}")
def admin_control_room_client(client_id: int, request: Request):
    _require_admin(request)
    from core import control_room
    data = control_room.client_view(client_id)
    if not data:
        raise HTTPException(status_code=404, detail="Client not found")
    return {"success": True, "data": data}

@app.get("/api/admin/control-room/{client_id}/budget")
def admin_control_room_budget(client_id: int, request: Request):
    # Lazy: live ad-platform reads (cached in the ad agents), kept off the
    # main view so the page paints before Google/Meta answer
    _require_admin(request)
    from core import control_room
    return {"success": True, "data": control_room.budget_split(client_id)}

@app.get("/api/admin/control-room/{client_id}/costs")
def admin_control_room_costs(client_id: int, request: Request):
    # What this client has cost us, by category and by action (core/
    # cost_tracker.client_cost_view). Its own lazy endpoint: cost figures stay
    # out of client_view, which grounds the Control Room chat — no cost
    # information in any chat prompt.
    _require_admin(request)
    from core.cost_tracker import client_cost_view
    if not get_client(client_id):
        raise HTTPException(status_code=404, detail="Client not found")
    return {"success": True, "data": client_cost_view(client_id)}

@app.get("/api/admin/control-room/{client_id}/article-drafts")
def admin_control_room_article_drafts(client_id: int, request: Request, post_id: int = 0):
    # Review surface for articles the system wrote: the recorded pipeline
    # findings (language, translation plugin, links, placeholders, image
    # ideas) per draft, and with post_id the draft's FULL current text read
    # live from WordPress. Only post ids WE created for this client.
    _require_admin(request)
    rows = (db.table("client_activity").select("details,result,created_at")
            .eq("client_id", client_id).eq("agent_name", "seo_agent")
            .eq("action_type", "seo_article_generated")
            .order("created_at", desc=True).limit(20).execute().data or [])
    drafts = [{"post_id": (r.get("result") or {}).get("post_id"), "created_at": r.get("created_at"),
               **(r.get("details") or {})} for r in rows]
    if not post_id:
        return {"success": True, "data": drafts}
    if post_id not in {d["post_id"] for d in drafts}:
        raise HTTPException(status_code=404, detail="not a draft this system created for this client")
    from agents.website_agent import _creds, _get_connection
    from core import wordpress_service as wp
    site_url, username, app_password = _creds(_get_connection(client_id))
    try:
        post = wp.get_content(site_url, username, app_password, "post", post_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"WordPress read failed: {e}")
    return {"success": True, "data": {
        "post_id": post_id, "status": post.get("status"), "slug": post.get("slug"),
        "title": (post.get("title") or {}).get("raw", ""),
        "excerpt": (post.get("excerpt") or {}).get("raw", ""),
        "content_html": (post.get("content") or {}).get("raw", ""),
        "pipeline": next(d for d in drafts if d["post_id"] == post_id)}}

class ClientSettingsUpdate(BaseModel):
    changes: dict = {}
    reset: list = []

@app.get("/api/admin/control-room/{client_id}/settings")
def admin_control_room_settings(client_id: int, request: Request):
    # Per-client limits (core/client_settings): article cap, package check,
    # repeat-topic block, auto articles, revision cap
    _require_admin(request)
    from core import client_settings
    if not get_client(client_id):
        raise HTTPException(status_code=404, detail="Client not found")
    return {"success": True, "data": client_settings.view(client_id)}

@app.post("/api/admin/control-room/{client_id}/settings")
def admin_control_room_settings_update(client_id: int, req: ClientSettingsUpdate, request: Request):
    _require_admin(request)
    from core import client_settings
    if not get_client(client_id):
        raise HTTPException(status_code=404, detail="Client not found")
    try:
        return {"success": True, "data": client_settings.update(client_id, req.changes, req.reset)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/admin/control-room/{client_id}/chat")
def admin_control_room_chat_history(client_id: int, request: Request, persona: str = "general"):
    _require_admin(request)
    from core import control_room
    return {"success": True, "data": control_room.chat_history(client_id, persona)}

@app.post("/api/admin/control-room/{client_id}/drive-self-test")
def admin_control_room_drive_self_test(client_id: int, request: Request):
    # Proves the client's media folder is USABLE (folder, upload, list,
    # delete, link) - the calls generation and delivery make - and names the
    # exact fix when a step fails. Plain `def`: Drive HTTP calls.
    _require_admin(request)
    if not get_client(client_id):
        raise HTTPException(status_code=404, detail="Client not found")
    from agents.media_agent import drive_self_test
    try:
        return {"success": True, "data": drive_self_test(client_id)}
    except Exception as e:
        # Each step already catches its own Drive error; this is anything
        # outside them (config/import/DB). Reported as a failed step so the
        # admin sees the reason in place, never a bare 500.
        import traceback
        traceback.print_exc()
        return {"success": True, "data": {"ok": False, "folder_link": "", "steps": [
            {"step": "self-test setup", "ok": False, "reason": "other",
             "fix": "Unexpected error before the Drive calls ran — see the Cloud Run log.",
             "error": f"{type(e).__name__}: {str(e)[:300]}"}]}}

class AdminConfigureConversionRequest(BaseModel):
    ga4_measurement_id: str = ""  # optional — falls back to the id detected on the site

@app.post("/api/admin/control-room/{client_id}/configure-conversion")
def admin_control_room_configure_conversion(client_id: int, req: AdminConfigureConversionRequest,
                                            request: Request):
    # Browser-session twin of /api/website/configure-conversion, which is
    # X-Admin-Key only - same function, same refusals. Plain `def`: multiple
    # GTM API calls.
    _require_admin(request)
    if not get_client(client_id):
        raise HTTPException(status_code=404, detail="Client not found")
    from agents.website_agent import configure_lead_conversion
    result = configure_lead_conversion(client_id, req.ga4_measurement_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

# ─── Google verification demo: one-click writes on an INTERNAL test client ───
# The fallback buttons. core/verification_demo holds the implementation and
# the internal-client refusal; the Control Room chat reaches the same two
# functions through core/agent_actions.

@app.post("/api/admin/control-room/{client_id}/demo/google-ads-campaign")
def admin_demo_google_ads_campaign(client_id: int, request: Request):
    # Plain `def`: one blocking googleAds:mutate
    _require_admin(request)
    from core import verification_demo
    try:
        return {"success": True, "data": verification_demo.create_test_campaign(client_id)}
    except verification_demo.DemoRefused as e:
        raise HTTPException(status_code=e.status, detail=e.detail)

@app.post("/api/admin/control-room/{client_id}/demo/youtube-upload")
def admin_demo_youtube_upload(client_id: int, request: Request):
    # Plain `def`: Drive download + resumable YouTube upload
    _require_admin(request)
    from core import verification_demo
    try:
        return {"success": True, "data": verification_demo.upload_test_video(client_id)}
    except verification_demo.DemoRefused as e:
        raise HTTPException(status_code=e.status, detail=e.detail)

# ─── Agent actions: the human click that runs what an agent proposed ────────
# core/agent_actions. The proposal (action + params) was stored server-side
# when the agent proposed it; the browser sends only its id and the decision,
# so there are no params here to tamper with. Who may decide is checked from
# the row (approver) against the caller (client session / admin session).

class ActionDecisionRequest(BaseModel):
    decision: str   # approve | cancel
    # Admin "enter as client" only: the explicit "this is a staff action" mark
    staff_action: bool = False

@app.get("/api/client/actions")
def client_open_actions(request: Request, persona: str = "general"):
    # The window re-shows its open cards after a reload (history has the text)
    client_id = _require_session(request)
    from core import agent_actions
    return {"success": True, "data": agent_actions.open_cards(client_id, actor="client",
                                                              persona=(persona or "general").strip())}

@app.get("/api/client/actions/{proposal_id}")
def client_action_status(proposal_id: int, request: Request):
    # A progress card polls this until the slow action finished; another
    # client's proposal is answered exactly like a missing one
    client_id = _require_session(request)
    from core import agent_actions
    view = agent_actions.status_card(proposal_id, client_id)
    if not view:
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    return {"success": True, "data": view}

@app.post("/api/client/actions/{proposal_id}/decide")
def client_decide_action(proposal_id: int, req: ActionDecisionRequest, request: Request,
                         background_tasks: BackgroundTasks):
    # Plain `def`: an approved action may make blocking calls
    client_id = _require_session(request)
    from core import agent_actions
    staff = _admin_view_client(request) is not None
    if staff:
        # Staff in the client's dashboard: only an explicitly marked staff
        # action, audited BEFORE it runs (no audit → nothing runs)
        if not req.staff_action:
            raise HTTPException(status_code=403, detail={"code": "ERR_STAFF_ACTION_REQUIRED"})
        try:
            _admin_view_audit(request, "staff_action_requested", client_id,
                              f"proposal {proposal_id} {req.decision}")
        except admin_view.AuditUnavailable:
            raise HTTPException(status_code=503, detail={"code": "ERR_AUDIT_UNAVAILABLE"})
    result = agent_actions.decide(proposal_id, actor="client", actor_client_id=client_id,
                                  decision=req.decision, background_tasks=background_tasks, staff=staff)
    if staff:
        try:
            _admin_view_audit(request, "staff_action_result", client_id,
                              f"proposal {proposal_id} {req.decision}: "
                              f"{result.get('status') or result.get('code')}")
        except admin_view.AuditUnavailable:
            alert("admin_view", [f"client {client_id}: staff action on proposal {proposal_id} ran "
                                 f"but its result was not audited"])
    if not result.get("success"):
        raise HTTPException(status_code=result.get("http", 400), detail={"code": result.get("code")})
    return {"success": True, "data": result}

@app.get("/api/admin/control-room/{client_id}/actions")
def admin_open_actions(client_id: int, request: Request):
    _require_admin(request)
    from core import agent_actions
    return {"success": True, "data": agent_actions.open_cards(client_id, actor="admin")}

@app.post("/api/admin/control-room/{client_id}/actions/{proposal_id}/decide")
def admin_decide_action(client_id: int, proposal_id: int, req: ActionDecisionRequest,
                        request: Request, background_tasks: BackgroundTasks):
    _require_admin(request)
    from core import agent_actions
    result = agent_actions.decide(proposal_id, actor="admin", actor_client_id=client_id,
                                  decision=req.decision, background_tasks=background_tasks)
    if not result.get("success"):
        raise HTTPException(status_code=result.get("http", 400), detail={"code": result.get("code")})
    return {"success": True, "data": result}

@app.post("/api/admin/control-room/{client_id}/capability-check")
def admin_capability_check(client_id: int, request: Request):
    # Plain `def`: ~10 blocking chat turns. DRY RUN: nothing is stored, no
    # card is created, no alert is raised — it shows what the client's agents
    # would answer right now and flags any "can't see / go check" reply.
    _require_admin(request)
    if not get_client(client_id):
        raise HTTPException(status_code=404, detail="Client not found")
    from core import agent_actions
    return {"success": True, "data": agent_actions.capability_check(client_id)}

# ─── PHP2Shell exposure (core/php_exposure, docs/INCIDENT-PHP2SHELL.md) ─────
# Admin only. The scan is ONE ordinary anonymous HEAD/GET of each known
# WordPress homepage, reading response headers — never a probe or payload.

@app.get("/api/admin/php-exposure")
def admin_php_exposure(request: Request):
    _require_admin(request)
    from core import php_exposure
    return {"success": True, "data": php_exposure.overview()}

@app.post("/api/admin/php-exposure/scan")
def admin_php_exposure_scan(request: Request, client_id: int = 0):
    # Plain `def`: one HTTP request (two at most) per known site
    _require_admin(request)
    from core import php_exposure
    return {"success": True, "data": php_exposure.run_scan(client_id or None)}

@app.get("/api/website/php-exposure-scan", dependencies=_admin_only)
def website_php_exposure_scan():
    # Scheduler twin of the admin button (X-Admin-Key), like /api/website/scan
    from core import php_exposure
    return {"success": True, "data": php_exposure.run_scan()}

class AdminProposeActionRequest(BaseModel):
    name: str

@app.post("/api/admin/control-room/{client_id}/actions/propose")
def admin_propose_action(client_id: int, req: AdminProposeActionRequest, request: Request):
    # A Control Room button that PROPOSES a no-param admin action (e.g.
    # rotate_wordpress_password from the PHP2Shell panel) — the same propose()
    # the admin chat uses, every guard included; it still runs only on the
    # card's approve click (decide).
    _require_admin(request)
    if not get_client(client_id):
        raise HTTPException(status_code=404, detail="Client not found")
    from core import agent_actions, client_state
    result = agent_actions.propose(client_id, "admin_chat", "website", {"name": req.name, "params": {}},
                                   state=client_state.snapshot(client_id))
    if not result.get("card"):
        raise HTTPException(status_code=400, detail={"code": result.get("error") or "not_proposed"})
    return {"success": True, "data": result}

@app.post("/api/admin/control-room/feedback/{feedback_id}/reviewed")
def admin_control_room_feedback_reviewed(feedback_id: int, request: Request):
    # Browser-session twin of /api/admin/feedback/{id}/reviewed, which is
    # X-Admin-Key only - so until now no screen could mark feedback as read
    _require_admin(request)
    from agents.engagement_agent import mark_feedback_reviewed
    return {"success": True, "data": mark_feedback_reviewed(feedback_id)}

class AdminAgentChatRequest(BaseModel):
    persona: str = "general"
    message: str

@app.post("/api/admin/control-room/{client_id}/chat")
def admin_control_room_chat(client_id: int, req: AdminAgentChatRequest, request: Request):
    # Plain `def`: grounding makes several blocking reads, then one LLM call
    _require_admin(request)
    from core import control_room
    if not get_client(client_id):
        raise HTTPException(status_code=404, detail="Client not found")
    result = control_room.ask_agent(client_id, req.persona, req.message)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail={"code": result.get("code")})
    return {"success": True, "data": result}

@app.get("/api/budget/scan", dependencies=_admin_only)
def budget_scan():
    """Weekly cron (X-Admin-Key) - snapshots every active client's financial
    picture for trend history and raises deduped deviation alerts. See the
    budget skill for the scheduler command."""
    from agents.budget_agent import run_weekly_scan
    return {"success": True, "data": run_weekly_scan()}

@app.get("/api/admin/clients/{client_id}/seo/pending-strategy")
def admin_seo_pending_strategy(client_id: int, request: Request):
    """Admin dashboard drawer's SEO approval surface (session cookie, not
    X-Admin-Key — this is the browser-facing counterpart to the /api/seo/*
    server-to-server endpoints)."""
    _require_admin(request)
    from agents.seo_agent import get_pending_strategy
    return {"success": True, "data": get_pending_strategy(client_id)}

@app.post("/api/admin/clients/{client_id}/seo/approve-strategy")
def admin_seo_approve_strategy(client_id: int, request: Request):
    _require_admin(request)
    from agents.seo_agent import approve_strategy
    result = approve_strategy(client_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

# ─── Feature announcements — ADMIN authoring ─────────────────────────────────
# Johnny decides when something is ready to tell clients about; nothing here is
# created by a deploy. Targeting and persona come from core/feature_catalog.py,
# so this surface only carries the note and the live/draft switch.

@app.get("/api/admin/feature-announcements")
def admin_list_feature_announcements(request: Request):
    _require_admin(request)
    from core import feature_announcements, feature_catalog
    return {"success": True, "data": {
        "announcements": feature_announcements.list_announcements(),
        # The dropdown of what can be announced — the catalogue itself, so a new
        # feature becomes announceable with no change to this endpoint.
        "features": [{"key": f["key"], "name_he": f["name_he"], "persona": f["persona"],
                      "audience": "כל הלקוחות" if f["relevance"].get("always")
                                  else "לקוחות רלוונטיים בלבד"}
                     for f in feature_catalog.FEATURES],
        "table_ready": feature_announcements.table_ready(),
        "migration": "migrations/2026-08-08-feature-announcements.sql",
    }}

class FeatureAnnouncementCreate(BaseModel):
    feature_key: str
    title: str = ""
    note: str
    status: str = "draft"

@app.post("/api/admin/feature-announcements")
def admin_create_feature_announcement(req: FeatureAnnouncementCreate, request: Request):
    _require_admin(request)
    from core import feature_announcements
    result = feature_announcements.create_announcement(
        req.feature_key, req.title, req.note, req.status)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return result

class FeatureAnnouncementStatus(BaseModel):
    status: str   # draft | live | archived

@app.patch("/api/admin/feature-announcements/{announcement_id}")
def admin_set_feature_announcement_status(announcement_id: int,
                                          req: FeatureAnnouncementStatus, request: Request):
    _require_admin(request)
    from core import feature_announcements
    result = feature_announcements.set_status(announcement_id, req.status)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return result

@app.delete("/api/admin/feature-announcements/{announcement_id}")
def admin_delete_feature_announcement(announcement_id: int, request: Request):
    _require_admin(request)
    from core import feature_announcements
    result = feature_announcements.delete_announcement(announcement_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return result

# ─── Marketing automation (ManyChat / Make) — ADMIN ONLY ─────────────────────
# The credentials the CLIENT created and handed to Johnny, so he can sign in and
# build their automation. Deliberately admin-session only and deliberately NOT
# mirrored on any client endpoint: unlike every other integration there is no
# client-facing connect flow here, and the client must never be able to read
# back a stored password. See core/automation_service.py's module docstring.

@app.get("/api/admin/clients/{client_id}/automation")
def admin_automation_credentials(client_id: int, request: Request):
    _require_admin(request)
    from core import automation_service
    return {"success": True, "data": automation_service.get_credentials(client_id)}

class AutomationTokenRequest(BaseModel):
    vendor: str      # manychat | make (automation_service.VENDORS)
    api_token: str   # encrypted per client; passwords are no longer accepted

@app.get("/api/admin/costs/by-client-month")
def admin_costs_by_client_month(request: Request, months: int = 6):
    # OUR cost per client per month, by metering category (core/cost_tracker)
    _require_admin(request)
    from core.cost_tracker import costs_by_client_month
    return {"success": True, "data": costs_by_client_month(max(1, min(int(months), 24)))}

@app.get("/api/admin/automation/legacy-logins")
def admin_automation_legacy_logins(request: Request):
    # Clients still on a stored username/password (kept, never shown) — to be
    # moved to an API token
    _require_admin(request)
    from core import automation_service
    return {"success": True, "data": automation_service.legacy_logins()}

@app.post("/api/admin/clients/{client_id}/automation")
def admin_save_automation_credentials(client_id: int, req: AutomationTokenRequest, request: Request):
    _require_admin(request)
    from core import automation_service
    result = automation_service.save_token(client_id, req.vendor, req.api_token)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

@app.delete("/api/admin/clients/{client_id}/automation/{vendor}")
def admin_delete_automation_credentials(client_id: int, vendor: str, request: Request):
    _require_admin(request)
    from core import automation_service
    result = automation_service.remove_credentials(client_id, vendor)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

@app.get("/api/pricing/monitor-scan", dependencies=_admin_only)
def pricing_monitor_scan():
    """Bi-monthly cron (X-Admin-Key) - re-checks every vendor in
    core/third_party_pricing.py against its live pricing page and alerts on
    anything that isn't a confident match. See the price-monitoring skill
    for the scheduler command."""
    from agents.price_monitor_agent import run_price_check
    return {"success": True, "data": run_price_check()}

@app.get("/api/admin/alerts")
def admin_alerts(request: Request, status: str = None):
    _require_admin(request)
    return {"success": True, "data": admin_service.list_alerts(status)}

class BulkResolveRequest(BaseModel):
    ids: list[int]

# Takes the ids the admin was SHOWN, never a scope like "all open for client
# X": a scope would also close alerts that arrived after the screen rendered.
@app.post("/api/admin/alerts/resolve-bulk")
def admin_resolve_alerts_bulk(req: BulkResolveRequest, request: Request):
    _require_admin(request)
    if len(req.ids) > admin_service.BULK_RESOLVE_MAX:
        raise HTTPException(status_code=400, detail={"code": "ERR_TOO_MANY_IDS",
                                                     "max": admin_service.BULK_RESOLVE_MAX})
    return {"success": True, "data": admin_service.resolve_alerts(req.ids)}

@app.post("/api/admin/alerts/{alert_id}/resolve")
def admin_resolve_alert(alert_id: int, request: Request):
    _require_admin(request)
    return {"success": True, "data": admin_service.resolve_alert(alert_id)}

@app.get("/api/admin/reports")
def admin_reports(request: Request):
    _require_admin(request)
    return {"success": True, "data": admin_service.list_weekly_reports()}

@app.get("/api/admin/settings")
def admin_get_settings(request: Request):
    _require_admin(request)
    return {"success": True, "data": admin_service.get_settings()}

@app.put("/api/admin/settings")
def admin_update_settings(request: Request, changes: dict):
    _require_admin(request)
    return {"success": True, "data": admin_service.update_settings(changes)}

@app.get("/api/admin/pricing")
def admin_pricing_reference(request: Request):
    """Read-only unified pricing reference (setup fees, platform management,
    organic SEO tiers, website hosting, avatar tier, and client-direct
    external cost references) - pulled live from PRICING/budget_agent,
    never a second copy of any number. See admin_service.get_pricing_reference."""
    _require_admin(request)
    return {"success": True, "data": admin_service.get_pricing_reference()}

@app.get("/health")
async def health():
    return {"status": "ok", "service": "uallak-super-system"}

class DynamicQRequest(BaseModel):
    intro: str
    answers: dict
    lead_id: str = ""   # cost attribution (core/cost_tracker)

@app.post("/api/dynamic-questions")
def dynamic_questions(req: DynamicQRequest):
    try:
        from agents.onboarding_agent import get_dynamic_questions, get_api_key
        api_key = os.environ.get("ANTHROPIC_API_KEY", "") or get_api_key()
        with _sales_cost_owner(req.lead_id):
            questions = get_dynamic_questions(req.intro, req.answers, api_key)
        return {"questions": questions}
    except Exception as e:
        return {"questions": []}

class FilterRequest(BaseModel):
    intro: str
    answers: dict
    lead_id: str = ""   # cost attribution (core/cost_tracker)

@app.post("/api/filter-questions")
def filter_questions_endpoint(req: FilterRequest):
    try:
        from agents.question_filter import get_skip_ids
        with _sales_cost_owner(req.lead_id):
            return {"skip_ids": get_skip_ids(req.intro)}
    except Exception as e:
        import traceback
        print(f"Filter error: {e}")
        traceback.print_exc()
        return {"skip_ids": []}

# TikTok domain verification (signature-file method) — must resolve at the
# site root exactly, so it's registered as a route BEFORE the root catch-all
# mount below (routes beat mounts, but keep the visual ordering honest too).
# Needed for TikTok developer-app URL-ownership verification; harmless to
# keep serving permanently.
@app.get("/tiktokrJGb1d7R9INmQdVAEt5me4e6LEOQtAXk.txt", include_in_schema=False)
async def tiktok_domain_verification():
    return PlainTextResponse("tiktok-developers-site-verification=rJGb1d7R9INmQdVAEt5me4e6LEOQtAXk")

# ─── Landing pages: the PUBLIC serving routes ────────────────────────────────
# ONE shared route serves every client's landing pages — no per-client or
# per-page hosting project, which is the whole reason this scales past the
# ~30-project ceiling that shaped the hosting decision. Registered here, before
# the root catch-all mount, for the same reason as the TikTok file above.

@app.get("/_uallak-verify", include_in_schema=False)
def landing_domain_verify_marker():
    # Answers on EVERY hostname that reaches this app, which is exactly what
    # makes it a proof of routing: core/landing_domains.verify_domain fetches
    # this over HTTPS on the client's own hostname. It carries no secret and
    # identifies no client — it only proves "this hostname reaches uallak".
    from core.landing_domains import VERIFY_BODY
    return PlainTextResponse(VERIFY_BODY)

@app.get("/lp/{client_segment}/{page_slug}", include_in_schema=False)
def serve_landing_page(client_segment: str, page_slug: str, request: Request):
    # PUBLIC and unauthenticated by necessity — this is the page a real visitor
    # loads. Plain `def`: Supabase reads are blocking.
    from core import landing_pages
    from core.session import create_lead_capture_token

    # The client id is the trailing numeric segment of the slug (see
    # landing_pages.client_slug) — no lookup table, and a renamed business
    # keeps working.
    match = re.search(r"(\d+)$", client_segment or "")
    if not match:
        raise HTTPException(status_code=404, detail="not found")
    client_id = int(match.group(1))

    try:
        page = landing_pages.get_by_slug(client_id, page_slug)
    except Exception as e:
        print(f"[landing] lookup failed for {client_segment}/{page_slug}: {e}")
        raise HTTPException(status_code=503, detail="temporarily unavailable")
    # A draft is not publicly reachable — same 404 as a page that never
    # existed, so an unpublished slug can't be probed for.
    if not page or page.get("status") != "published":
        raise HTTPException(status_code=404, detail="not found")

    base = os.environ.get("PUBLIC_APP_URL", "https://app.uallak.com").rstrip("/")
    capture_url = f"{base}/api/leads/capture/{create_lead_capture_token(client_id)}"
    # The redirect target is THIS request's own URL, so it always matches the
    # host the visitor actually used — the shared URL or the client's own
    # domain. That is what satisfies the capture endpoint's same-host-https
    # open-redirect guard in both states, without this route having to know
    # which one it is serving.
    public_url = str(request.url.replace(query="", scheme="https"))
    # Attribution rides in as hidden fields rendered from THIS request's query
    # string — no JS on the page, and it still works with JS disabled.
    page_html = landing_pages.render_page(
        page, capture_url, public_url,
        sent=request.query_params.get("sent") == "1",
        query_params=request.query_params,
        path=request.url.path,
        referrer=request.headers.get("referer", ""))
    return HTMLResponse(page_html)

# Must be last — catch-all for the landing page
app.mount("/", StaticFiles(directory=os.path.join(BASE_DIR, "dashboard", "landing"), html=True), name="landing")
