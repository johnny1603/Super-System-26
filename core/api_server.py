import os
from agents.keys_agent import inject_all_keys, validate_keys
inject_all_keys()
validate_keys()
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import sys, os, json, sqlite3
from datetime import datetime

sys.path.append('/home/johnosaki94/super-system')
from agents.onboarding_agent import run_full_onboarding
from core.email_service import send_client_report, send_admin_alert

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/chat", StaticFiles(directory="/home/johnosaki94/super-system/dashboard/onboarding", html=True), name="chat")

# DB
def init_db():
    conn = sqlite3.connect('/home/johnosaki94/super-system/core/leads.db')
    conn.execute('''CREATE TABLE IF NOT EXISTS leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT,
        client_email TEXT,
        client_name TEXT,
        answers TEXT,
        proposal TEXT,
        approved INTEGER,
        setup_fee INTEGER,
        monthly_fee INTEGER
    )''')
    conn.commit()
    conn.close()

init_db()

class OnboardingRequest(BaseModel):
    answers: dict
    client_email: str = ""
    client_name: str = ""

@app.post("/api/onboarding")
async def onboarding(req: OnboardingRequest):
    try:
        result = run_full_onboarding(req.answers)
        proposal = result["proposal"]

        # שמירה ב-DB
        conn = sqlite3.connect('/home/johnosaki94/super-system/core/leads.db')
        conn.execute('''INSERT INTO leads 
            (created_at, client_email, client_name, answers, proposal, approved, setup_fee, monthly_fee)
            VALUES (?,?,?,?,?,?,?,?)''',
            (datetime.now().isoformat(), req.client_email, req.client_name,
             json.dumps(req.answers, ensure_ascii=False),
             json.dumps(proposal, ensure_ascii=False),
             1 if proposal.get('approved') else 0,
             proposal.get('setup_fee_total', 0),
             proposal.get('monthly_management_total', 0)))
        conn.commit()
        conn.close()

        # שליחת מיילים
        send_admin_alert(req.answers, proposal)
        if req.client_email:
            send_client_report(req.client_email, req.client_name, proposal)

        return {"success": True, "data": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/leads")
async def get_leads():
    conn = sqlite3.connect('/home/johnosaki94/super-system/core/leads.db')
    cursor = conn.execute('SELECT * FROM leads ORDER BY created_at DESC')
    leads = [dict(zip([col[0] for col in cursor.description], row)) for row in cursor.fetchall()]
    conn.close()
    return {"leads": leads}

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
