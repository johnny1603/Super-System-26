import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
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
)
from core import google_ads_service
from core import meta_service
from core import admin_service
from core import drive_service

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

app.mount("/chat", StaticFiles(directory=os.path.join(BASE_DIR, "dashboard", "onboarding"), html=True), name="chat")
app.mount("/terms", StaticFiles(directory=os.path.join(BASE_DIR, "dashboard", "terms"), html=True), name="terms")
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

for _page in ("/chat", "/terms", "/dashboard", "/login", "/admin", "/profile"):
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

@app.post("/api/onboarding")
def onboarding(req: OnboardingRequest):
    try:
        result = run_full_onboarding(req.answers)
        proposal = result["proposal"]
        review = result.get("review", {})
        if is_agent_active("master_agent") and not review.get("approved", True):
            alert("proposal", review.get("issues", []))

        # שמירה ב-DB — נשמור את המסלול הזול ביותר כבסיס להשוואה בטבלה
        packages = proposal.get("packages", [])
        cheapest = min(packages, key=lambda pkg: pkg.get("monthly_management_total", 0)) if packages else {}
        db.table("leads").insert({
            "created_at": datetime.now().isoformat(),
            "client_email": req.client_email,
            "client_name": req.client_name,
            "answers": req.answers,
            "proposal": proposal,
            "approved": bool(proposal.get("approved")),
            "setup_fee": cheapest.get("setup_fee_total", 0),
            "monthly_fee": cheapest.get("monthly_management_total", 0),
        }).execute()

        # שליחת מיילים
        send_admin_alert(req.answers, proposal)
        if req.client_email:
            send_client_report(req.client_email, req.client_name, proposal)

        return {"success": True, "data": result}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/leads", dependencies=_admin_only)
async def get_leads():
    result = db.table("leads").select("*").order("created_at", desc=True).execute()
    return {"leads": result.data}

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

@app.post("/api/checkout")
async def checkout(req: CheckoutRequest):
    try:
        client = create_client(req.client_name, req.client_email, req.client_phone, req.package_name,
                                req.client_address, req.business_name, req.business_tax_id)
        client_id = client["id"]
        update_client_status(client_id, "pending_payment")

        # The lead row was created at proposal time, before the client gave their name/email —
        # backfill the newest contactless lead now that we finally know who they are. (Newest-first
        # match is good enough at current volume; a chat-session id would make this exact.)
        try:
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
                }).eq("id", recent_lead.data[0]["id"]).execute()
                # Also stamp client_id on the lead, for budget_agent's exact-match forecast
                # join — leads.client_id doesn't exist in Supabase yet (nullable bigint,
                # add it whenever convenient), so this silently no-ops until that column is
                # added; budget_agent falls back to the email match above until then.
                try:
                    db.table("leads").update({"client_id": client_id}).eq(
                        "id", recent_lead.data[0]["id"]).execute()
                except Exception:
                    pass
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
        raise HTTPException(status_code=500, detail=str(e))

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
                if event_type in ACTIVATION_EVENTS:
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

    return {"success": True}

class ObjectionRequest(BaseModel):
    text: str
    answers: dict = {}
    empathy_final: dict = {}
    packages: list = []

@app.post("/api/handle-objection")
def handle_objection_endpoint(req: ObjectionRequest):
    try:
        from agents.onboarding_agent import handle_objection, get_api_key
        reply = handle_objection(req.text, req.packages, req.answers, req.empathy_final, get_api_key())
        return {"success": True, "reply": reply}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"success": False, "reply": "מצטערים, הייתה תקלה קטנה — אפשר לנסות שוב? 🙏"}

class ReactionRequest(BaseModel):
    question_text: str = ""
    answer_text: str = ""

@app.post("/api/reaction")
def reaction_endpoint(req: ReactionRequest):
    try:
        from agents.onboarding_agent import get_reaction, get_api_key
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
        return {"success": True, "data": client}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/clients", dependencies=_admin_only)
