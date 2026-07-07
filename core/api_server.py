import json
import os
import random
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Request, Response
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
    create_client, get_client, get_client_by_email, list_clients, update_client_status, complete_onboarding,
    add_account, get_accounts,
    assign_agent, get_client_agents, update_agent_status,
    log_activity, get_activity,
    log_communication, get_communications,
    create_login_code, get_active_login_code, mark_login_code_used,
)
from core.email_service import send_client_report, send_admin_alert, send_payment_confirmation, send_login_code
from core.paypal_service import create_subscription, verify_webhook_signature, get_subscription_status
from core.session import create_session_token, verify_session_token

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

@app.get("/api/leads")
async def get_leads():
    result = db.table("leads").select("*").order("created_at", desc=True).execute()
    return {"leads": result.data}

@app.get("/api/monitor/scan")
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

@app.post("/api/architect/create")
def architect_create(req: CreateAgentRequest):
    try:
        result = create_new_agent(req.need_description)
        return {"success": True, "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/architect/suspend")
async def architect_suspend(req: SuspendAgentRequest):
    result = suspend_agent(req.agent_name, req.reason)
    return {"success": True, "data": result}

@app.post("/api/architect/propose-deletion")
async def architect_propose_deletion(req: ProposeDeleteRequest):
    result = propose_agent_deletion(req.agent_name, req.reason)
    return {"success": True, "data": result}

# ─── Checkout ─────────────────────────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    client_name: str
    client_email: str = ""
    package_id: str = ""
    package_name: str = ""
    setup_fee_total: int = 0
    monthly_management_total: int = 0

@app.post("/api/checkout")
async def checkout(req: CheckoutRequest):
    try:
        client = create_client(req.client_name, req.client_email, package=req.package_name)
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
    print(f"[{source}] client {client_id} marked active, confirmation email sent")

@app.get("/api/payment-success")
async def payment_success(client_id: int, subscription_id: str = None):
    try:
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

@app.post("/api/clients")
async def api_create_client(req: CreateClientRequest):
    try:
        client = create_client(req.name, req.email, req.phone, req.package)
        return {"success": True, "data": client}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/clients")
async def api_list_clients(status: str = None):
    try:
        return {"success": True, "data": list_clients(status)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/clients/{client_id}")
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
            code = f"{random.randint(0, 999999):06d}"
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

@app.post("/api/login/verify-code")
async def login_verify_code(req: LoginVerifyCodeRequest, response: Response):
    invalid = HTTPException(status_code=401, detail="קוד שגוי או שפג תוקפו")

    client = get_client_by_email(req.email.strip())
    if not client:
        raise invalid

    login_code = get_active_login_code(client["id"], req.code.strip())
    if not login_code:
        raise invalid

    expires_at = login_code["expires_at"].replace("Z", "+00:00")
    if datetime.fromisoformat(expires_at) < datetime.now(timezone.utc):
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

    return {"success": True, "data": {
        "client": {
            "id": client.get("id"),
            "name": client.get("name"),
            "package": client.get("package"),
            "status": client.get("status"),
        },
        "monthly_fee": monthly_fee,
        "next_billing_date": next_billing_date,
        "connections": accounts,
        "activity": activity[:10],
    }}

@app.patch("/api/clients/{client_id}/status")
async def api_update_client_status(client_id: int, req: UpdateStatusRequest):
    try:
        return {"success": True, "data": update_client_status(client_id, req.status)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/clients/{client_id}/complete-onboarding")
async def api_complete_onboarding(client_id: int):
    try:
        return {"success": True, "data": complete_onboarding(client_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/clients/{client_id}/accounts")
async def api_add_account(client_id: int, req: AddAccountRequest):
    try:
        return {"success": True, "data": add_account(client_id, req.platform, req.account_id, req.access_token, req.status)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/clients/{client_id}/accounts")
async def api_get_accounts(client_id: int):
    try:
        return {"success": True, "data": get_accounts(client_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/clients/{client_id}/agents")
async def api_assign_agent(client_id: int, req: AssignAgentRequest):
    try:
        return {"success": True, "data": assign_agent(client_id, req.agent_name)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/clients/{client_id}/agents")
async def api_get_client_agents(client_id: int):
    try:
        return {"success": True, "data": get_client_agents(client_id)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/clients/{client_id}/agents/status")
async def api_update_agent_status(client_id: int, req: UpdateAgentStatusRequest):
    try:
        return {"success": True, "data": update_agent_status(client_id, req.agent_name, req.status)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/clients/{client_id}/activity")
async def api_log_activity(client_id: int, req: LogActivityRequest):
    try:
        return {"success": True, "data": log_activity(client_id, req.agent_name, req.action_type, req.details, req.result)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/clients/{client_id}/activity")
async def api_get_activity(client_id: int, limit: int = 50):
    try:
        return {"success": True, "data": get_activity(client_id, limit)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/clients/{client_id}/communications")
async def api_log_communication(client_id: int, req: LogCommunicationRequest):
    try:
        return {"success": True, "data": log_communication(client_id, req.direction, req.channel, req.content)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/clients/{client_id}/communications")
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
