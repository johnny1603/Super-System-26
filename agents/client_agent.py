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

def create_client(name: str, email: str = "", phone: str = "", package: str = "",
                   address: str = "", business_name: str = "", business_tax_id: str = "",
                   language: str = "he") -> dict:
    # language: the client's UI language switcher choice at checkout time
    # (uallakI18n.current(), same 5 codes as the frontend engine) - the
    # stored preference outbound emails use, since (unlike chat) there's no
    # live message to detect language from. Defaults to Hebrew.
    row = {
        "name": name,
        "email": email,
        "phone": phone,
        "package": package,
        "address": address,
        "business_name": business_name,
        "business_tax_id": business_tax_id,
        "status": "active",
        "onboarding_completed": False,
    }
    try:
        result = _db().table("clients").insert({
            **row, "language": language if language in ("he", "en", "fr", "ar", "ru") else "he",
        }).execute()
    except Exception as e:
        # clients.language doesn't exist in Supabase yet (nullable text
        # column, default 'he' - add it whenever convenient) - never let a
        # missing column break checkout, which is what this function serves
        print(f"[client_agent] create_client without language (column missing?): {e}")
        result = _db().table("clients").insert(row).execute()
    return result.data[0] if result.data else {}


def get_client(client_id: int) -> dict:
    result = _db().table("clients").select("*").eq("id", client_id).execute()
    return result.data[0] if result.data else {}


# Credential/auth-state columns (core/client_auth). Never shipped to ANY
# browser, the admin's included - strip them wherever a full row goes out.
CLIENT_SECRET_FIELDS = ("password_hash", "google_sub", "failed_password_attempts",
                        "password_locked_until", "session_version")


def public_client(client: dict) -> dict:
    if not client:
        return client
    out = {k: v for k, v in client.items() if k not in CLIENT_SECRET_FIELDS}
    out["has_password"] = bool(client.get("password_hash"))
    out["google_linked"] = bool(client.get("google_sub"))
    return out


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


def update_client_package(client_id: int, package: str) -> dict:
    result = _db().table("clients").update({"package": package}).eq("id", client_id).execute()
    return result.data[0] if result.data else {}


def complete_onboarding(client_id: int) -> dict:
    result = _db().table("clients").update({"onboarding_completed": True}).eq("id", client_id).execute()
    return result.data[0] if result.data else {}


# ─── Client accounts ──────────────────────────────────────────────────────────

# Credentials are written ONLY here, and only ENCRYPTED (core/secrets_vault):
# the plaintext column is always blanked. If SECRETS_ENCRYPTION_KEY is
# missing the write raises VaultUnavailable and nothing is stored.

def _require_client(client_id: int):
    """Credentials are attached to an existing client or not at all."""
    if not client_id or not get_client(client_id):
        print(f"[client_agent] ADMIN: refused to store a credential for unknown client {client_id}")
        raise ValueError(f"unknown client {client_id} - credential not stored")


def add_account(client_id: int, platform: str, account_id: str = "",
                access_token: str = "", status: str = "active") -> dict:
    from core.secrets_vault import public_account, sealed_fields
    _require_client(client_id)
    result = _db().table("client_accounts").insert({
        "client_id": client_id,
        "platform": platform,
        "account_id": account_id,
        **sealed_fields(access_token, client_id, platform),
        "status": status,
    }).execute()
    return public_account(result.data[0]) if result.data else {}


def get_accounts(client_id: int) -> list:
    """The client's connections WITHOUT any secret (core/secrets_vault
    .public_account) — safe for screens, prompts and logs. An integration
    that needs the credential calls secrets_vault.get_client_secret."""
    from core.secrets_vault import public_account
    rows = _db().table("client_accounts").select("*").eq("client_id", client_id).execute().data or []
    return [public_account(r) for r in rows]


def upsert_account(client_id: int, platform: str, account_id: str = "",
                   access_token: str = "", status: str = "active") -> dict:
    """Reconnecting a platform must replace the stored credentials, not pile up
    duplicate rows (get-connection queries assume one live row per platform).
    Matched by (client_id, platform) only — never across clients — and the
    client must exist."""
    from core.secrets_vault import public_account, sealed_fields
    _require_client(client_id)
    existing = (
        _db().table("client_accounts").select("id,client_id")
        .eq("client_id", client_id).eq("platform", platform)
        .limit(1).execute()
    )
    values = {"account_id": account_id, **sealed_fields(access_token, client_id, platform),
              "status": status}
    if existing.data:
        if int(existing.data[0].get("client_id") or 0) != int(client_id):
            raise ValueError(f"client_accounts row {existing.data[0]['id']} is not client {client_id}'s")
        result = (_db().table("client_accounts").update(values)
                  .eq("id", existing.data[0]["id"]).eq("client_id", client_id).execute())
    else:
        result = _db().table("client_accounts").insert({"client_id": client_id, "platform": platform, **values}).execute()
    return public_account(result.data[0]) if result.data else {}


def remove_accounts(client_id: int, platforms: list = None) -> int:
    """Delete the stored client_accounts rows (credentials included) for the
    given platforms — or ALL of the client's platforms when platforms is None.
    Deleting the row (not just flipping status) is the point: a disconnect must
    leave no stored credential behind. Returns how many rows were removed."""
    query = _db().table("client_accounts").delete().eq("client_id", client_id)
    if platforms:
        query = query.in_("platform", platforms)
    result = query.execute()
    return len(result.data or [])


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
    # Every stored message passes through here: likely keys/passwords are
    # replaced with [REDACTED] before they can reach the table (core/redaction)
    from core.redaction import redact
    result = _db().table("client_communications").insert({
        "client_id": client_id,
        "direction": direction,
        "channel": channel,
        "content": redact(content),
    }).execute()
    return result.data[0] if result.data else {}


def get_communications(client_id: int, limit: int = 50, channel: str = None) -> list:
    query = _db().table("client_communications").select("*").eq("client_id", client_id)
    if channel:
        query = query.eq("channel", channel)
    return query.order("created_at", desc=True).limit(limit).execute().data or []


# ─── Login codes (email + one-time code auth) ─────────────────────────────────

def create_login_code(client_id: int, code: str, expires_at: str) -> dict:
    result = _db().table("login_codes").insert({
        "client_id": client_id,
        "code": code,
        "expires_at": expires_at,
        "used": False,
    }).execute()
    return result.data[0] if result.data else {}


def get_active_login_code(client_id: int) -> dict:
    """The client's most recent unused login code, regardless of what code value
    was submitted - callers compare the code themselves so a wrong guess can be
    counted as a failed attempt against this same row."""
    result = (
        _db().table("login_codes")
        .select("*")
        .eq("client_id", client_id)
        .eq("used", False)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else {}


def increment_login_code_attempts(login_code_id, current_attempts: int) -> int:
    new_count = current_attempts + 1
    _db().table("login_codes").update({"failed_attempts": new_count}).eq("id", login_code_id).execute()
    return new_count


def mark_login_code_used(login_code_id) -> dict:
    result = _db().table("login_codes").update({"used": True}).eq("id", login_code_id).execute()
    return result.data[0] if result.data else {}
