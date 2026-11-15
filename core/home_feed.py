"""The home idea feed, a few at a time (2026-11-16, docs/HOME-FOCUS.md).

A client never gets a wall of items: at most `ACTIVE_MAX` pending ideas are
shown, ordered by importance and timing, and the rest wait. Deciding one (approve
or reject) opens the next one on the following load, because the order is
recomputed from the pending rows every time. Nothing is stored and nothing is
hidden for good.

Order:
1. a calendar item whose event is near (a promotion for an event in 12 days
   comes before one in 30). A "sensitive" day (tone down) comes first of all;
2. a performance tweak (grounded in the client's own numbers);
3. a media plan or content document (something ready to produce);
4. a content idea;
5. homework.
Ties: the newest first.
"""
ACTIVE_MAX = 3

_KIND_RANK = {"campaign_tweak": 2, "media_plan": 3, "content_doc": 3, "content_idea": 4, "homework": 5}


def _events() -> dict:
    try:
        from core import israel_calendar
        return {e["slug"]: e for e in israel_calendar.upcoming_events()}
    except Exception as e:
        print(f"[home_feed] calendar unavailable ({type(e).__name__}): ordering by kind only")
        return {}


def _key(item: dict, events: dict):
    slug = str(((item.get("context") or {}).get("event_slug")) or "")
    event = events.get(slug) if slug else None
    if event:
        if event.get("kind") == "sensitive":
            return (0, -1, "")
        return (1, int(event.get("days_until", 999)), "")
    rank = 1 if item.get("kind") == "promotion" else _KIND_RANK.get(item.get("kind"), 4)
    # A promotion with no event we can date still comes before the generic ideas
    return (rank, 999, "")


def order(items: list, events: dict = None) -> list:
    """The pending items, most important and most urgent first."""
    events = _events() if events is None else events
    newest_first = sorted(items or [], key=lambda s: str(s.get("created_at") or ""), reverse=True)
    return sorted(newest_first, key=lambda s: _key(s, events)[:2])


def release(items: list, events: dict = None) -> dict:
    """{"active": the ≤ ACTIVE_MAX shown now, "waiting": how many wait}."""
    ordered = order(items, events)
    return {"active": ordered[:ACTIVE_MAX], "waiting": max(0, len(ordered) - ACTIVE_MAX)}
