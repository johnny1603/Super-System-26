from datetime import datetime
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALERT_HISTORY_PATH = os.path.join(BASE_DIR, "data", "alert_history.json")


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


def alert(label: str, issues: list):
    ts = datetime.now().isoformat()
    print(f"[{ts}] ALERT [{label}] NOT APPROVED")
    for issue in issues:
        print(f"  - {issue}")
    _append_alert_history({"ts": ts, "source": "review", "label": label, "issues": issues})
