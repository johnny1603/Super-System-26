"""The client-paid creative tools — each one its OWN connection.

ElevenLabs (the client's voice clone + speech in it) and Higgsfield
(generated images / video, AND the presenter — the client's character
speaking an audio track, core/avatar_video). Every tool is independent:

- its own key (one `client_accounts` row per tool), its own Connect, Test
  connection, status and Disconnect (which deletes that tool's row and
  nothing else);
- its own consent where the law of likeness needs one: `likeness` sits on
  the Higgsfield card (optional there — it is required only before a
  presenter is made from the client's OWN photos; AI images need none),
  `voice` (cloning) on the ElevenLabs card, also optional — without it a
  recording can still be used as a plain audio track, never cloned
  (agents/avatar_agent.has_consent);
- its own help entry (core/connection_help, same id).

HEYGEN IS RETIRED (2026-10-31): it is in RETIRED_TOOLS, not TOOLS — never
offered, no card, no Connect, no help entry. A stored row (internal test
clients only) is left intact; ownership and Disconnect still see it.

STATUS per tool: connected | connected_unverified (stored; the vendor has no
key-check we can call, or it could not be reached — the first real job is
the check) | key_rejected (the vendor said the key is invalid — at Connect,
at Test, or during a real job) | not_connected.

Key health and the client's default video provider live in ONE app_settings
row per client, `creative_tools:<id>` (cleared by client_deletion, guarded by
deletion_guard). Never a key in it: only status, time, and the last 4
characters already shown on the card.

A feature that needs several tools asks `missing()` and shows
`missing_message()` — plain Hebrew naming exactly which tool is missing,
with a button that opens only that tool's card (`focus_id`).

Keys are pasted ONLY into the tool's card. They never pass through chat
(core/chat_guard refuses key-looking messages) and never reach a log.

NAMES: `label` is the vendor's name — code, logs, admin UI and cost rows
only. Everything a CLIENT reads uses `client_label` (capability language:
"your voice", "your video presenter — filmed likeness of you" / "— an AI
presenter"). The vendor is named to a client only inside the connection help
flow, where they must open an account there (core/connection_help).
"""
import re
from datetime import datetime, timezone

import httpx

RECORD_PREFIX = "creative_tools:"


def _t(he, en, fr, ar, ru) -> dict:
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


TOOLS = {
    "elevenlabs": {"label": "ElevenLabs", "platform": "elevenlabs", "help_id": "elevenlabs",
                   "client_label": _t("הקול שלך", "Your voice", "Votre voix", "صوتك", "Ваш голос"),
                   "what_he": "שיבוט הקול שלך והקראה בקול שלך",
                   "consent": "voice", "consent_required": False},
    "higgsfield": {"label": "Higgsfield", "platform": "higgsfield", "help_id": "higgsfield",
                   "client_label": _t("הדמות שבסרטונים ותמונות AI",
                                      "Your video presenter and AI images",
                                      "Votre présentateur vidéo et images IA",
                                      "الشخصية في الفيديوهات وصور الذكاء الاصطناعي",
                                      "Ведущий ваших видео и ИИ-изображения"),
                   "what_he": "הדמות שמדברת בסרטונים שלך (מתמונה שלך או דמות AI), ותמונות וסרטונים שנוצרים במחשב",
                   "consent": "likeness", "consent_required": False},
}

# Retired tools: never offered (no card, no Connect, no help entry), but a
# client's stored row stays and Disconnect/ownership still reach it.
RETIRED_TOOLS = {
    "heygen": {"label": "HeyGen", "platform": "heygen", "retired_on": "2026-10-31",
               "client_label": _t("כלי הדמות המצולמת הקודם", "The previous filmed-presenter tool",
                                  "L'ancien outil de présentateur filmé", "أداة الشخصية المصوَّرة السابقة",
                                  "Прежний инструмент снятого ведущего")},
}


def client_label(tool: str, lang: str = "he") -> str:
    """What the client reads for a tool — never the vendor's name."""
    labels = (TOOLS.get(tool) or RETIRED_TOOLS.get(tool) or {}).get("client_label") or {}
    return labels.get(lang) or labels.get("he") or ""


_VENDOR_RE = re.compile(r"\b(heygen|eleven\s?labs|higgsfield)(?:'s)?\b", re.IGNORECASE)
# A reply that still says "HeyGen" is talking about the presenter — which is Higgsfield now
_VENDOR_TOOL = {"heygen": "higgsfield", "elevenlabs": "elevenlabs", "higgsfield": "higgsfield"}


def scrub_vendor_names(text: str, lang: str = "he") -> str:
    """The code-level backstop for a chat reply: a vendor name the model wrote
    anyway becomes the tool's client label. Not applied to the connection
    help flow for these tools — there the vendor must be named (the client
    opens an account there)."""
    def swap(match):
        tool = _VENDOR_TOOL.get(re.sub(r"\s", "", match.group(1).lower()), "")
        return client_label(tool, lang) or match.group(0)
    return _VENDOR_RE.sub(swap, text or "")
