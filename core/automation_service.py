"""Marketing automation (ManyChat / Make) — a MANUAL service, deliberately not
built like any other integration in the dashboard.

Every other connection (Google/Meta/TikTok/YouTube/WordPress/Higgsfield) is a
self-service flow the client completes alone, and every one of them has a price
the system already knows. This one has neither, on purpose:

- There is NO fixed price and no self-service setup. Johnny runs a personal
  needs assessment with the client over the existing WhatsApp support channel,
  and only then prices and builds the automation himself. Nothing here may ever
  quote a number — not the sales chat, not the support chat, not the dashboard.
- The client dashboard shows these two as INFORMATIONAL cards only. There is no
  "connect" button, no OAuth, no client-facing credential form.
- If the client decides to go ahead, they open their OWN ManyChat/Make account
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
    "make": {
        "name": "Make",
        "login_url": "https://www.make.com/en/login",
        "signup_url": "https://www.make.com/en/register",
        "what_he": "חיבור בין המערכות שלך — טפסים, CRM, מיילים וגיליונות — בלי עבודה ידנית",
    },
}

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
    "ManyChat and Make are a MANUAL service: there is no fixed price and no "
    "self-service setup in the system. The client talks to the team on WhatsApp, "
    "a personal needs assessment happens, and only then is the automation priced "
    "and built for them. NEVER quote a price, a price range, or a setup fee for "
    "this, and never promise a delivery date."
)


def vendor(slug: str) -> dict:
    return VENDORS.get((slug or "").strip().lower()) or {}


def get_credentials(client_id: int) -> list:
    """ADMIN ONLY — the stored username/password per vendor, in the clear,
    because the admin UI exists to copy-paste them into the vendor's login page.
    Never call this from a client-session endpoint."""
    from agents.client_agent import get_accounts

    rows = {r.get("platform"): r for r in get_accounts(client_id)}
    result = []
    for slug, v in VENDORS.items():
        row = rows.get(slug) or {}
        result.append({
            "slug": slug,
            "name": v["name"],
            "login_url": v["login_url"],
            "signup_url": v["signup_url"],
            "username": row.get("account_id") or "",
            "password": row.get("access_token") or "",
            "saved": bool(row.get("account_id") or row.get("access_token")),
        })
    return result


def save_credentials(client_id: int, slug: str, username: str, password: str) -> dict:
    """Store (or replace) one vendor's credentials for a client. upsert_account
    replaces the existing row rather than stacking a second one, so re-sending
    corrected credentials can't leave a stale password behind."""
    from agents.client_agent import upsert_account

    v = vendor(slug)
    if not v:
        return {"success": False, "errors": [f"unknown automation vendor: {slug}"]}
    username, password = (username or "").strip(), (password or "").strip()
    if not username or not password:
        return {"success": False, "errors": ["username and password are both required"]}

    upsert_account(client_id, slug, account_id=username, access_token=password)
    # Deliberately logs the vendor and never the credentials themselves.
    log_step(AGENT_NAME, "credentials_saved", f"client {client_id}: {v['name']}")
    return {"success": True, "vendor": v["name"]}


def remove_credentials(client_id: int, slug: str) -> dict:
    from agents.client_agent import remove_accounts

    v = vendor(slug)
    if not v:
        return {"success": False, "errors": [f"unknown automation vendor: {slug}"]}
    removed = remove_accounts(client_id, [slug])
    log_step(AGENT_NAME, "credentials_removed", f"client {client_id}: {v['name']}")
    return {"success": True, "removed": removed}
