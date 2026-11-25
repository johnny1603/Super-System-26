"""What the Social tab's preview needs beyond the queue (2026-11-25).

The preview shows a post the way the platform will: the profile row above it
carries the client's ACTUAL account name — read live with the client's own
connection (Instagram: the business account's username; Facebook: the Page's
name; TikTok: the display name), kept in memory for a few hours, never stored.
A channel that is not connected, or whose name can't be read right now, has
no handle: the page then shows the business name and says the account is not
connected — never an invented handle.

Never raises; adds no storage, no scope and no new permission (every read
here is one the agents already make).
"""
import threading
import time

SERVICE_NAME = "social_preview"
TTL_SECONDS = 6 * 3600
RETRY_SECONDS = 600               # a failed read is retried after this, not on every page load
_CACHE = {}
_LOCK = threading.Lock()


def _business_name(client_id: int) -> str:
    try:
        from agents.client_agent import get_client
        row = get_client(client_id) or {}
        return str(row.get("business_name") or row.get("name") or "").strip()[:80]
    except Exception:
        return ""


def _instagram(client_id: int) -> dict:
    from agents import meta_content_agent as mc
    from core import meta_service as meta
    row = mc._get_connection(client_id, mc.INSTAGRAM_PLATFORM)
    if not row or not row.get("access_token"):
        return {}
    got = meta.graph_get(str(row["account_id"]), row["access_token"], params={"fields": "username,name"})
    return {"handle": str(got.get("username") or "")[:60], "name": str(got.get("name") or "")[:80]}


def _facebook(client_id: int) -> dict:
    from agents import meta_content_agent as mc
    from core import meta_service as meta
    row = mc._get_connection(client_id, mc.PAGE_PLATFORM)
    if not row or not row.get("access_token"):
        return {}
    got = meta.graph_get(str(row["account_id"]), row["access_token"], params={"fields": "name"})
    return {"handle": str(got.get("name") or "")[:80], "name": str(got.get("name") or "")[:80]}


def _tiktok(client_id: int) -> dict:
    from agents import tiktok_content_agent
    got = tiktok_content_agent.get_profile(client_id) or {}
    if not got.get("connected") or got.get("unavailable"):
        return {}
    return {"handle": str(got.get("display_name") or "")[:60], "name": str(got.get("display_name") or "")[:80]}


READERS = {"instagram": _instagram, "facebook": _facebook, "tiktok": _tiktok}


def profile(client_id: int, _readers: dict = None, _now=None) -> dict:
    """{"name": the business name, "accounts": {channel: {"handle", "name"}}}
    — only channels whose account name was really read."""
    now = (_now or time.time)()
    with _LOCK:
        hit = _CACHE.get(int(client_id))
        if hit and hit["until"] > now and _readers is None:
            return hit["value"]
    accounts, failed = {}, False
    for channel, read in (_readers or READERS).items():
        try:
            got = read(client_id)
            if got and got.get("handle"):
                accounts[channel] = got
        except Exception as e:
            failed = True
            print(f"[{SERVICE_NAME}] client {client_id}: {channel} account name not read ({type(e).__name__})")
    value = {"name": _business_name(client_id), "accounts": accounts}
    if _readers is None:
        with _LOCK:
            _CACHE[int(client_id)] = {"value": value, "until": now + (RETRY_SECONDS if failed else TTL_SECONDS)}
    return value


def forget(client_id: int):
    with _LOCK:
        _CACHE.pop(int(client_id), None)


def frame(item: dict) -> dict:
    """{"format", "aspect", "safe_zone"} a queue item is previewed in — from
    THE table (core/social_formats), never a number of the page's own."""
    from core import social_formats as sf
    kind = item.get("kind")
    channels = item.get("channels") or []
    fmt = None
    zone = ""
    placement = str((item.get("post_ref") or {}).get("placement") or "")
    if kind == "post":
        post = item.get("post") or {}
        fmt = sf.FORMATS.get(post.get("format") or "") or sf.for_post(post.get("post_format"), post.get("placement"))
        zone = post.get("safe_zone") or ""
        placement = str(post.get("placement") or "")
    elif kind == "design":
        spec = (item.get("design") or {}).get("spec") or {}
        canvas = spec.get("canvas") or {}
        size = (canvas.get("width"), canvas.get("height"))
        fmt = next((f for f in sf.FORMATS.values() if f.size == size), None)
        zone = spec.get("safe_zone") or ""
    else:
        ref = item.get("post_ref") or {}
        fmt = sf.FORMATS.get(ref.get("format") or "")
        if not fmt and kind == "video":
            fmt = sf.FORMATS[sf.VERTICAL]
    fmt = fmt or sf.FORMATS[sf.DEFAULT_FEED]
    if fmt.kind == "vertical":
        zone = zone if zone in sf.SAFE_ZONES else sf.zone_for(channels)
    else:
        zone = ""
    placement = placement if placement in ("story", "reel") and fmt.kind == "vertical" else (
        "reel" if fmt.kind == "vertical" else "feed")
    return {"format": fmt.name, "aspect": fmt.aspect, "width": fmt.width, "height": fmt.height, "safe_zone": zone,
            "placement": placement}