CONNECTED = ("connected", "connected_unverified")
PROBE_TIMEOUT = 15


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db():
    from agents.client_agent import _db as client_db
    return client_db()


# ─── The per-client record (no secrets) ──────────────────────────────────────

def _record(client_id: int) -> dict:
    try:
        rows = (_db().table("app_settings").select("value")
                .eq("key", f"{RECORD_PREFIX}{int(client_id)}").limit(1).execute().data or [])
        value = rows[0].get("value") if rows else {}
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _save(client_id: int, record: dict):
    try:
        _db().table("app_settings").upsert(
            {"key": f"{RECORD_PREFIX}{int(client_id)}", "value": record, "updated_at": _now()},
            on_conflict="key").execute()
    except Exception as e:
        print(f"[vendor_tools] record not stored for client {client_id}: {type(e).__name__}")


def _set_health(client_id: int, tool: str, state: str):
    record = _record(client_id)
    health = dict(record.get("health") or {})
    if state:
        health[tool] = {"state": state, "checked_at": _now()}
    else:
        health.pop(tool, None)
    record["health"] = health
    _save(client_id, record)


def mark_rejected(client_id: int, tool: str):
    """A real job got "invalid key" from the vendor: the card says so."""
    if tool in TOOLS:
        _set_health(client_id, tool, "key_rejected")


# ─── Key checks (one cheap, read-only call per vendor) ───────────────────────

def _probe(url: str, headers: dict, rejected_ok_statuses=()) -> str:
    """ok | rejected | unknown. Never logs the key or the response body."""
    try:
        response = httpx.get(url, headers=headers, timeout=PROBE_TIMEOUT)
    except Exception:
        return "unknown"
    if response.status_code == 200:
        return "ok"
    if response.status_code in (401, 403):
        try:
            detail = (response.json() or {}).get("detail") or {}
            status = detail.get("status", "") if isinstance(detail, dict) else ""
        except Exception:
            status = ""
        # A restricted key that simply lacks THIS permission is still a real key
        return "ok" if status in rejected_ok_statuses else "rejected"
    return "unknown"


def _check_elevenlabs(key: str) -> str:
    from core import elevenlabs_service as el
    return _probe(f"{el.API_BASE}/v1/user", {"xi-api-key": key},
                  rejected_ok_statuses=("missing_permissions",))


# Higgsfield publishes no key-check endpoint we could verify: a stored key is
# "connected_unverified" until a real generation succeeds or is refused.
CHECKS = {"elevenlabs": _check_elevenlabs}


# ─── Status ──────────────────────────────────────────────────────────────────

def _accounts(client_id: int) -> dict:
    from agents.client_agent import get_accounts
    return {a.get("platform"): a for a in get_accounts(client_id) if a.get("status") == "active"}


def _consent(client_id: int, scope):
    if not scope:
        return None
    try:
        from agents.avatar_agent import has_consent
        return bool(has_consent(client_id, scope))
    except Exception:
        return None


def status(client_id: int, tool: str, accounts: dict = None, record: dict = None) -> str:
    if tool not in TOOLS:
        return "not_connected"   # unknown or retired: nothing can run on it
    accounts = _accounts(client_id) if accounts is None else accounts
    row = accounts.get(TOOLS[tool]["platform"])
    if not row or not row.get("has_secret"):
        return "not_connected"
    health = ((record if record is not None else _record(client_id)).get("health") or {}).get(tool) or {}
    if health.get("state") == "key_rejected":
        return "key_rejected"
    if health.get("state") == "ok":
        return "connected"
    # Rows from before this module (no health yet) or never checkable
    return "connected_unverified"


def statuses(client_id: int) -> dict:
    accounts, record = _accounts(client_id), _record(client_id)
    out = {}
    for tool, spec in TOOLS.items():
        row = accounts.get(spec["platform"]) or {}
        health = (record.get("health") or {}).get(tool) or {}
        out[tool] = {"tool": tool, "label": spec["label"], "client_label": client_label(tool),
                     "what_he": spec["what_he"],
                     # No part of the key, not even its last 4 (core/ownership)
                     "status": status(client_id, tool, accounts, record),
                     "checked_at": health.get("checked_at"),
                     "can_test": tool in CHECKS,
                     "consent_scope": spec["consent"], "consent_required": spec["consent_required"],
                     "consent": _consent(client_id, spec["consent"]),
                     "help_id": spec["help_id"]}
    return out


def is_connected(client_id: int, tool: str) -> bool:
    return status(client_id, tool) in CONNECTED


def missing(client_id: int, tools: list) -> list:
    """The tools in `tools` this client cannot use right now (in order)."""
    return [t for t in tools if not is_connected(client_id, t)]


