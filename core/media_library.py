"""The client Media tab's library view (2026-11-11, docs/MEDIA-TAB.md).

One read for the tab: the client's WHOLE Drive media tree (drive_mirror.catalog,
metadata only), filtered and ranked by core/media_search — THE matcher the
agents' find_media_file uses too, so the search box and the chat find the same
files. Plus three small facts per file:

- `new`: created in Drive after the client's previous visit to the tab. The
  visit mark is ONE app_settings row `media_seen:<id>` = {"at": iso} (no
  migration; client_deletion + deletion_guard know the prefix). The first
  visit ever marks nothing as new.
- `sent`: ["tiktok", "youtube"] — from what really happened: agent_actions
  rows that ended `done` (send_tiktok_to_inbox, publish_youtube_from_media)
  and finished TikTok upload jobs (core/tiktok_uploads — batch and
  owner-approved sends have no card row of their own).
- `playable`: a video small enough for the built-in player
  (drive_download.play_max_bytes, admin setting media_video_play_max_mb).

Never raises for Drive trouble: {"error": code} → one calm line in the tab.
Nothing here reaches a chat prompt.
"""
import time
from datetime import datetime, timezone

from core import drive_mirror, drive_storage, media_search

SERVICE_NAME = "media_library"
SEEN_PREFIX = "media_seen:"
PAGE = 60
MAX_PAGE = 200
SORTS = ("newest", "oldest", "name")
KINDS = ("video", "image", "document")
SENT_ACTIONS = {"send_tiktok_to_inbox": "tiktok", "send_tiktok_batch": "tiktok",
                "publish_youtube_from_media": "youtube"}
SENT_CACHE_SECONDS = 60
_sent_cache = {}   # client_id -> (at, {file_id: [targets]})


def _db():
    from agents.client_agent import _db as db
    return db()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── Last visit ──────────────────────────────────────────────────────────────

def last_seen(client_id: int) -> str:
    try:
        rows = (_db().table("app_settings").select("value").eq("key", f"{SEEN_PREFIX}{int(client_id)}")
                .limit(1).execute().data or [])
        return str(((rows[0].get("value") or {}) if rows else {}).get("at") or "")
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: last visit unreadable ({type(e).__name__})")
        return ""


def mark_seen(client_id: int) -> str:
    """Stamp this visit; returns the PREVIOUS one ("" = first visit)."""
    previous = last_seen(client_id)
    try:
        _db().table("app_settings").upsert({"key": f"{SEEN_PREFIX}{int(client_id)}", "value": {"at": _now_iso()},
                                            "updated_at": _now_iso()}, on_conflict="key").execute()
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: visit not stamped ({type(e).__name__})")
    return previous


# ─── Sent to TikTok / YouTube ────────────────────────────────────────────────

def sent_map(client_id: int) -> dict:
    hit = _sent_cache.get(client_id)
    if hit and hit[0] > time.time() - SENT_CACHE_SECONDS:
        return hit[1]
    out = {}

    def add(file_id, target):
        if file_id and target not in out.setdefault(file_id, []):
            out[file_id].append(target)
    try:
        rows = (_db().table("agent_actions").select("action,params,status").eq("client_id", int(client_id))
                .in_("status", ["done"]).in_("action", list(SENT_ACTIONS)).limit(1000).execute().data or [])
        for row in rows:
            params = row.get("params") or {}
            target = SENT_ACTIONS.get(row.get("action"))
            for fid in [params.get("drive_file_id")] + list(params.get("drive_file_ids") or []):
                add(fid, target)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: sent cards unreadable ({type(e).__name__})")
    try:
        from core import tiktok_uploads
        for job in tiktok_uploads.load(client_id):
            if job.get("status") == "done":
                add(job.get("file_id"), "tiktok")
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: TikTok jobs unreadable ({type(e).__name__})")
    _sent_cache[client_id] = (time.time(), out)
    return out


def forget(client_id: int):
    _sent_cache.pop(client_id, None)


# ─── The view ────────────────────────────────────────────────────────────────

def _clean_args(kind, folder, sort, offset, limit):
    kind = kind if kind in KINDS else ""
    sort = sort if sort in SORTS else "newest"
    folder = None if folder in (None, "*") else str(folder).strip().strip("/")
    offset = max(0, int(offset or 0))
    limit = max(1, min(MAX_PAGE, int(limit or PAGE)))
    return kind, folder, sort, offset, limit


@drive_storage.scoped
def view(client_id: int, q: str = "", kind: str = "", folder=None, sort: str = "newest", offset: int = 0,
         limit: int = PAGE, refresh: bool = False, since: str = "") -> dict:
    """One page of the tab. `folder`: None/"*" = everywhere, "" = the main
    folder only, "videos/tiktok" = that folder and below. `since`: the
    previous visit (from mark_seen) — files created after it are `new`."""
    kind, folder, sort, offset, limit = _clean_args(kind, folder, sort, offset, limit)
    q = str(q or "")[:120]
    try:
        cat = drive_mirror.catalog(client_id, refresh=refresh)
    except Exception as e:
        from core import drive_service
        reason = drive_service.classify_error(e)
        print(f"[{SERVICE_NAME}] client {client_id}: catalog failed ({reason}): {e}")
        return {"exists": True, "error": reason}
    if not cat.get("exists"):
        return {"exists": False}
    if folder and folder not in cat["folders"]:
        folder = None          # a folder the tree doesn't have (renamed / gone): everywhere
    everything = cat["items"]
    found = media_search.search(everything, q, kind=kind, folder=folder, sort=sort)
    from core import drive_download
    play_max = drive_download.play_max_bytes()
    sent = sent_map(client_id)
    names = {}
    for item in everything:
        key = media_search.compact(media_search.stem(item.get("name")))
        names[key] = names.get(key, 0) + 1
    page = []
    for item in found[offset:offset + limit]:
        created = item.get("created_time") or ""
        page.append({**item,
                     "new": bool(since and created and created > since),
                     "sent": sent.get(item["id"], []),
                     # two files with the same name: the card shows folder + date to tell them apart
                     "duplicate": names.get(media_search.compact(media_search.stem(item.get("name"))), 0) > 1,
                     "playable": (item.get("kind") == "video" and drive_download.playable_mime(item.get("mime_type"))
                                  and bool(item.get("size")) and item["size"] <= play_max)})
    counts = {k: sum(1 for i in media_search.search(everything, q, kind=k, folder=folder)) for k in KINDS}
    return {"exists": True, "items": page, "total": len(found), "offset": offset, "limit": limit,
            "counts": counts, "all": len(media_search.search(everything, q, folder=folder)),
            "folders": cat["folders"], "truncated": bool(cat.get("truncated")),
            "listed_at": cat.get("listed_at"), "cached": bool(cat.get("cached")),
            "play_max_mb": play_max // (1024 * 1024),
            "download_max_mb": drive_download.max_bytes() // (1024 * 1024),
            "new_count": sum(1 for i in everything if since and (i.get("created_time") or "") > since)}
