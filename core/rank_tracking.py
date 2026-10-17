"""Keyword rank tracking for the client Performance page's organic section.

## Providers

One interface, several sources. A provider answers
`get_positions(client_id, keywords, as_of=None)` with
{keyword: {"position": float | None, "date": "YYYY-MM-DD" | None}} and declares
`capabilities` = {exact_rank, history, competitors, clicks_impressions}.

Registered now, NONE of them connected:
- "seo_pro"        — uallak's ONE shared professional SEO vendor account
                     (owner-controlled; its cost is inside the fixed organic
                     price). Will read its key from SEO_PRO_API_KEY. Not
                     implemented: no vendor is called and no key is added.
- "search_console" — Google Search Console (average position, clicks and
                     impressions). DEFERRED: it needs a new Google OAuth scope,
                     and nothing new is requested until Google finishes
                     reviewing the current scope submission.
- "none"           — the explicit "no source" provider.

## Display rules (enforced here, not in the page)

- The best source per keyword wins, by PROVIDERS order; sources are NEVER
  averaged together.
- A source without exact_rank reports an AVERAGE position — the row says so
  ("average position"), it is never presented as an exact rank.
- Clients never see vendor names: a source is shown by its generic label.

## Target keywords

Stored today nowhere structured (seo_agent's strategy text and the cached
research hold free-text keyword themes). The proposed table is
migrations/2026-10-18-client-target-keywords.sql — NOT applied. Until it
exists every read degrades to "no keywords yet", never an error.

Soft cap: more than TARGET_KEYWORDS_SOFT_CAP keywords raises an ADMIN alert
only (deduped). It blocks nothing and is never shown to the client.
"""
import os

SERVICE_NAME = "rank_tracking"

TARGET_KEYWORDS_SOFT_CAP = int(os.environ.get("TARGET_KEYWORDS_SOFT_CAP", "30"))
SOFT_CAP_ALERT_DAYS = 7
TARGET_KEYWORDS_TABLE = "client_target_keywords"
DEFAULT_TARGET_POSITION = 1


class RankProvider:
    id = "none"
    # Generic, vendor-free label key (the page translates it)
    label_key = "source_none"
    capabilities = {"exact_rank": False, "history": False,
                    "competitors": False, "clicks_impressions": False}

    def is_connected(self, client_id: int) -> bool:
        return False

    def get_positions(self, client_id: int, keywords: list, as_of: str = None) -> dict:
        return {k: {"position": None, "date": None} for k in keywords}


class SeoProProvider(RankProvider):
    """The shared uallak vendor account. Reads its key from ENV_KEY once
    implemented; until then it is never connected and never calls out."""
    id = "seo_pro"
    label_key = "source_pro"
    ENV_KEY = "SEO_PRO_API_KEY"
    capabilities = {"exact_rank": True, "history": True,
                    "competitors": True, "clicks_impressions": False}

    def is_connected(self, client_id: int) -> bool:
        return False  # not implemented — no vendor call exists yet

    def get_positions(self, client_id: int, keywords: list, as_of: str = None) -> dict:
        raise NotImplementedError("seo_pro rank provider is not implemented yet")


class SearchConsoleProvider(RankProvider):
    """Deferred: needs a new Google OAuth scope (not requested)."""
    id = "search_console"
    label_key = "source_search_console"
    capabilities = {"exact_rank": False, "history": True,
                    "competitors": False, "clicks_impressions": True}

    def is_connected(self, client_id: int) -> bool:
        return False

    def get_positions(self, client_id: int, keywords: list, as_of: str = None) -> dict:
        raise NotImplementedError("search_console rank provider is deferred")


# Preference order = "best source" order
PROVIDERS = [SeoProProvider(), SearchConsoleProvider(), RankProvider()]


