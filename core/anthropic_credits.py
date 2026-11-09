"""Anthropic credits — the owner's "how much Claude credit is left" widget
(admin only; never on a client surface). docs/ANTHROPIC-CREDITS.md.

No Anthropic call and no new key: the only numbers are
- the BALANCE the owner types after each top-up (table
  `anthropic_credit_balances`, migrations/2026-11-09-anthropic-credits.sql),
- our OWN measured Claude spend: the `client_costs` rows the two Claude
  choke points write (core/claude_json, cost_tracker.record_claude_response)
  — every row that carries token counts (`details.input_tokens`) or web
  searches. Their USD is `details.usd` (list prices; cost_tracker).

Estimated remaining = last balance − measured spend since it was entered.
Burn = the last 7 days' measured spend / 7. Days left = remaining / burn.
It is an ESTIMATE: list prices, no discounts/taxes, and any Claude use outside
this app (the Console, another key) is invisible. The Anthropic Console is
the source of truth — the widget says so.

Status: green / yellow (< yellow_days left) / red (< red_days left, or
nothing left), thresholds editable (app_settings `anthropic_credits:settings`).
ONE owner notice per crossing into a worse status (email to ADMIN_EMAIL + an
alerts row); the level is remembered per balance entry
(`anthropic_credits:alert`), so a new top-up starts a new cycle and a
recovery re-arms it. Warn only — nothing here stops or slows any agent.

Every balance entry and threshold change is an audit row (alerts source
`credits`, protected like the other audit sources in core/bulk_delete).
"""
import os
from datetime import datetime, timedelta, timezone

SERVICE_NAME = "anthropic_credits"
TABLE = "anthropic_credit_balances"
SETTINGS_KEY = "anthropic_credits:settings"
ALERT_KEY = "anthropic_credits:alert"
AUDIT_SOURCE = "credits"
ALERT_SOURCE = "anthropic_credits"
CONSOLE_BILLING_URL = "https://console.anthropic.com/settings/billing/"
NOTE_HE = ("הערכה לפי המדידה שלנו; הקונסולה של Anthropic היא מקור האמת. "
           "מומלץ להפעיל שם Auto reload.")

DEFAULT_SETTINGS = {"yellow_days": 7.0, "red_days": 2.0}
BURN_WINDOW_DAYS = 7
PAGE = 1000
MAX_PAGES = 200            # 200k cost rows per read — far beyond a real window
BALANCE_MAX_USD = 1_000_000
LEVELS = {"unknown": -1, "green": 0, "yellow": 1, "red": 2}

_db_instance = None


def _db():
    global _db_instance
    if _db_instance is None:
        from supabase import create_client
        _db_instance = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    return _db_instance


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value):
    try:
        out = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return out if out.tzinfo else out.replace(tzinfo=timezone.utc)
    except Exception:
        return None


class MigrationPending(RuntimeError):
    """The balances table isn't there yet (migration not run)."""


# ─── Measured spend (our own client_costs rows; read only) ───────────────────

def is_claude_row(row: dict) -> bool:
    """A cost row Anthropic bills: a Claude call (token counts) or the web
    search tool's per-search fee. Drive egress, Places, landing hosting and
    client-paid media are other vendors."""
    details = row.get("details") or {}
    return "input_tokens" in details or "output_tokens" in details or bool(details.get("web_searches"))


def _row_usd(row: dict) -> float:
    from core.cost_tracker import USD_TO_ILS
    details = row.get("details") or {}
    try:
        if "usd" in details:
            return float(details.get("usd") or 0)
        return float(row.get("amount") or 0) / USD_TO_ILS
    except (TypeError, ValueError):
        return 0.0


def claude_costs_since(since: datetime) -> tuple:
    """([(created_at datetime, usd)], truncated) for Anthropic-billed rows."""
    out, truncated = [], False
    for page in range(MAX_PAGES):
        rows = (_db().table("client_costs").select("created_at,amount,details")
                .gte("created_at", since.isoformat()).order("created_at")
                .range(page * PAGE, page * PAGE + PAGE - 1).execute().data or [])
        for row in rows:
            if is_claude_row(row):
                at = _parse(row.get("created_at"))
                if at:
                    out.append((at, _row_usd(row)))
        if len(rows) < PAGE:
            break
    else:
        truncated = True
    return out, truncated


