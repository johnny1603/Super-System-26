"""Read-only mirror of a client's Drive media folder, for the Media tab.

What it is: a LISTING of what is actually in `client-{id} — {name}/...` in the
Shared Drive — including files Johnny drops in by hand — without copying
anything into our system. Metadata only (drive_service.list_children asks for
id, name, mimeType, modifiedTime, size, thumbnailLink, webViewLink); no file
body is ever downloaded. Thumbnails are the one exception, and only when the
browser can't load Google's thumbnail URL itself (thumbnail()).

Rules this module holds:
- **Never trust a folder id from the browser.** The client's root folder id
  comes from the server's own record (media_agent's `media_folder_created`
  activity row). Any other folder is listed only after proving it sits UNDER
  that root (_assert_in_client_tree walks `parents`), and the media root
  itself / other clients' folders are refused.
- **The read path creates nothing.** No folder yet → `exists: False` and the
  Media tab explains; media_agent creates the folder when it first has
  something to put in it.
- **Cheap at scale.** Listings are cached per (client, folder, page) for
  CACHE_SECONDS; a failed listing for FAIL_CACHE_SECONDS, with one deduped
  admin alert per client per day. Pages are PAGE_SIZE items; a folder view
  stops paging at MAX_ITEMS_PER_FOLDER. Drive's default quota (12,000
  queries/min per project) is several orders of magnitude above what 50
  clients opening their Media tab can produce through this cache.
- Nothing here ever reaches a chat prompt.
"""
import os
import time
from collections import OrderedDict

from core import drive_service as drive

SERVICE_NAME = "drive_mirror"

CACHE_SECONDS = 180
FAIL_CACHE_SECONDS = 60
PAGE_SIZE = 40
MAX_ITEMS_PER_FOLDER = 400
MAX_PARENT_HOPS = 6          # how deep a subfolder may sit under the client root
FAIL_ALERT_DAYS = 1

THUMB_MAX_BYTES = 200 * 1024
THUMB_CACHE_ENTRIES = 400
THUMB_CACHE_SECONDS = 3600

ROOT_RESOLVE_SECONDS = 600

_root_cache = {}             # client_id -> (at, root folder id under the current media root)
_list_cache = {}             # (client_id, folder_id, page_token) -> (at, result)
_tree_cache = {}             # client_id -> {"root": id, "folders": {ids proven inside}}
_thumb_cache = OrderedDict() # (client_id, file_id) -> (at, bytes, content_type)


class NotInClientFolder(Exception):
    """The requested folder/file is not inside this client's media folder."""


# ─── Which folder is this client's ────────────────────────────────────────────

def client_root_id(client_id: int) -> str:
    """The client's media folder id, or "" if it doesn't exist yet. Never
    creates it (this is the read path).

    The authority is the `client-{id} — …` folder under the CURRENT media
    root — the one media_agent.ensure_client_media_root writes into. The
    `media_folder_created` activity row is only a hint: it is written ONCE,
    so after DRIVE_MEDIA_FOLDER_ID moved (personal My Drive → Shared Drive)
    it still named the OLD folder, and Cloud Run instances disagreed — one
    that had run ensure_client_media_root (memory cache) listed the new
    folder, every other instance the stale one. The hint is used only after
    checking it sits in the current root; otherwise the folder is looked up
    by its name prefix there. Resolved per client for ROOT_RESOLVE_SECONDS."""
    from agents import media_agent
    cached = media_agent._folder_cache.get(client_id)
    if cached and cached.get("root"):
        return cached["root"]   # found/created under the current root by this process
    hit = _root_cache.get(client_id)
    if hit and hit[0] > time.time() - ROOT_RESOLVE_SECONDS:
        return hit[1]

    rows = (media_agent._db().table("client_activity").select("details")
            .eq("client_id", client_id).eq("agent_name", media_agent.AGENT_NAME)
            .eq("action_type", "media_folder_created")
            .order("created_at", desc=True).limit(1).execute().data or [])
    hinted = ((rows[0].get("details") or {}).get("folder_id") or "") if rows else ""
    media_root = media_agent._media_root_id()
    if not media_root:
        return hinted
    root = ""
    try:
        if hinted:
            meta = drive.get_meta(hinted, "id,parents,trashed")
            if not meta.get("trashed") and media_root in (meta.get("parents") or []):
                root = hinted
        if not root:
            matches = drive.find_child_folders_by_prefix(media_root, f"client-{client_id} — ")
            if len(matches) > 1:
                print(f"[{SERVICE_NAME}] client {client_id}: {len(matches)} folders match under the "
                      f"media root — using the oldest, {matches[0]['id']}")
            root = matches[0]["id"] if matches else ""
            if hinted and root != hinted:
                print(f"[{SERVICE_NAME}] client {client_id}: recorded folder {hinted} is not under "
                      f"the current media root — using {root or '(none yet)'}")
    except Exception as e:
        # Drive trouble: not cached; the recorded folder is still THIS
        # client's own, and list_folder reports the Drive failure itself
        print(f"[{SERVICE_NAME}] client {client_id}: root resolve failed ({e}) — using the record")
        return hinted
    _root_cache[client_id] = (time.time(), root)
    return root


def _tree(client_id: int, root: str) -> dict:
    tree = _tree_cache.get(client_id)
    if not tree or tree["root"] != root:
        tree = {"root": root, "folders": {root}}
        _tree_cache[client_id] = tree
    return tree


