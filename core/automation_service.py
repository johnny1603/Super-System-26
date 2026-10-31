"""Marketing automation (ManyChat) — a MANUAL service, deliberately not
built like any other integration in the dashboard.

Every other connection (Google/Meta/TikTok/YouTube/WordPress/Higgsfield) is a
self-service flow the client completes alone, and every one of them has a price
the system already knows. This one has neither, on purpose:

- There is NO fixed price and no self-service setup. Johnny runs a personal
  needs assessment with the client over the existing WhatsApp support channel,
  and only then prices and builds the automation himself. Nothing here may ever
  quote a number — not the sales chat, not the support chat, not the dashboard.
- The client dashboard shows it as an INFORMATIONAL card only. There is no
  "connect" button, no OAuth, no client-facing credential form.
- If the client decides to go ahead, they open their OWN ManyChat account
  with an email + password — explicitly NOT "Sign in with Google", because
  Johnny later signs in as them and a Google-federated account makes that a
  fight with Google's own auth. They then send those credentials to Johnny, who
  stores them in the ADMIN dashboard. See SIGNUP_RULE_HE, which is the single
  wording of that instruction (support_agent quotes it to the client).

Storage reuses the existing per-client external-account pattern rather than a
new table: one `client_accounts` row per vendor, `platform` = the vendor slug,
`account_id` = the username, `access_token` = the password — the same shape
seo_agent.connect_tool and the WordPress Application Password already use.
Credentials are ADMIN-ONLY: /api/client/dashboard never ships stored tokens to
the browser, and nothing here is exposed on a client-session endpoint.
"""
from core.agent_base import log_step

AGENT_NAME = "automation_service"

# The ONLY place a vendor is named. Adding one is a single entry here — the
# admin UI, the client cards and the support prompt all read from it.
VENDORS = {
    "manychat": {
        "name": "ManyChat",
        "login_url": "https://app.manychat.com/",
        "signup_url": "https://manychat.com/signup",
        "what_he": "אוטומציות שיחה בוואטסאפ, אינסטגרם ומסנג׳ר — מענה אוטומטי לפניות ולידים",
    },
}

# Make was retired on 2026-10-31: those automations are built in-house now.
# It is no longer a vendor — not offered, not priced, not shown — but a
# client's existing `client_accounts` row with platform "make" is LEFT
# INTACT (never deleted by this change); see RETIRED_VENDORS.
RETIRED_VENDORS = ("make",)

# Said to the client verbatim (support_agent) before they open an account. The
# Google warning is the operational point, not a style choice: Johnny signs in
# to these accounts later.
SIGNUP_RULE_HE = (
    "חשוב: פותחים את החשבון עם אימייל וסיסמה רגילים — לא דרך "
    "\"התחברות עם Google\". חשבון שנפתח דרך Google יוצר התנגשות כשאנחנו "
    "נכנסים לחשבון כדי לבנות ולתחזק את האוטומציה עבורכם."
)

# What the support chat is allowed to say about how this is sold. No prices.
POLICY_NOTE = (
    "ManyChat is a MANUAL service: there is no fixed price and no "
    "self-service setup in the system. The client talks to the team on WhatsApp, "
    "a personal needs assessment happens, and only then is the automation priced "
    "and built for them. NEVER quote a price, a price range, or a setup fee for "
    "this, and never promise a delivery date."
)


def vendor(slug: str) -> dict:
    return VENDORS.get((slug or "").strip().lower()) or {}


# ─── Credentials: API token, never a password (2026-10-18) ──────────────────
# Client PASSWORDS are no longer stored. A connection is the vendor's API
# token, encrypted per client (core/secrets_vault) and never shown back —
# the admin screen sees "connected" + at most the last 4 characters.
#
# TODO(owner-verify): the code cannot tell whether each vendor issues a token
# that is enough for HOW Johnny builds automations. Verify per vendor:
#   ManyChat — that a per-account API key exists on the client's plan (it is a
#     Pro feature), where it is created, whether it can be revoked, and whether
#     it lets you BUILD flows or only read/send via the API. If building needs
#     a dashboard login, the right model is the client inviting Johnny as a
#     team member (their own login stays theirs) — not a stored password.
# Until verified, tokens are stored but nothing in the system USES them.
TOKEN_ACCOUNT_ID = "api_token"


def _mode(row: dict) -> str:
    if not row:
        return "none"
    return "token" if row.get("account_id") == TOKEN_ACCOUNT_ID else "legacy_login"


def get_credentials(client_id: int) -> list:
    """ADMIN ONLY — connection state per vendor, WITHOUT any secret.
    A legacy username/password row is reported as legacy (kept, not shown,
    not deleted) so the admin can move the client to a token."""
    from agents.client_agent import get_accounts

    rows = {r.get("platform"): r for r in get_accounts(client_id)}
    result = []
    for slug, v in VENDORS.items():
        row = rows.get(slug) or {}
        mode = _mode(row)
        result.append({
            "slug": slug,
            "name": v["name"],
            "login_url": v["login_url"],
            "signup_url": v["signup_url"],
            "mode": mode,
            "saved": mode != "none",
            "secret_last4": (row.get("secret_last4") or "") if mode == "token" else "",
        })
    return result


def save_token(client_id: int, slug: str, api_token: str) -> dict:
    """Store (or replace) one vendor's API token for a client — encrypted by
    upsert_account. Replaces a legacy login row in place."""
    v = vendor(slug)
    if not v:
        return {"success": False, "errors": [f"unknown automation vendor: {slug}"]}
    from agents.client_agent import upsert_account

    api_token = (api_token or "").strip()
    if not api_token:
        return {"success": False, "errors": ["api_token is required"]}
    upsert_account(client_id, slug, account_id=TOKEN_ACCOUNT_ID, access_token=api_token)
    # Deliberately logs the vendor and never the token itself.
    log_step(AGENT_NAME, "token_saved", f"client {client_id}: {v['name']}")
    return {"success": True, "vendor": v["name"]}


def save_credentials(client_id: int, slug: str, username: str, password: str) -> dict:
    """RETIRED: passwords are no longer stored. Kept so an old caller gets a
    clear refusal instead of silently storing a password."""
    return {"success": False, "errors": [
        "client passwords are no longer stored — save the vendor's API token instead"]}


def legacy_logins() -> list:
    """ADMIN ONLY — every client still on a stored username/password login
    (vendor + client only, never the login itself)."""
    from agents.client_agent import _db
    rows = (_db().table("client_accounts").select("client_id,platform,account_id")
            .in_("platform", list(VENDORS)).execute().data or [])
    return sorted(({"client_id": r["client_id"], "vendor": VENDORS[r["platform"]]["name"]}
                   for r in rows if r.get("account_id") != TOKEN_ACCOUNT_ID),
                  key=lambda r: (r["client_id"], r["vendor"]))


def remove_credentials(client_id: int, slug: str) -> dict:
    from agents.client_agent import remove_accounts

    v = vendor(slug)
    if not v:
        return {"success": False, "errors": [f"unknown automation vendor: {slug}"]}
    removed = remove_accounts(client_id, [slug])
    log_step(AGENT_NAME, "credentials_removed", f"client {client_id}: {v['name']}")
    return {"success": True, "removed": removed}