# ─── Settings and the balance record ─────────────────────────────────────────

def _setting(key: str) -> dict:
    try:
        rows = _db().table("app_settings").select("value").eq("key", key).limit(1).execute().data or []
        value = rows[0].get("value") if rows else {}
        return value if isinstance(value, dict) else {}
    except Exception as e:
        print(f"[{SERVICE_NAME}] {key} unreadable ({type(e).__name__})")
        return {}


def _store_setting(key: str, value: dict):
    _db().table("app_settings").upsert({"key": key, "value": value, "updated_at": _now().isoformat()},
                                       on_conflict="key").execute()


def settings() -> dict:
    stored = _setting(SETTINGS_KEY)
    out = dict(DEFAULT_SETTINGS)
    for key in out:
        try:
            if stored.get(key) is not None:
                out[key] = float(stored[key])
        except (TypeError, ValueError):
            pass
    return out


def latest_balance() -> dict:
    """The last balance entered, or {} — MigrationPending when the table is missing."""
    try:
        rows = (_db().table(TABLE).select("id,balance_usd,entered_at,note")
                .order("entered_at", desc=True).limit(1).execute().data or [])
    except Exception as e:
        raise MigrationPending(f"{TABLE} unreadable ({type(e).__name__})")
    return rows[0] if rows else {}


def history(limit: int = 10) -> list:
    try:
        return (_db().table(TABLE).select("id,balance_usd,entered_at,note")
                .order("entered_at", desc=True).limit(int(limit)).execute().data or [])
    except Exception:
        return []


def _audit(line: str):
    """One audit row; raises when it cannot be written (no audit → no change)."""
    _db().table("alerts").insert({"source": AUDIT_SOURCE, "issues": [line], "status": "resolved",
                                  "resolved_at": _now().isoformat()}).execute()


def record_balance(amount_usd, note: str = "", who: str = "admin") -> dict:
    """The owner's "balance after top-up". The timestamp is the server's."""
    try:
        amount = round(float(amount_usd), 2)
    except (TypeError, ValueError):
        raise ValueError("amount must be a number")
    if not 0 <= amount <= BALANCE_MAX_USD:
        raise ValueError(f"amount must be between 0 and {BALANCE_MAX_USD}")
    note = " ".join(str(note or "").split())[:200]
    previous = latest_balance()
    rows = _db().table(TABLE).insert({"balance_usd": amount, "note": note or None,
                                      "entered_by": str(who)[:80]}).execute().data or []
    if not rows:
        raise RuntimeError("balance not stored")
    row = rows[0]
    try:
        _audit(f"credits balance set to ${amount:,.2f} by {who} at {_now().isoformat()}"
               + (f" (was ${float(previous['balance_usd']):,.2f} on {str(previous.get('entered_at'))[:16]})"
                  if previous else "") + (f" — {note}" if note else ""))
    except Exception as e:
        # The entry is the record; a missing audit line is said loudly, not hidden
        print(f"[{SERVICE_NAME}] ADMIN: audit row for balance {row.get('id')} not written ({type(e).__name__})")
    return row


def set_thresholds(yellow_days, red_days, who: str = "admin") -> dict:
    try:
        yellow, red = float(yellow_days), float(red_days)
    except (TypeError, ValueError):
        raise ValueError("thresholds must be numbers")
    if not (0 < red < yellow <= 90):
        raise ValueError("need 0 < red < yellow <= 90 days")
    before = settings()
    value = {"yellow_days": round(yellow, 2), "red_days": round(red, 2)}
    _audit(f"credits thresholds set to yellow<{value['yellow_days']}d red<{value['red_days']}d by {who} "
           f"at {_now().isoformat()} (was yellow<{before['yellow_days']}d red<{before['red_days']}d)")
    _store_setting(SETTINGS_KEY, value)
    return value


# ─── The estimate ────────────────────────────────────────────────────────────

