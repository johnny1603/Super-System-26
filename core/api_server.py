import os
from datetime import datetime

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from supabase import create_client, Client

from agents.keys_agent import inject_all_keys, validate_keys
inject_all_keys()
validate_keys()

from agents.onboarding_agent import run_full_onboarding
from agents.master_agent import review_output
from agents.monitor_agent import run_deep_scan
from agents.architect_agent import (
    is_agent_active, create_new_agent, suspend_agent, propose_agent_deletion
)
from agents.client_agent import (
    create_client, get_client, list_clients, update_client_status, complete_onboarding,
    add_account, get_accounts,
    assign_agent, get_client_agents, update_agent_status,
    log_activity, get_activity,
    log_communication, get_communications,
)
from core.email_service import send_client_report, send_admin_alert

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

db: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_SERVICE_KEY"],
)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/chat", StaticFiles(directory=os.path.join(BASE_DIR, "dashboard", "onboarding"), html=True), name="chat")

class OnboardingRequest(BaseModel):
    answers: dict
    client_email: str = ""
    client_name: str = ""

@app.post("/api/onboarding")
async def onboarding(req: OnboardingRequest):
    try:
        result = run_full_onboarding(req.answers)
        proposal = result["proposal"]
        if is_agent_active("master_agent"):
            review_output("proposal", proposal, req.answers)

        # שמירה ב-DB
        db.table("leads").insert({
            "created_at": datetime.now().isoformat(),
            "client_email": req.client_email,
            "client_name": req.client_name,
            "answers": req.answers,
            "proposal": proposal,
            "approved": bool(proposal.get("approved")),
            "setup_fee": proposal.get("setup_fee_total", 0),
            "monthly_fee": proposal.get("monthly_management_total", 0),
        }).execute()

        # שליחת מיילים
        send_admin_alert(req.answers, proposal)
        if req.client_email:
            send_client_report(req.client_email, req.client_name, proposal)

        return {"success": True, "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/leads")
async def get_leads():
    result = db.table("leads").select("*").order("created_at", desc=True).execute()
    return {"leads": result.data}

@app.get("/api/monitor/scan")
async def monitor_scan():
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
async def architect_create(req: CreateAgentRequest):
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

@app.get("/health")
async def health():
    return {"status": "ok", "service": "uallak-super-system"}

class DynamicQRequest(BaseModel):
    intro: str
    answers: dict

@app.post("/api/dynamic-questions")
async def dynamic_questions(req: DynamicQRequest):
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
async def filter_questions_endpoint(req: FilterRequest):
    try:
        from agents.question_filter import filter_questions
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        BASE_Q_IDS = ["business_age","financial_status","marketing_budget","existing_digital","main_goal","biggest_fear"]
        answers_with_intro = {"intro": req.intro, **req.answers}
        skip_ids = []
        import anthropic, json
        client = anthropic.Anthropic(api_key=api_key)
        system = "You are a question filter. Given a client opening message, return JSON with skip_ids array containing question IDs already answered. IDs: business_age, financial_status, marketing_budget, existing_digital, main_goal, biggest_fear. Return JSON only: {skip_ids: []}"
        response = client.messages.create(model="claude-sonnet-4-6", max_tokens=200, system=system, messages=[{"role":"user","content":f"Client said: {req.intro}"}])
        raw = response.content[0].text.replace("```json","").replace("```","").strip()
        skip_ids = json.loads(raw).get("skip_ids", [])
        print(f"Filter skip_ids: {skip_ids}")
        return {"skip_ids": skip_ids}
    except Exception as e:
        import traceback
        print(f"Filter error: {e}")
        traceback.print_exc()
        return {"skip_ids": []}
