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
    add_account, get_accounts, upsert_account,
    assign_agent, get_client_agents, update_agent_status,
    log_activity, get_activity,
    log_communication, get_communications,
    create_login_code, get_active_login_code, increment_login_code_attempts, mark_login_code_used,
)
from core.email_service import send_client_report, send_admin_alert, send_payment_confirmation, send_login_code
from core.paypal_service import (
    create_subscription, verify_webhook_signature, get_subscription_status,
    get_plan, create_plan, revise_subscription_plan, list_subscription_transactions,
    create_invoice,
)
from core.session import (
    create_session_token, verify_session_token,
    create_oauth_state_token, verify_oauth_state_token,
    create_admin_session_token, verify_admin_session_token,
)
from core import google_ads_service
from core import meta_service
from core import admin_service

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
    if event_type in ACTIVATION_EVENTS:
        client_id_raw = resource.get("custom_id") or resource.get("custom")
        if client_id_raw:
            try:
                client_id = int(client_id_raw)
                _mark_paid_and_notify(client_id, resource.get("id"), source="paypal_webhook")
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

@app.post("/api/login/request-code")
async def login_request_code(req: LoginRequestCodeRequest):
    try:
        client = get_client_by_email(req.email.strip())
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
    if not client:
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
            "package": client.get("package"),
            "status": client.get("status"),
            "created_at": client.get("created_at"),
        },
        "monthly_fee": monthly_fee,
        "next_billing_date": next_billing_date,
        "connections": accounts,
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
async def client_chat_history(request: Request):
    client_id = _require_session(request)
    history = get_communications(client_id, limit=50, channel="dashboard_chat")
    return {"success": True, "history": list(reversed(history))}

# ─── Package upgrade + billing (client-facing, session-gated) ────────────────

def _client_subscription_info(client_id: int) -> dict:
    """monthly_fee + subscription_id from the newest checkout activity row -
    the same derivation /api/dashboard uses (no dedicated billing table yet)."""
    for entry in get_activity(client_id, limit=100):
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
