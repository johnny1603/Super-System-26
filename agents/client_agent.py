import os
from supabase import create_client as _supabase_client

# Created lazily — no DB clients at import time (api_server imports this at startup)
_db_instance = None


def _db():
    global _db_instance
    if _db_instance is None:
        _db_instance = _supabase_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"],
        )
    return _db_instance


# ─── Clients ──────────────────────────────────────────────────────────────────

def create_client(name: str, email: str = "", phone: str = "", package: str = "") -> dict:
    result = _db().table("clients").insert({
        "name": name,
        "email": email,
        "phone": phone,
        "package": package,
        "status": "active",
        "onboarding_completed": False,
    }).execute()
    return result.data[0] if result.data else {}


def get_client(client_id: int) -> dict:
    result = _db().table("clients").select("*").eq("id", client_id).execute()
    return result.data[0] if result.data else {}


def get_client_by_email(email: str) -> dict:
    result = _db().table("clients").select("*").eq("email", email).limit(1).execute()
    return result.data[0] if result.data else {}


def list_clients(status: str = None) -> list:
    query = _db().table("clients").select("*").order("created_at", desc=True)
    if status:
        query = query.eq("status", status)
    return query.execute().data or []


def update_client_status(client_id: int, status: str) -> dict:
    result = _db().table("clients").update({"status": status}).eq("id", client_id).execute()
    return result.data[0] if result.data else {}


def complete_onboarding(client_id: int) -> dict:
    result = _db().table("clients").update({"onboarding_completed": True}).eq("id", client_id).execute()
    return result.data[0] if result.data else {}


# ─── Client accounts ──────────────────────────────────────────────────────────

def add_account(client_id: int, platform: str, account_id: str = "",
                access_token: str = "", status: str = "active") -> dict:
    result = _db().table("client_accounts").insert({
        "client_id": client_id,
        "platform": platform,
        "account_id": account_id,
        "access_token": access_token,
        "status": status,
    }).execute()
    return result.data[0] if result.data else {}


def get_accounts(client_id: int) -> list:
    return _db().table("client_accounts").select("*").eq("client_id", client_id).execute().data or []


# ─── Client agents ────────────────────────────────────────────────────────────

def assign_agent(client_id: int, agent_name: str) -> dict:
    existing = _db().table("client_agents").select("id").eq("client_id", client_id).eq("agent_name", agent_name).execute()
    if existing.data:
        return existing.data[0]
    result = _db().table("client_agents").insert({
        "client_id": client_id,
        "agent_name": agent_name,
        "status": "active",
    }).execute()
    return result.data[0] if result.data else {}


def get_client_agents(client_id: int) -> list:
    return _db().table("client_agents").select("*").eq("client_id", client_id).execute().data or []


def update_agent_status(client_id: int, agent_name: str, status: str) -> dict:
    result = (
        _db().table("client_agents")
        .update({"status": status})
        .eq("client_id", client_id)
        .eq("agent_name", agent_name)
        .execute()
    )
    return result.data[0] if result.data else {}


# ─── Activity log ─────────────────────────────────────────────────────────────

def log_activity(client_id: int, agent_name: str, action_type: str,
                 details: dict = None, result: dict = None) -> dict:
    row = _db().table("client_activity").insert({
        "client_id": client_id,
        "agent_name": agent_name,
        "action_type": action_type,
        "details": details or {},
        "result": result or {},
    }).execute()
    return row.data[0] if row.data else {}


def get_activity(client_id: int, limit: int = 50) -> list:
    return (
        _db().table("client_activity")
        .select("*")
        .eq("client_id", client_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data or []
    )


# ─── Communications ───────────────────────────────────────────────────────────

def log_communication(client_id: int, direction: str, channel: str, content: str) -> dict:
    result = _db().table("client_communications").insert({
        "client_id": client_id,
        "direction": direction,
        "channel": channel,
        "content": content,
    }).execute()
    return result.data[0] if result.data else {}


def get_communications(client_id: int, limit: int = 50) -> list:
    return (
        _db().table("client_communications")
        .select("*")
        .eq("client_id", client_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
        .data or []
    )


# ─── Login codes (email + one-time code auth) ─────────────────────────────────

def create_login_code(client_id: int, code: str, expires_at: str) -> dict:
    result = _db().table("login_codes").insert({
        "client_id": client_id,
        "code": code,
        "expires_at": expires_at,
        "used": False,
    }).execute()
    return result.data[0] if result.data else {}


def get_active_login_code(client_id: int, code: str) -> dict:
    result = (
        _db().table("login_codes")
        .select("*")
        .eq("client_id", client_id)
        .eq("code", code)
        .eq("used", False)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else {}


def mark_login_code_used(login_code_id) -> dict:
    result = _db().table("login_codes").update({"used": True}).eq("id", login_code_id).execute()
    return result.data[0] if result.data else {}
