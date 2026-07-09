from datetime import datetime
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALERT_HISTORY_PATH = os.path.join(BASE_DIR, "data", "alert_history.json")

# Created lazily - no DB client at import time
_db_instance = None


def _db():
    global _db_instance
    if _db_instance is None:
        from supabase import create_client
        _db_instance = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    return _db_instance


def _append_alert_history(entry: dict):
    os.makedirs(os.path.dirname(ALERT_HISTORY_PATH), exist_ok=True)
    try:
        with open(ALERT_HISTORY_PATH, "r", encoding="utf-8") as f:
            history = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        history = []
    history.append(entry)
    with open(ALERT_HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history[-500:], f, ensure_ascii=False, indent=2)


def _persist_alert(label: str, issues: list):
    """Durable copy in Supabase - the local file is wiped on every deploy, and
    the admin dashboard reads alert history from here. alert() is called from
    error paths, so a DB failure must never raise out of this function."""
    try:
        _db().table("alerts").insert({
            "source": label,
            "issues": issues,
            "status": "open",
        }).execute()
    except Exception as e:
        print(f"[master_agent] could not persist alert to Supabase (non-fatal): {e}")


def alert(label: str, issues: list):
    ts = datetime.now().isoformat()
    print(f"[{ts}] ALERT [{label}] NOT APPROVED")
    for issue in issues:
        print(f"  - {issue}")
    # Local file kept for monitor_agent's deep scan; Supabase is the durable copy
    _append_alert_history({"ts": ts, "source": "review", "label": label, "issues": issues})
    _persist_alert(label, issues)
