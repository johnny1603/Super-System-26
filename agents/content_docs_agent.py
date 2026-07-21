"""uallak's shared mechanism for delivering long-form content (full scripts,
detailed homework/instructions) that's too dense for the chat itself, as a
real Google Doc in the client's Drive — reusing the existing per-client
Drive root (media_agent._subfolder) and the existing pending-approval
pipeline (client_suggestions), never a parallel notification/consent system.

Flow:
1. deliver_doc() creates/finds content-docs/ under the client's Drive root
   (a peer of media_agent's images/videos/scripts folders), uploads the HTML
   as a NATIVE Google Doc (core.drive_service.upload_google_doc — Drive
   converts HTML into a real editable Doc, not a downloadable .txt), shares
   it with the client as a commenter (feedback/questions inline, never
   accidental edits to the content), inserts ONE client_suggestions row
   (kind='content_doc') so it appears in the EXISTING "ממתין לאישור שלך"
   dashboard area exactly like any other suggestion, and pushes a chat
   notification with the direct doc link.
2. The client acknowledges the SAME way they approve any suggestion — tap
   approve on the card, or through the support chat concierge (which already
   walks clients through any pending client_suggestions row generically, see
   support_agent.py's pending_suggestions handling — no changes needed there
   for this to work). client_suggestions.status flips to 'approved' with a
   real decided_at timestamp: that IS the acknowledgment, a real recorded
   confirmation never assumed, same principle as avatar_agent's consent gate
   — just modeled on the existing approval pipeline instead of a bespoke
   checkbox, per the explicit ask to keep this consistent with what's
   already built rather than adding a second system.
3. get_doc_status()/has_acknowledged()/list_delivered_docs() read that same
   state back for admin visibility - no separate tracking table.

Deliberately NOT wired into engagement_agent's _AUTO_FULFILL dispatch:
acknowledging a doc IS the terminal action here (unlike a media_plan
approval, which kicks off real generation) - there is nothing further to
auto-execute on approval.
"""
import os
import re
from datetime import datetime, timezone

from supabase import create_client as _supabase_client

from core import drive_service as drive
from core.agent_base import agent_alert, log_step, timed_step

AGENT_NAME = "content_docs_agent"

# The fixed client_suggestions.kind for every doc delivered this way - the
# dashboard/support-chat's existing generic per-kind handling picks it up
# with zero code changes on that side (see SUGG_KIND_KEYS client-side).
SUGGESTION_KIND = "content_doc"

_DEFAULT_BODY_HE = 'פתחו את המסמך, ואחרי שעברתם עליו אשרו כאן שקראתם.'

# Created lazily — no DB client at import time (api_server imports every agent at startup)
_db_instance = None


def _db():
    global _db_instance
    if _db_instance is None:
        _db_instance = _supabase_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"],
        )
    return _db_instance


def _log_activity(client_id: int, action_type: str, details: dict, result: dict = None):
    _db().table("client_activity").insert({
        "client_id": client_id, "agent_name": AGENT_NAME,
        "action_type": action_type, "details": details, "result": result or {},
    }).execute()


def deliver_doc(client_id: int, title: str, html_content: str,
                doc_kind: str = "content", body: str = "") -> dict:
    """Creates the Google Doc, shares it, opens the approval card, and pushes
    the chat notification. `doc_kind` is a free-form label for admin/activity
    context ("script", "homework", "instructions", ...) — NOT the
    client_suggestions kind, which is always SUGGESTION_KIND. `body` lets the
    caller write a specific ask; otherwise a generic Hebrew default is used
    (client-facing text stays Hebrew per house convention)."""
    title = (title or "").strip()
    if not title:
        return {"success": False, "errors": ["title is required"]}
    if not (html_content or "").strip():
        return {"success": False, "errors": ["html_content is required"]}

    from agents.media_agent import _subfolder
    from agents.client_agent import get_client, log_communication

    log_step(AGENT_NAME, "deliver_doc", f"client {client_id} [{doc_kind}]: {title[:80]}")
    try:
        folder = _subfolder(client_id, "content-docs")
        safe_name = re.sub(r'[\\/:*?"<>|]', "", title)[:80] or "מסמך"
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
        uploaded = timed_step(
            AGENT_NAME, "upload_doc",
            lambda: drive.upload_google_doc(folder, f"{safe_name} — {stamp}", html_content))
    except Exception as e:
        agent_alert(AGENT_NAME, [f"client {client_id}: content doc creation failed "
                                 f"('{title[:60]}'): {e}"])
        return {"success": False, "errors": [str(e)]}

    client = get_client(client_id)
    link = uploaded.get("webViewLink", "")
    if client.get("email"):
        # Commenter, not reader/writer: room for feedback/questions inline,
        # never an accidental edit to the actual content.
        drive.share_with_user(uploaded["id"], client["email"], role="commenter")

    _db().table("client_suggestions").insert({
        "client_id": client_id,
        "kind": SUGGESTION_KIND,
        "title": title,
        "body": (body or "").strip() or _DEFAULT_BODY_HE,
        "source": "content_docs",
        "context": {"doc_id": uploaded["id"], "link": link, "doc_kind": doc_kind},
        "status": "pending",
    }).execute()

    _log_activity(client_id, "content_doc_created",
                  {"title": title, "doc_kind": doc_kind, "doc_id": uploaded["id"]},
                  {"link": link})

    if client.get("email"):
        log_communication(client_id, "outbound", "dashboard_chat",
                          f'יש לכם מסמך חדש לעיון — "{title}" 📄\n{link}\n'
                          'אחרי שתעברו עליו, אשרו שראיתם באזור "ממתין לאישור שלך" בדשבורד.')

    log_step(AGENT_NAME, "deliver_doc", f"client {client_id}: doc {uploaded['id']} delivered")
    return {"success": True, "doc_id": uploaded["id"], "link": link}


def get_doc_status(client_id: int, doc_id: str) -> dict:
    """Real status of one delivered doc — 'pending' (not yet acknowledged) or
    the client's real approve/reject decision, with the real decided_at
    timestamp as the acknowledgment record (never assumed)."""
    rows = (_db().table("client_suggestions").select("status,decided_at,context")
            .eq("client_id", client_id).eq("kind", SUGGESTION_KIND)
            .execute().data or [])
    for row in rows:
        if (row.get("context") or {}).get("doc_id") == doc_id:
            acknowledged = row.get("status") == "approved"
            return {"found": True, "acknowledged": acknowledged,
                    "acknowledged_at": row.get("decided_at") if acknowledged else None,
                    "status": row.get("status")}
    return {"found": False, "acknowledged": False}


def has_acknowledged(client_id: int, doc_id: str) -> bool:
    return get_doc_status(client_id, doc_id).get("acknowledged", False)


def list_delivered_docs(client_id: int, limit: int = 50) -> list:
    """Every content doc ever delivered to this client, newest first — the
    admin drawer's visibility into what's pending vs acknowledged."""
    rows = (_db().table("client_suggestions").select("*")
            .eq("client_id", client_id).eq("kind", SUGGESTION_KIND)
            .order("created_at", desc=True).limit(limit).execute().data or [])
    return [{
        "id": r["id"], "title": r.get("title", ""),
        "doc_kind": (r.get("context") or {}).get("doc_kind", ""),
        "link": (r.get("context") or {}).get("link", ""),
        "status": r.get("status"), "created_at": r.get("created_at"),
        "acknowledged_at": r.get("decided_at") if r.get("status") == "approved" else None,
    } for r in rows]