def _assert_in_client_tree(client_id: int, root: str, item_id: str):
    """Raise NotInClientFolder unless item_id IS the root or sits under it."""
    tree = _tree(client_id, root)
    if item_id in tree["folders"]:
        return
    media_root = os.environ.get("DRIVE_MEDIA_FOLDER_ID", "")
    current, hops = item_id, 0
    while hops < MAX_PARENT_HOPS:
        parents = drive.get_meta(current, "id,parents").get("parents") or []
        if not parents:
            break
        parent = parents[0]
        if parent in tree["folders"]:
            return
        if parent == media_root:
            break   # reached the shared media root without passing this client's folder
        current, hops = parent, hops + 1
    raise NotInClientFolder(item_id)


# ─── Listing ──────────────────────────────────────────────────────────────────

def _shape(item: dict) -> dict:
    is_folder = item.get("mimeType") == drive.FOLDER_MIME
    mime = item.get("mimeType") or ""
    kind = ("folder" if is_folder else "image" if mime.startswith("image/")
            else "video" if mime.startswith("video/")
            else "document" if ("document" in mime or "pdf" in mime or mime.startswith("text/")
                                or "spreadsheet" in mime or "presentation" in mime)
            else "file")
    from core import drive_download   # imports this module; resolved lazily
    return {"id": item.get("id", ""), "name": item.get("name", ""), "kind": kind,
            # UI hints only — the download endpoint re-checks everything
            "download": "none" if is_folder else drive_download.download_kind(mime),
            "previewable": drive_download.is_previewable(mime),
            "mime_type": mime, "modified_time": item.get("modifiedTime", ""),
            "size": int(item["size"]) if str(item.get("size") or "").isdigit() else None,
            "thumbnail_link": item.get("thumbnailLink", ""),
            "web_view_link": item.get("webViewLink", "")}


def list_folder(client_id: int, folder_id: str = "", page_token: str = "",
                refresh: bool = False) -> dict:
    """One page of the client's media folder (folder_id "" = its root).
    Returns {"exists", "folder_id", "is_root", "folders", "files",
    "next_page_token", "cached", "listed_at"} — or {"exists": False}, or
    {"error": reason} (a short classify_error code, for a neutral UI line).
    Never raises for Drive trouble; raises NotInClientFolder for a folder
    outside this client's tree (the endpoints turn that into a 404)."""
    root = client_root_id(client_id)
    if not root:
        return {"exists": False}
    target = folder_id or root
    if target != root:
        _assert_in_client_tree(client_id, root, target)

    key = (client_id, target, page_token or "")
    cached = _list_cache.get(key)
    if cached and not refresh:
        ttl = FAIL_CACHE_SECONDS if cached[1].get("error") else CACHE_SECONDS
        if cached[0] > time.time() - ttl:
            return {**cached[1], "cached": True}

    try:
        page = drive.list_children(target, page_token=page_token, page_size=PAGE_SIZE)
    except Exception as e:
        reason = drive.classify_error(e)
        print(f"[{SERVICE_NAME}] client {client_id}: listing {target} failed ({reason}): {e}")
        from core.chat_fallback import alert_once
        alert_once(SERVICE_NAME, client_id, f"{SERVICE_NAME}:list_failed",
                   f"client {client_id}: Media tab Drive listing failed ({reason}): {str(e)[:300]}",
                   FAIL_ALERT_DAYS)
        failed = {"exists": True, "folder_id": target, "is_root": target == root, "error": reason}
        _list_cache[key] = (time.time(), failed)
        return {**failed, "cached": False}

    items = [_shape(f) for f in page["files"]]
    folders = [i for i in items if i["kind"] == "folder"]
    tree = _tree(client_id, root)
    tree["folders"].update(f["id"] for f in folders)   # proven inside: listed from inside
    result = {"exists": True, "folder_id": target, "is_root": target == root,
              "folders": folders, "files": [i for i in items if i["kind"] != "folder"],
              "next_page_token": page["next_page_token"], "listed_at": int(time.time())}
    _list_cache[key] = (time.time(), result)
    return {**result, "cached": False}


def page_allowed(pages_already_loaded: int) -> bool:
    """Total cap per folder view: PAGE_SIZE * pages <= MAX_ITEMS_PER_FOLDER."""
    return (pages_already_loaded + 1) * PAGE_SIZE <= MAX_ITEMS_PER_FOLDER


# ─── Thumbnails (fallback proxy) ──────────────────────────────────────────────

def thumbnail(client_id: int, file_id: str) -> tuple:
    """(bytes, content_type) of a small thumbnail for a file in the client's
    folder — fetched as the service account, size-capped, cached in memory.
    Used only when the browser can't load Google's thumbnailLink directly."""
    key = (client_id, file_id)
    hit = _thumb_cache.get(key)
    if hit and hit[0] > time.time() - THUMB_CACHE_SECONDS:
        _thumb_cache.move_to_end(key)
        return hit[1], hit[2]
    root = client_root_id(client_id)
    if not root:
        raise NotInClientFolder(file_id)
    meta = drive.get_meta(file_id, "id,parents,thumbnailLink,mimeType")
    parent = (meta.get("parents") or [""])[0]
    _assert_in_client_tree(client_id, root, parent)
    link = meta.get("thumbnailLink") or ""
    if not link:
        raise FileNotFoundError("no thumbnail")
    # Google's thumbnail URLs end in a size directive (=s220); ask for a small one
    if "=s" in link.rsplit("/", 1)[-1]:
        link = link.rsplit("=s", 1)[0] + "=s320"
    content, content_type = drive.fetch_thumbnail(link, THUMB_MAX_BYTES)
    _thumb_cache[key] = (time.time(), content, content_type)
    while len(_thumb_cache) > THUMB_CACHE_ENTRIES:
        _thumb_cache.popitem(last=False)
    return content, content_type
