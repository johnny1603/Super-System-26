"""Per-asset usage and the owner's rejections, fed back into selection
(2026-11-24).

Why: without it the media agent could pick the same photo for five posts in
a row, and nothing was learned when the owner sent back a post because of
the picture the agent chose.

- USAGE: every time the media agent uses a real asset (agents/media_agent
  `_use_real_asset`) → count + last used. find_assets ranks a recently and
  often used asset LOWER — never excludes it: a strong fit may be reused.
- REJECTIONS: when the owner sends back a post and marks the picture as the
  problem (the Social tab's rejection form: one click on a reason, or one
  line of their own), the rejection is recorded against that asset with the
  short reason. A rejected asset ranks lower for this client.
- REASONS: the accumulated reasons (with what the rejected asset showed) go
  to the agent's choice step, so the pattern informs the next pick.

Ranking only ever SUBTRACTS a bounded penalty from the match score
(USE_PENALTY_MAX, REJECT_PENALTY_MAX); an asset with no history is ranked
exactly as before.

Storage: app_settings `asset_feedback:<client_id>` (its own row, so the
indexing job's saves of `drive_assets:<id>` never race with it):
{"usage": {file_id: {"count", "last_used_at"}},
 "rejections": {file_id: [{"at", "reason", "item_id"}]},
 "reasons": [{"at", "file_id", "reason", "asset"}]   newest last, ≤ MAX_REASONS}
Cleared with the client (client_deletion, deletion_guard, admin_service).
"""
import re
from datetime import datetime, timezone

SERVICE_NAME = "asset_feedback"
PREFIX = "asset_feedback:"
MAX_REASONS = 40
MAX_PER_ASSET = 10
REASON_MAX = 160
RECENT_DAYS = 21.0            # a use stops counting as "recent" after this
USE_PENALTY_MAX = 1.2
REJECT_PENALTY_MAX = 1.5
REJECT_PENALTY_EACH = 0.75

def _db():
    from agents.client_agent import _db as db
    return db()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _key(client_id: int) -> str:
    return f"{PREFIX}{int(client_id)}"


def _empty() -> dict:
    return {"usage": {}, "rejections": {}, "reasons": []}


def load(client_id: int) -> dict:
    """Never raises: an unreadable row = no history (ranking as before)."""
    try:
        rows = (_db().table("app_settings").select("value").eq("key", _key(client_id)).limit(1)
                .execute().data) or []
        value = rows[0].get("value") if rows else None
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: unreadable ({type(e).__name__})")
        value = None
    out = _empty()
    if isinstance(value, dict):
        for k in out:
            if isinstance(value.get(k), type(out[k])):
                out[k] = value[k]
    return out


def _save(client_id: int, value: dict):
    _db().table("app_settings").upsert({"key": _key(client_id), "value": value, "updated_at": _now().isoformat()},
                                       on_conflict="key").execute()


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:REASON_MAX]


def record_use(client_id: int, file_id: str) -> dict:
    """The media agent used this real asset in a post. Never raises."""
    file_id = str(file_id or "")
    if not file_id:
        return {}
    try:
        data = load(client_id)
        u = data["usage"].get(file_id) or {}
        u = {"count": int(u.get("count") or 0) + 1, "last_used_at": _now().isoformat()}
        data["usage"][file_id] = u
        _save(client_id, data)
        return u
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: usage not recorded ({type(e).__name__})")
        return {}


def record_rejection(client_id: int, file_id: str, reason: str = "", item_id: str = "",
                     asset: str = "") -> dict:
    """The owner rejected the asset the agent chose (optional short reason;
    `asset` = what it shows, for the agent's next choice). Never raises."""
    file_id = str(file_id or "")
    if not file_id:
        return {}
    reason = _clean(reason)
    entry = {"at": _now().isoformat(), "reason": reason, "item_id": str(item_id or "")[:60]}
    try:
        data = load(client_id)
        rows = list(data["rejections"].get(file_id) or [])
        rows.append(entry)
        data["rejections"][file_id] = rows[-MAX_PER_ASSET:]
        data["reasons"] = (list(data["reasons"]) + [{"at": entry["at"], "file_id": file_id, "reason": reason,
                                                     "asset": _clean(asset)}])[-MAX_REASONS:]
        _save(client_id, data)
        return entry
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: rejection not recorded ({type(e).__name__})")
        return {}


def _days_since(iso: str, now: datetime) -> float:
    try:
        then = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return max(0.0, (now - then).total_seconds() / 86400.0)
    except Exception:
        return 1e9


def penalty(data: dict, file_id: str, now: datetime = None) -> float:
    """How much lower this asset ranks: recent + frequent use, and every
    rejection. 0 for an asset with no history. Bounded, never exclusion."""
    now = now or _now()
    u = (data.get("usage") or {}).get(file_id) or {}
    count = int(u.get("count") or 0)
    use = 0.0
    if count:
        recency = max(0.0, 1.0 - _days_since(u.get("last_used_at"), now) / RECENT_DAYS)
        use = min(USE_PENALTY_MAX, 0.8 * recency + 0.1 * min(count, 4))
    rej = min(REJECT_PENALTY_MAX, REJECT_PENALTY_EACH * len((data.get("rejections") or {}).get(file_id) or []))
    return round(use + rej, 4)


def annotate(data: dict, file_id: str) -> dict:
    """What a candidate carries to the agent's choice."""
    u = (data.get("usage") or {}).get(file_id) or {}
    rej = (data.get("rejections") or {}).get(file_id) or []
    return {"times_used": int(u.get("count") or 0), "last_used_at": u.get("last_used_at", ""),
            "times_rejected": len(rej),
            "rejection_reasons": [r.get("reason") for r in rej if r.get("reason")][-3:]}


def reasons_for_selection(client_id: int, data: dict = None, limit: int = 12) -> list:
    """The owner's accumulated rejection reasons, newest first:
    [{"reason", "asset", "at"}] — for the choice step."""
    data = data if data is not None else load(client_id)
    out = [{"reason": r.get("reason", ""), "asset": r.get("asset", ""), "at": r.get("at", "")}
           for r in reversed(data.get("reasons") or []) if r.get("reason") or r.get("asset")]
    return out[:limit]