def missing_message(tools: list) -> str:
    names = [f"\"{client_label(t)}\"" for t in tools if t in TOOLS]
    if not names:
        return ""
    if len(names) == 1:
        return f"כדי לעשות את זה צריך קודם לחבר את {names[0]}. לוחצים על הכפתור, ונעבור על זה יחד צעד אחר צעד."
    return (f"כדי לעשות את זה צריך קודם לחבר את {', את '.join(names[:-1])} ואת {names[-1]}. "
            f"כל אחד מתחבר בכרטיס משלו, עם הדרכה צעד אחר צעד.")


def focus_id(tool: str) -> str:
    """The dashboard element of that tool's own card (and nothing else)."""
    return f"toolCard-{tool}"


def missing_block(tools: list) -> dict:
    """What a caller returns when tools are missing: the code, the plain
    message and one button per missing tool."""
    return {"success": False, "code": "missing_tool", "missing": list(tools),
            "message_he": missing_message(tools),
            "buttons": [{"tool": t, "label": f"לחיבור \"{client_label(t)}\"", "focus": focus_id(t)}
                        for t in tools if t in TOOLS]}


# ─── Connect / test / disconnect ─────────────────────────────────────────────

class ToolError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _log(client_id: int, action: str, tool: str, extra: dict = None):
    try:
        _db().table("client_activity").insert({
            "client_id": int(client_id), "agent_name": "vendor_tools", "action_type": action,
            "details": {"tool": tool, **(extra or {})}, "result": {}}).execute()
    except Exception as e:
        print(f"[vendor_tools] activity not logged for client {client_id}: {type(e).__name__}")


def connect(client_id: int, tool: str, api_key: str, consent: bool = False) -> dict:
    """Check the key with the vendor FIRST (a key the vendor rejects is never
    stored), then store it on this tool's own row. Raises ToolError."""
    if tool not in TOOLS:
        raise ToolError("unknown_tool")
    spec = TOOLS[tool]
    key = (api_key or "").strip()
    if not key or len(key) > 400 or any(c.isspace() for c in key):
        raise ToolError("empty_key")
    if spec["consent_required"] and not consent and not _consent(client_id, spec["consent"]):
        raise ToolError("consent_required")
    checked = CHECKS[tool](key) if tool in CHECKS else "unknown"
    if checked == "rejected":
        raise ToolError("key_rejected")
    from agents.client_agent import upsert_account
    upsert_account(client_id, spec["platform"], spec["platform"], key, "active")
    if consent and spec["consent"]:
        from agents.avatar_agent import record_consent
        record_consent(client_id, spec["consent"])
    _set_health(client_id, tool, "ok" if checked == "ok" else "unverified")
    _log(client_id, "creative_tool_connected", tool, {"verified": checked == "ok"})
    return {"tool": tool, "status": "connected" if checked == "ok" else "connected_unverified"}


def test(client_id: int, tool: str) -> dict:
    """Re-check the STORED key with the vendor; the card shows the result."""
    if tool not in TOOLS:
        raise ToolError("unknown_tool")
    from core.secrets_vault import use_vendor_key
    key = use_vendor_key(client_id, TOOLS[tool]["platform"], job=f"vendor_tools:test:{tool}")
    if not key:
        raise ToolError("not_connected")
    if tool not in CHECKS:
        return {"tool": tool, "status": "connected_unverified", "checkable": False}
    checked = CHECKS[tool](key)
    if checked == "unknown":
        return {"tool": tool, "status": status(client_id, tool), "reachable": False}
    _set_health(client_id, tool, "ok" if checked == "ok" else "key_rejected")
    return {"tool": tool, "status": "connected" if checked == "ok" else "key_rejected"}


def disconnect(client_id: int, tool: str) -> dict:
    """Deletes THIS tool's stored key and health — nothing else (the other
    tools, consents, recordings and videos stay). The key itself lives on the
    client's own vendor account, where they can revoke it too. A retired
    tool's stored row can still be disconnected."""
    spec = TOOLS.get(tool) or RETIRED_TOOLS.get(tool)
    if not spec:
        raise ToolError("unknown_tool")
    from agents.client_agent import remove_accounts
    removed = remove_accounts(client_id, [spec["platform"]])
    _set_health(client_id, tool, "")
    if removed:
        _log(client_id, "creative_tool_disconnected", tool)
    return {"tool": tool, "removed": removed}


# ─── Video provider choice (per client; a video may override it) ─────────────

def default_provider(client_id: int) -> str:
    """The presenter provider. There is ONE since HeyGen was retired
    (2026-10-31): a stored "heygen" choice (internal test clients) is simply
    no longer honoured — the record is left as it is."""
    from core.avatar_video import PROVIDERS, DEFAULT_PROVIDER
    chosen = _record(client_id).get("avatar_provider")
    return chosen if chosen in PROVIDERS else DEFAULT_PROVIDER


def set_default_provider(client_id: int, provider: str) -> str:
    from core.avatar_video import PROVIDERS
    if provider not in PROVIDERS:
        raise ToolError("unknown_provider")
    record = _record(client_id)
    record["avatar_provider"] = provider
    _save(client_id, record)
    return provider