async def api_list_clients(status: str = None):
    try:
        return {"success": True, "data": list_clients(status)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/clients/{client_id}", dependencies=_admin_only)
async def api_get_client(client_id: int):
    client = get_client(client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return {"success": True, "data": client}

# ─── Client login (email + one-time code, no passwords) ──────────────────────

class LoginRequestCodeRequest(BaseModel):
    email: str

# Offboarded clients are hard-locked out of the dashboard (business decision,
# 2026-07): their archive lives in Google Drive, not behind a login. Their
# data export was attached to the closure/transfer confirmation email.
OFFBOARDED_STATUSES = ("closed", "transferred")

@app.post("/api/login/request-code")
async def login_request_code(req: LoginRequestCodeRequest):
    try:
        client = get_client_by_email(req.email.strip())
        # Same generic response for an offboarded account as for an unknown
        # email - no code is sent, and nothing is revealed
        if client and client.get("status") in OFFBOARDED_STATUSES:
            client = None
        if client:
            code = f"{secrets.randbelow(1_000_000):06d}"
            expires_at = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
            create_login_code(client["id"], code, expires_at)
            send_login_code(client["email"], client.get("name", ""), code)
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
    invalid = HTTPException(status_code=401, detail="קוד שגוי או שפג תוקפו")

    client = get_client_by_email(req.email.strip())
    if not client or client.get("status") in OFFBOARDED_STATUSES:
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

    token = create_session_token(client["id"])
    response.set_cookie(
        key="session",
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=30 * 24 * 60 * 60,
        path="/",
    )
    return {"success": True}

def _require_session(request: Request) -> int:
    client_id = verify_session_token(request.cookies.get("session"))
    if not client_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    # Hard lock: a closed/transferred client is out even with a still-valid
    # session cookie on another device (cookies live 30 days; the status check
    # is what actually ends access everywhere). One extra DB read per request -
    # fine at current volume.
    client = get_client(client_id)
    if not client or client.get("status") in OFFBOARDED_STATUSES:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return client_id

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
        # Never ship stored credentials to the browser - the dashboard only
        # needs platform + status to paint the connection cards
        "connections": [{"platform": a.get("platform"), "status": a.get("status")}
                        for a in accounts],
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

@app.post("/api/client-chat")
def client_chat(req: ClientChatRequest, request: Request):
    client_id = _require_session(request)
    try:
        from agents.support_agent import answer_support_question
        log_communication(client_id, "inbound", "dashboard_chat", req.message)
        result = answer_support_question(client_id, req.message)
        log_communication(client_id, "outbound", "dashboard_chat", result["reply"])
        return {"success": True, "reply": result["reply"], "needs_human_followup": result["needs_human_followup"]}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/client-chat/history")
async def client_chat_history(request: Request, scope: str = "current"):
    # scope=current -> the active thread only (bounded by clients.chat_started_at,
    # set by the 'שיחה חדשה' action); scope=all -> full history for browsing
    client_id = _require_session(request)
    history = list(reversed(get_communications(client_id, limit=200, channel="dashboard_chat")))
    boundary = get_client(client_id).get("chat_started_at")
    current = ([h for h in history if (h.get("created_at") or "") >= boundary]
               if boundary else history)
    return {
        "success": True,
        "history": history if scope == "all" else current,
        "chat_started_at": boundary,
        "has_previous": len(history) > len(current),
    }

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

@app.get("/api/client/upgrade-options")
def client_upgrade_options(request: Request):
    from agents.onboarding_agent import get_upgrade_tiers
    client_id = _require_session(request)
    client = get_client(client_id)
    sub = _client_subscription_info(client_id)
    if not sub["subscription_id"]:
        return {"success": True, "data": {"available": False, "reason": "אין מנוי פעיל לשדרוג"}}
    tiers = [t for t in get_upgrade_tiers() if t["monthly_fee"] > sub["monthly_fee"]]
    return {"success": True, "data": {
        "available": bool(tiers),
        "current": {"package": client.get("package", ""), "monthly_fee": sub["monthly_fee"]},
        "tiers": tiers,
    }}

class UpgradeRequest(BaseModel):
    tier_id: str

@app.post("/api/client/upgrade")
def client_upgrade(req: UpgradeRequest, request: Request):
    from agents.onboarding_agent import get_upgrade_tiers
    client_id = _require_session(request)
    sub = _client_subscription_info(client_id)
    if not sub["subscription_id"]:
        raise HTTPException(status_code=400, detail="אין מנוי פעיל לשדרוג")

    tier = next((t for t in get_upgrade_tiers() if t["id"] == req.tier_id), None)
    if not tier or tier["monthly_fee"] <= sub["monthly_fee"]:
        raise HTTPException(status_code=400, detail="חבילה לא זמינה לשדרוג")

    try:
        # The upgraded plan must live under the same PayPal product as the
        # original - recover the product through the live subscription, since
        # checkout never stored the product id
        current_plan_id = get_subscription_status(sub["subscription_id"]).get("plan_id")
        product_id = get_plan(current_plan_id).get("product_id")
        new_plan_id = create_plan(product_id, f"uallak ניהול חודשי — {tier['name']}", tier["monthly_fee"])

        public_url = os.environ.get("PUBLIC_APP_URL", "https://uallak.com")
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
        raise HTTPException(status_code=500, detail="שגיאה בהכנת השדרוג — נסה שוב או פנה אלינו בצ'אט")

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

        tier = next((t for t in get_upgrade_tiers() if t["id"] == tier_id), None)
        if tier:
            update_client_package(client_id, tier["name"])
            # Logged as subscription_created so the fee derivations (dashboard
            # + admin MRR) pick up the new amount - they read the newest row
            log_activity(client_id, "paypal_service", "subscription_created",
                         {"package_name": tier["name"], "monthly_management_total": tier["monthly_fee"],
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
    "meta": ["meta_ads", "meta_page", "meta_instagram"],
    "wordpress": ["wordpress"],
    "higgsfield": ["higgsfield"],
    # HeyGen + ElevenLabs disconnect together (both are the avatar add-on's
    # credentials; like wordpress/higgsfield, the keys live on the client's
    # own accounts and are revocable there - deleting our copy is our half)
    "avatar": ["heygen", "elevenlabs"],
}

def _disconnect_platform(client_id: int, platform: str) -> dict:
    """Revoke our access at the provider (best-effort - the grant may already
    be dead) and DELETE the stored client_accounts rows. Only OUR access is
    removed; the client's ad accounts / Pages / site are never touched."""
    rows = [a for a in get_accounts(client_id) if a.get("platform") in _DISCONNECT_GROUPS[platform]]

    revoked = None
    if platform == "google_ads":
        for row in rows:
            if row.get("access_token"):
                revoked = google_ads_service.revoke_token(row["access_token"])
    elif platform == "meta":
        # Any of the group's tokens traces back to the same user grant; prefer
        # the user token on the meta_ads row, fall back to whatever exists
        token_row = (next((r for r in rows if r.get("platform") == "meta_ads" and r.get("access_token")), None)
                     or next((r for r in rows if r.get("access_token")), None))
        if token_row:
            revoked = meta_service.revoke_permissions(token_row["access_token"])
    # wordpress: nothing to revoke remotely - the Application Password lives in
    # the client's own wp-admin (we tell them they can delete it there too);
    # deleting our stored copy of it IS the disconnect.
    # higgsfield: same as wordpress - the API key lives on the client's own
    # Higgsfield account; they can revoke it themselves at
    # cloud.higgsfield.ai/api-keys, and deleting our stored copy is our half.

    removed = remove_accounts(client_id, _DISCONNECT_GROUPS[platform])

    # Drop stale in-memory caches so a reconnect doesn't serve the old account's data
    from agents import google_ads_agent, meta_ads_agent, website_agent
    google_ads_agent._perf_cache.pop(client_id, None)
    meta_ads_agent._perf_cache.pop(client_id, None)
    website_agent._overview_cache.pop(client_id, None)

    return {"removed": removed, "revoked": revoked}

class DisconnectRequest(BaseModel):
    platform: str  # google_ads | meta | wordpress

@app.post("/api/client/disconnect")
def client_disconnect(req: DisconnectRequest, request: Request):
    # Plain `def`: the revoke is a blocking HTTP call to the provider
    client_id = _require_session(request)
    if req.platform not in _DISCONNECT_GROUPS:
        raise HTTPException(status_code=400, detail="unknown platform")
    result = _disconnect_platform(client_id, req.platform)
    if not result["removed"]:
        raise HTTPException(status_code=404, detail="הפלטפורמה הזו לא מחוברת")
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
# disconnect groups. Deliberately NOT purged: alerts (system-level history),
# weekly_reports (aggregate business documents), leads (pre-client sales
# funnel records).
_ARCHIVE_PURGE_TABLES = ("client_accounts", "client_agents", "client_activity",
                          "client_communications", "client_suggestions",
                          "client_costs", "login_codes")

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
        # Tombstone: id/name/package/status/created_at stay for the admin list;
        # every contact/PII field goes. An emptied email also backstops the
        # login hard-lock - request-code starts from an email lookup.
        db.table("clients").update({
            "email": "", "phone": "", "address": "", "business_name": "",
            "business_tax_id": "", "profile_image": None, "owner_gender": None,
        }).eq("id", client_id).execute()
    except Exception as e:
        purge_errors.append(f"clients tombstone: {e}")
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
    client = get_client(client_id)
    if not client:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if client.get("status") in ("closed", "transferred"):
        return {"status": client["status"], "already_offboarded": True}

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
    # rows just logged, and it must exist before the purge below deletes them)
    export_json = ""
    try:
        export_json = json.dumps(_build_client_export(client_id, performance_snapshot),
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
                                    export_name, export_json)
            else:
                send_account_transferred(client["email"], client.get("name", ""),
                                         export_name, export_json)
    except Exception as e:
        print(f"[offboarding] confirmation email failed for client {client_id} (non-fatal): {e}")

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
        f"reason: {reason or '-'}"
    ])

    return {"status": new_status, "subscription_cancelled": subscription_cancelled,
            "disconnected": disconnected, "archive": archive}

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
        raise HTTPException(status_code=400, detail="אישור הסגירה לא תקין")
    result = _offboard_client(client_id, "closure", req.reason.strip())
    response.delete_cookie("session", path="/")  # closure ends the session too
    return {"success": True, "data": result}

@app.post("/api/client/transfer-out")
def client_transfer_out(req: OffboardRequest, request: Request, response: Response):
    # Hard lock applies to transfers too - the client's copy of their data
    # rides the confirmation email as an attachment, not the dashboard
    client_id = _require_session(request)
    if req.confirm_phrase.strip().lower() not in TRANSFER_CONFIRM_PHRASES:
        raise HTTPException(status_code=400, detail="אישור המעבר לא תקין")
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
    export_json = json.dumps(_build_client_export(client_id),
                             ensure_ascii=False, indent=2, default=str)
    return {"success": True, "data": _archive_and_purge_client(client_id, client, export_json)}

# ─── Google Ads OAuth (client connects their own ad account) ─────────────────

@app.get("/api/oauth/google-ads/start")
async def google_ads_oauth_start(request: Request):
    client_id = _require_session(request)
    state = create_oauth_state_token(client_id)
    return RedirectResponse(url=google_ads_service.build_consent_url(state))

@app.get("/api/oauth/google-ads/callback")
def google_ads_oauth_callback(state: str = "", code: str = "", error: str = ""):
    # Identity comes from the signed state token, not the session cookie - the
    # browser arrives here from Google's domain and the state also blocks CSRF
    client_id = verify_oauth_state_token(state)
    if not client_id:
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

        customer_ids = google_ads_service.list_accessible_customers(refresh_token)
        if not customer_ids:
            print(f"[google_ads oauth] client {client_id}: authorized user has no accessible Ads accounts")
            return RedirectResponse(url="/dashboard/?connect_error=no_ads_account")

        # MVP: first accessible account. Clients with several accounts (or an
        # MCC) need an account-picker step - flagged as a known limitation.
        customer_id = customer_ids[0]
        if len(customer_ids) > 1:
            print(f"[google_ads oauth] client {client_id}: {len(customer_ids)} accessible accounts, using {customer_id}")

        upsert_account(client_id, "google_ads", customer_id, refresh_token, "active")
        log_activity(client_id, "google_ads_agent", "account_connected",
                     {"customer_id": customer_id, "accessible_accounts": len(customer_ids)}, {})
        return RedirectResponse(url="/dashboard/?connected=google_ads")
    except Exception as e:
        import traceback
        traceback.print_exc()
        return RedirectResponse(url="/dashboard/?connect_error=google_ads")

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
        if not ad_accounts and not pages:
            print(f"[meta oauth] client {client_id}: authorized user has no ad accounts or Pages")
            return RedirectResponse(url="/dashboard/?connect_error=no_meta_assets")

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

        log_activity(client_id, "meta_ads_agent", "account_connected", connected, {})
        return RedirectResponse(url="/dashboard/?connected=meta")
    except Exception as e:
        import traceback
        traceback.print_exc()
        return RedirectResponse(url="/dashboard/?connect_error=meta")

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
    from agents.meta_content_agent import run_inbox_scan
    try:
        return {"success": True, "data": run_inbox_scan()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/meta-content/engagement", dependencies=_admin_only)
def meta_content_engagement(client_id: int):
    from agents.meta_content_agent import get_engagement_summary
    try:
        return {"success": True, "data": get_engagement_summary(client_id)}
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
    # Creates a BILLABLE hosted site (reserved InstaWP clone) - admin/fulfillment
    # only, never client-facing. Plain `def`: provisioning polls for minutes.
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
    tool: str      # seoptimer | semrush | ahrefs (the PRICING seo_tiers ladder)
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
    # quality gate enforced inside the agent (iron rules).
    from agents.seo_agent import write_article
    result = write_article(req.client_id, req.topic, req.target_keyword, req.notes)
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

# ─── Avatar agent (distinct paid add-on — HeyGen twins + ElevenLabs voices) ──

class AvatarConnectRequest(BaseModel):
    heygen_key: str
    elevenlabs_key: str = ""  # optional - voice cloning is its own choice

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

@app.post("/api/avatar/create-voice", dependencies=_admin_only)
def avatar_create_voice(req: AvatarVoiceRequest):
    from agents.avatar_agent import create_voice_clone
    result = create_voice_clone(req.client_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
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
    script_text: str
    avatar_id: str = ""
    heygen_voice_id: str = ""

@app.post("/api/avatar/generate-video", dependencies=_admin_only)
def avatar_generate_video(req: AvatarVideoRequest):
    # SLOW (HeyGen render polls up to ~15 min). Tier + minutes + consent
    # gates enforced in the agent; minutes are the billed unit
    from agents.avatar_agent import generate_avatar_video
    result = generate_avatar_video(req.client_id, req.script_text,
                                   req.avatar_id, req.heygen_voice_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("errors", ["unknown error"]))
    return {"success": True, "data": result}

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
    if not drive_service.is_configured() or not os.environ.get("DRIVE_MEDIA_FOLDER_ID"):
        raise HTTPException(status_code=503, detail="תיקיית המדיה עדיין לא מוגדרת — דברו איתנו בצ'אט")
    from agents.media_agent import get_client_media_link
    try:
        return {"success": True, "data": {"link": get_client_media_link(client_id)}}
    except Exception as e:
        print(f"[media_folder] failed for client {client_id}: {e}")
        raise HTTPException(status_code=500, detail="לא הצלחנו לפתוח את התיקייה — נסו שוב")

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
                                   request: Request):
    client_id = _require_session(request)
    from agents.engagement_agent import decide_suggestion
    result = decide_suggestion(client_id, suggestion_id, req.decision)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "decide failed"))
    return {"success": True, "data": result}