def get_target_keywords(client_id: int) -> dict:
    """{"available": bool, "keywords": [{"keyword", "target_position"}]}.
    available=False means the table isn't there yet (proposed migration)."""
    try:
        from agents.client_agent import _db
        rows = (_db().table(TARGET_KEYWORDS_TABLE).select("keyword,target_position,created_at")
                .eq("client_id", client_id).order("created_at").execute().data or [])
    except Exception as e:
        print(f"[{SERVICE_NAME}] target keywords unreadable for client {client_id} ({e})")
        return {"available": False, "keywords": []}
    return {"available": True, "keywords": [
        {"keyword": r.get("keyword", ""),
         "target_position": r.get("target_position") or DEFAULT_TARGET_POSITION}
        for r in rows if (r.get("keyword") or "").strip()]}


def _check_soft_cap(client_id: int, count: int):
    if count <= TARGET_KEYWORDS_SOFT_CAP:
        return
    from core.chat_fallback import alert_once
    alert_once(SERVICE_NAME, client_id, "target_keywords_soft_cap",
               f"client {client_id}: {count} target keywords — above the internal soft cap of "
               f"{TARGET_KEYWORDS_SOFT_CAP}. Nothing was blocked; review the scope.",
               SOFT_CAP_ALERT_DAYS)


def _status(position, target, previous) -> str:
    if position is None:
        return "no_data"
    if position <= target:
        return "reached"
    if previous is not None and position < previous:
        return "improving"
    return "not_yet"


def organic_table(client_id: int) -> dict:
    """The organic section's whole data model. Never raises."""
    from datetime import date, timedelta
    targets = get_target_keywords(client_id)
    keywords = targets["keywords"]
    _check_soft_cap(client_id, len(keywords))

    connected = []
    for provider in PROVIDERS:
        try:
            if provider.id != "none" and provider.is_connected(client_id):
                connected.append(provider)
        except Exception as e:
            print(f"[{SERVICE_NAME}] {provider.id}.is_connected failed for client {client_id}: {e}")

    # The soft cap is deliberately absent from this dict — it never reaches a client
    base = {"keywords_available": targets["available"], "tracking_connected": bool(connected)}
    if not connected:
        base["rows"] = [{"keyword": k["keyword"], "target_position": k["target_position"],
                         "current_position": None, "change_30d": None, "status": "no_data",
                         "source": None, "position_kind": None} for k in keywords]
        base["summary"] = {"at_target": 0, "total": len(keywords)}
        return base

    words = [k["keyword"] for k in keywords]
    month_ago = (date.today() - timedelta(days=30)).isoformat()
    readings = {}
    for provider in connected:
        try:
            now = provider.get_positions(client_id, words)
            then = (provider.get_positions(client_id, words, as_of=month_ago)
                    if provider.capabilities.get("history") else {})
            readings[provider.id] = (provider, now, then)
        except Exception as e:
            print(f"[{SERVICE_NAME}] {provider.id}.get_positions failed for client {client_id}: {e}")

    rows, at_target = [], 0
    for k in keywords:
        chosen = None
        for provider in connected:   # PROVIDERS order = best source first
            reading = readings.get(provider.id)
            if reading and (reading[1].get(k["keyword"]) or {}).get("position") is not None:
                chosen = reading
                break
        if not chosen:
            rows.append({"keyword": k["keyword"], "target_position": k["target_position"],
                         "current_position": None, "change_30d": None, "status": "no_data",
                         "source": None, "position_kind": None, "date": None})
            continue
        provider, now, then = chosen
        current = now[k["keyword"]]
        previous = (then.get(k["keyword"]) or {}).get("position")
        status = _status(current["position"], k["target_position"], previous)
        at_target += status == "reached"
        rows.append({
            "keyword": k["keyword"], "target_position": k["target_position"],
            "current_position": current["position"], "date": current.get("date"),
            # Positive = moved UP (a smaller position number)
            "change_30d": (round(previous - current["position"], 1) if previous is not None else None),
            "status": status, "source": provider.label_key,
            "position_kind": "exact" if provider.capabilities.get("exact_rank") else "average",
        })
    base["rows"] = rows
    base["summary"] = {"at_target": at_target, "total": len(keywords)}
    return base