def status_for(remaining, days_left, cfg: dict) -> str:
    if remaining is None:
        return "unknown"
    if remaining <= 0:
        return "red"
    if days_left is None:
        return "green"            # nothing measured in the last week: no burn
    if days_left < cfg["red_days"]:
        return "red"
    if days_left < cfg["yellow_days"]:
        return "yellow"
    return "green"


def estimate(now: datetime = None) -> dict:
    """The widget's numbers. Never raises for a missing table: `migration_pending`."""
    now = now or _now()
    cfg = settings()
    out = {"console_url": CONSOLE_BILLING_URL, "note": NOTE_HE, "thresholds": cfg,
           "currency": "USD", "as_of": now.isoformat(), "migration_pending": False}
    try:
        balance = latest_balance()
    except MigrationPending:
        balance, out["migration_pending"] = {}, True
    entered = _parse(balance.get("entered_at")) if balance else None
    week_ago = now - timedelta(days=BURN_WINDOW_DAYS)
    since = min(entered, week_ago) if entered else week_ago
    costs, truncated = claude_costs_since(since)

    def total(after):
        return round(sum(usd for at, usd in costs if at >= after), 4)

    spend_24h, spend_7d = total(now - timedelta(days=1)), total(week_ago)
    burn = round(spend_7d / BURN_WINDOW_DAYS, 4)
    out.update({"spend_24h_usd": spend_24h, "spend_7d_usd": spend_7d, "daily_burn_usd": burn,
                "truncated": truncated})
    if balance:
        spent = total(entered)
        remaining = round(float(balance.get("balance_usd") or 0) - spent, 2)
        days_left = round(remaining / burn, 1) if burn > 0 and remaining > 0 else (0.0 if remaining <= 0 else None)
        out.update({"balance": {"id": balance.get("id"), "usd": float(balance.get("balance_usd") or 0),
                                "entered_at": balance.get("entered_at"), "note": balance.get("note") or ""},
                    "spent_since_balance_usd": spent, "remaining_usd": remaining, "days_left": days_left})
    else:
        out.update({"balance": None, "spent_since_balance_usd": None, "remaining_usd": None, "days_left": None})
    out["status"] = status_for(out["remaining_usd"], out["days_left"], cfg)
    return out


# ─── One notice per crossing (warn only) ─────────────────────────────────────

def _notify(view: dict) -> bool:
    status = view["status"]
    label = "אדום" if status == "red" else "צהוב"
    days = view.get("days_left")
    line = (f"קרדיט Claude במצב {label}: נשארו כ-${view['remaining_usd']:,.2f} "
            f"({'אזל' if not days else f'כ-{days} ימים'} לפי קצב של ${view['daily_burn_usd']:,.2f} ליום). "
            f"להטענה: {CONSOLE_BILLING_URL} — הערכה לפי המדידה שלנו; הקונסולה היא מקור האמת. "
            f"שום סוכן לא נעצר.")
    from agents.master_agent import alert
    alert(ALERT_SOURCE, [line])
    try:
        from core import email_service
        return bool(email_service.send_owner_notice(f"⚠️ קרדיט Claude — {label}", line))
    except Exception as e:
        print(f"[{SERVICE_NAME}] owner email failed ({type(e).__name__})")
        return False


def check(now: datetime = None) -> dict:
    """Estimate + at most ONE notice when the status got worse than the level
    already announced for this balance entry. Never stops anything."""
    view = estimate(now)
    if view["status"] == "unknown":
        view["notified"] = False
        return view
    state = _setting(ALERT_KEY)
    balance_id = (view.get("balance") or {}).get("id")
    announced = int(state.get("level", 0)) if state.get("balance_id") == balance_id else 0
    level = LEVELS[view["status"]]
    view["notified"] = False
    if level > announced:
        _notify(view)
        view["notified"] = True
    if level != announced or state.get("balance_id") != balance_id:
        # Worse → announced; better (top-up, slower burn, thresholds) → re-armed
        try:
            _store_setting(ALERT_KEY, {"balance_id": balance_id, "level": level, "status": view["status"],
                                       "at": view["as_of"]})
        except Exception as e:
            print(f"[{SERVICE_NAME}] alert state not stored ({type(e).__name__})")
    return view