class ClientProfileRequest(BaseModel):
    owner_gender: str | None = None   # male | female — the client's own one-tap choice in
                                      # the dashboard (never inferred from their name)
    profile_image: str | None = None  # data:image/... URL, resized in the browser before
                                      # upload; "" removes the picture

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
    if not changes:
        raise HTTPException(status_code=400, detail="nothing to update")
    db.table("clients").update(changes).eq("id", client_id).execute()
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
    return {"success": True}

@app.get("/api/admin/overview")
def admin_overview(request: Request):
    _require_admin(request)
    return {"success": True, "data": admin_service.get_overview()}

@app.get("/api/admin/clients")
def admin_clients(request: Request):
    _require_admin(request)
    return {"success": True, "data": admin_service.list_clients_admin()}

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

@app.get("/api/budget/scan", dependencies=_admin_only)
def budget_scan():
    """Weekly cron (X-Admin-Key) - snapshots every active client's financial
    picture for trend history and raises deduped deviation alerts. See the
    budget skill for the scheduler command."""
    from agents.budget_agent import run_weekly_scan
    return {"success": True, "data": run_weekly_scan()}

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

@app.post("/api/dynamic-questions")
def dynamic_questions(req: DynamicQRequest):
    try:
        from agents.onboarding_agent import get_dynamic_questions, get_api_key
        api_key = os.environ.get("ANTHROPIC_API_KEY", "") or get_api_key()
        questions = get_dynamic_questions(req.intro, req.answers, api_key)
        return {"questions": questions}
    except Exception as e:
        return {"questions": []}

class FilterRequest(BaseModel):
    intro: str
    answers: dict

@app.post("/api/filter-questions")
def filter_questions_endpoint(req: FilterRequest):
    try:
        from agents.question_filter import get_skip_ids
        return {"skip_ids": get_skip_ids(req.intro)}
    except Exception as e:
        import traceback
        print(f"Filter error: {e}")
        traceback.print_exc()
        return {"skip_ids": []}

# Must be last — catch-all for the landing page
app.mount("/", StaticFiles(directory=os.path.join(BASE_DIR, "dashboard", "landing"), html=True), name="landing")
