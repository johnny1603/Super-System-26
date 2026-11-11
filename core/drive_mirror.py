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
- The Media tab's listing never reaches a chat prompt. The agents get
  `newest()` (the newest videos/images across the WHOLE tree) and `find()`
  (name search across the whole tree), both through client_state /
  agent_actions — 2026-11-11, docs/DRIVE-PULL.md.
- `diagnose()` (admin only) shows what Drive itself returns for a client next
  to what the Media tab and the agents see.
"""
import re
import time
from collections import OrderedDict

from core import drive_service as drive
from core import drive_storage

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

def forget(client_id: int):
    """Where the client's files live changed (core/drive_storage.forget)."""
    _root_cache.pop(client_id, None)
    _tree_cache.pop(client_id, None)
    _folders_cache.pop(client_id, None)
    _catalog_cache.pop(client_id, None)
    for key in [k for k in _list_cache if k[0] == client_id]:
        _list_cache.pop(key, None)
    for key in [k for k in _thumb_cache if k[0] == client_id]:
        _thumb_cache.pop(key, None)


@drive_storage.scoped
def client_root_id(client_id: int) -> str:
    """The client's media folder id, or "" if it doesn't exist yet. Never
    creates it (this is the read path). Wherever drive_storage.location says
    the files live: the uallak folder in the client's own Drive, or their
    folder in our Shared Drive (shared_client_root)."""
    where = drive_storage.location(client_id)
    if where["kind"] == drive_storage.CLIENT:
        return where["root"]
    return shared_client_root(client_id)


def shared_client_root(client_id: int) -> str:
    """The client's folder in OUR Shared Drive, or "" — whatever the pointer
    says (the migration and the usage scan read it; run as the service
    account).

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
    if cached and cached.get("root") and cached.get("kind") == drive_storage.SHARED:
        return cached["root"]   # found/created under the current root by this process
    hit = _root_cache.get(client_id)
    if hit and hit[0] > time.time() - ROOT_RESOLVE_SECONDS:
        return hit[1]

    rows = (media_agent._db().table("client_activity").select("details")
            .eq("client_id", client_id).eq("agent_name", media_agent.AGENT_NAME)
            .eq("action_type", "media_folder_created")
            .order("created_at", desc=True).limit(1).execute().data or [])
    hinted = ((rows[0].get("details") or {}).get("folder_id") or "") if rows else ""
    media_root = drive_storage.shared_parent_id()
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
    if root:
        # "No folder yet" is NOT cached: another instance may create it any
        # moment, and a cached "" hid it here for ROOT_RESOLVE_SECONDS
        _root_cache[client_id] = (time.time(), root)
    return root


def remember_root(client_id: int, root: str):
    """media_agent found/created the client's folder under the CURRENT root
    with this rule; every reader in this process uses the same one."""
    if root:
        _root_cache[client_id] = (time.time(), root)


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
    # The boundary above the client's root: our Shared Drive media root, or
    # the client's My Drive (the uallak folder's recorded parent)
    media_root = drive_storage.location(client_id).get("parent") or ""
    current, hops = item_id, 0
    while hops < MAX_PARENT_HOPS:
        parents = drive.get_meta(current, "id,parents").get("parents") or []
        if not parents:
            break
        parent = parents[0]
        if parent in tree["folders"]:
            return
        if parent == media_root:
            break   # reached the media root / My Drive without passing this client's folder
        current, hops = parent, hops + 1
    raise NotInClientFolder(item_id)


# ─── Listing ──────────────────────────────────────────────────────────────────

def kind_of(mime: str) -> str:
    mime = mime or ""
    return ("folder" if mime == drive.FOLDER_MIME else "shortcut" if mime == drive.SHORTCUT_MIME
            else "image" if mime.startswith("image/")
            else "video" if mime.startswith("video/")
            else "document" if ("document" in mime or "pdf" in mime or mime.startswith("text/")
                                or "spreadsheet" in mime or "presentation" in mime)
            else "file")


def _shape(item: dict) -> dict:
    mime = item.get("mimeType") or ""
    kind = kind_of(mime)
    from core import drive_download   # imports this module; resolved lazily
    shaped = {"id": item.get("id", ""), "name": item.get("name", ""), "kind": kind,
              # UI hints only — the download endpoint re-checks everything
              "download": ("none" if kind == "folder" else "drive" if kind == "shortcut"
                           else drive_download.download_kind(mime)),
              "previewable": kind != "shortcut" and drive_download.is_previewable(mime),
              "mime_type": mime, "modified_time": item.get("modifiedTime", ""),
              # when it ARRIVED in Drive (an upload keeps the file's own modified time)
              "created_time": item.get("createdTime", "") or item.get("modifiedTime", ""),
              "size": int(item["size"]) if str(item.get("size") or "").isdigit() else None,
              "thumbnail_link": item.get("thumbnailLink", ""),
              "web_view_link": item.get("webViewLink", "")}
    if kind == "shortcut":
        # A SHORTCUT is a pointer; the file itself lives outside the client's
        # folder, so nothing can be uploaded or published from it. Drive makes
        # one when a file you don't own is dragged into a Shared Drive folder.
        details = item.get("shortcutDetails") or {}
        shaped["target_id"] = details.get("targetId", "")
        shaped["target_kind"] = kind_of(details.get("targetMimeType", ""))
    return shaped


@drive_storage.scoped
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

@drive_storage.scoped
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


# ─── The whole tree: newest + find by name (the agents) ──────────────────────
# Before 2026-11-11 the agents saw the root and its first 8 subfolders ONE
# level deep, first page each: a file in videos/tiktok/ (where media_agent
# itself saves TikTok videos) or deeper never reached them.

TREE_SECONDS = 180
MAX_TREE_FOLDERS = 200
FIND_LIMIT = 10
_folders_cache = {}   # client_id -> (at, root, [{"id", "name", "path"}])
_KIND_MIME = {"video": "video/", "image": "image/"}
# The words a request drops and how names match: core/media_search (one matcher)


@drive_storage.scoped
def _walk_folders(client_id: int, root: str, max_depth: int = MAX_PARENT_HOPS, max_folders: int = MAX_TREE_FOLDERS) -> list:
    """Every folder under root (root included, path ""), breadth first, as
    whoever the current scope is."""
    out, pending = [{"id": root, "name": "", "path": ""}], [(root, "", 0)]
    while pending and len(out) < max_folders:
        folder, path, depth = pending.pop(0)
        if depth >= max_depth:
            continue
        page_token = ""
        while True:
            page = drive.list_children(folder, page_token=page_token, page_size=200,
                                       fields="nextPageToken,files(id,name,mimeType)")
            for item in page["files"]:
                if item.get("mimeType") == drive.FOLDER_MIME and len(out) < max_folders:
                    sub = f"{path}/{item.get('name', '')}".lstrip("/")
                    out.append({"id": item["id"], "name": item.get("name", ""), "path": sub})
                    pending.append((item["id"], sub, depth + 1))
            page_token = page["next_page_token"]
            if not page_token:
                break
    return out


@drive_storage.scoped
def folder_tree(client_id: int, refresh: bool = False) -> list:
    """Every folder of the client's media tree with its path ("videos/tiktok"),
    cached TREE_SECONDS. Each one is proven inside (walked from the root)."""
    root = client_root_id(client_id)
    if not root:
        return []
    hit = _folders_cache.get(client_id)
    if hit and not refresh and hit[1] == root and hit[0] > time.time() - TREE_SECONDS:
        return hit[2]
    folders = _walk_folders(client_id, root)
    _folders_cache[client_id] = (time.time(), root, folders)
    _tree(client_id, root)["folders"].update(f["id"] for f in folders)
    return folders


def _placed(item: dict, paths: dict) -> dict:
    shaped = _shape(item)
    shaped["folder"] = paths.get((item.get("parents") or [""])[0], "")
    return shaped


@drive_storage.scoped
def newest(client_id: int, kind: str, limit: int = 10, refresh: bool = False) -> list:
    """The newest `kind` ("video" / "image") files anywhere in the client's
    tree, newest first, each with its folder path. [] without a folder."""
    folders = folder_tree(client_id, refresh=refresh)
    if not folders:
        return []
    paths = {f["id"]: f["path"] for f in folders}
    items = drive.search_in_folders([f["id"] for f in folders], mime_prefix=_KIND_MIME[kind], page_size=limit)
    return [_placed(i, paths) for i in items]


def search_words(text: str) -> list:
    """The words of a request that could be in a file's NAME (longest first,
    at most 4): "use the video of the new product" → ["product", "new"].
    core/media_search's words — the one matcher."""
    from core import media_search
    seen, out = set(), []
    for w in media_search.query_words(text):
        if w not in seen:
            seen.add(w)
            out.append(w)
    return sorted(out, key=len, reverse=True)[:4]


# ─── The catalog: every file of the whole tree (Media tab search + find) ─────
# 2026-11-11 (docs/MEDIA-TAB.md). ONE listing of the tree, matched in Python
# by core/media_search — Drive's `name contains` could not match "test_3",
# a lone digit, or Thai.

CATALOG_SECONDS = 120
CATALOG_MAX_ITEMS = 2000
_catalog_cache = {}   # client_id -> (at, root, [shaped + "folder"], truncated)


@drive_storage.scoped
def catalog(client_id: int, refresh: bool = False) -> dict:
    """{"exists", "items": [shaped + folder path], "folders": [paths],
    "truncated", "listed_at", "cached"} for the client's WHOLE media tree.
    Shortcuts are items too (kind "shortcut", opened in Drive). Raises on
    Drive trouble — callers say one calm line."""
    root = client_root_id(client_id)
    if not root:
        return {"exists": False, "items": [], "folders": []}
    hit = _catalog_cache.get(client_id)
    if hit and not refresh and hit[1] == root and hit[0] > time.time() - CATALOG_SECONDS:
        return {**hit[2], "cached": True}
    folders = folder_tree(client_id, refresh=refresh)
    paths = {f["id"]: f["path"] for f in folders}
    raw = drive.list_in_folders([f["id"] for f in folders], max_items=CATALOG_MAX_ITEMS + 1)
    items = [_placed(i, paths) for i in raw[:CATALOG_MAX_ITEMS]]
    result = {"exists": True, "items": items, "folders": sorted(p for p in paths.values() if p),
              "truncated": len(raw) > CATALOG_MAX_ITEMS, "listed_at": int(time.time())}
    _catalog_cache[client_id] = (time.time(), root, result)
    return {**result, "cached": False}


@drive_storage.scoped
def find(client_id: int, text: str, kind: str = "", limit: int = FIND_LIMIT) -> dict:
    """Files in the client's WHOLE tree whose NAME matches `text`
    (core/media_search — the same matcher as the Media tab's search box),
    fresh from Drive, best matches only. Shortcuts are left out (the file
    itself is outside the client's folder) and counted.
    {"matches": [shaped + "folder"], "words": [...], "shortcuts": n}.
    Raises on Drive trouble (the caller says one calm line)."""
    from core import media_search
    words = search_words(text)
    if not words:
        return {"matches": [], "words": []}
    cat = catalog(client_id, refresh=True)
    if not cat.get("exists"):
        return {"matches": [], "words": words, "no_folder": True}
    found = media_search.search(cat["items"], text, kind=kind if kind in ("video", "image", "document") else "",
                                best_only=True)
    matches = [f for f in found if f["kind"] != "shortcut"]
    return {"matches": matches[:limit], "words": words, "shortcuts": len(found) - len(matches)}


# ─── Admin diagnostic: what Drive returns vs what we show ────────────────────

DIAG_MAX_FILES = 500


def _hinted_folder(client_id: int) -> str:
    from agents import media_agent
    rows = (media_agent._db().table("client_activity").select("details")
            .eq("client_id", client_id).eq("agent_name", media_agent.AGENT_NAME)
            .eq("action_type", "media_folder_created")
            .order("created_at", desc=True).limit(1).execute().data or [])
    return ((rows[0].get("details") or {}).get("folder_id") or "") if rows else ""


@drive_storage.scoped
def diagnose(client_id: int, max_files: int = DIAG_MAX_FILES) -> dict:
    """ADMIN ONLY, read-only. For one client: which folder the system uses and
    why, every folder in Drive that looks like this client's, the RAW listing
    Drive returns for the tree (names, kinds, parent ids), and for each file
    whether the Media tab's first page / the agents' list show it — plus plain
    findings. Never a token, never a file body."""
    from agents import media_agent
    out = {"client_id": int(client_id), "location": drive_storage.location(client_id).get("kind"),
           "media_root": drive_storage.shared_parent_id(), "findings": []}
    findings = out["findings"]
    try:
        out["service_account"] = drive.service_account_email()
    except Exception as e:
        out["service_account"] = ""
        findings.append(f"מפתח חשבון השירות לא נקרא ({type(e).__name__}) — אין גישה ל-Drive בכלל.")
        return out
    _root_cache.pop(client_id, None)       # resolve fresh, like a new instance would
    writer = (media_agent._folder_cache.get(client_id) or {}).get("root", "")
    hinted = _hinted_folder(client_id)
    out["recorded_folder"] = hinted
    out["writer_folder_this_instance"] = writer
    try:
        out["folders_named_like_client"] = [
            {"id": f.get("id"), "name": f.get("name"), "parents": f.get("parents") or [],
             "shared_drive": f.get("driveId", ""), "created": f.get("createdTime", ""),
             "link": f.get("webViewLink", ""),
             "under_media_root": out["media_root"] in (f.get("parents") or [])}
            for f in drive.find_folders_named(f"client-{int(client_id)} ")
            if str(f.get("name") or "").startswith(f"client-{int(client_id)} ")]
    except Exception as e:
        out["folders_named_like_client"] = []
        findings.append(f"החיפוש אחר תיקיות בשם הלקוח נכשל ({drive.classify_error(e)}).")
    root = client_root_id(client_id)
    out["resolved_folder"] = root
    if not root:
        findings.append("למערכת אין תיקייה ללקוח הזה תחת תיקיית המדיה הנוכחית — היא תיווצר בקובץ הראשון.")
        return out
    try:
        out["resolved_link"] = drive.get_link(root)
    except Exception:
        out["resolved_link"] = ""
    named = out["folders_named_like_client"]
    under = [f for f in named if f["under_media_root"]]
    elsewhere = [f for f in named if not f["under_media_root"]]
    if len(under) > 1:
        findings.append(f"יש {len(under)} תיקיות ללקוח הזה בתיקיית המדיה. המערכת משתמשת ב-{root}; "
                        f"קבצים בתיקייה האחרת לא נראים. כדאי להעביר אותם לתיקייה שבשימוש ולמחוק את הכפולה.")
    if elsewhere:
        findings.append(f"יש {len(elsewhere)} תיקיות בשם של הלקוח מחוץ לתיקיית המדיה (למשל ב-My Drive הישן). "
                        f"קבצים שם לא נראים במערכת — משכו אותם בעמוד הזה או העבירו לתיקייה שבשימוש.")
    if hinted and hinted != root:
        findings.append(f"התיקייה שנרשמה בפעם הראשונה ({hinted}) היא לא זו שבשימוש — "
                        f"היא כבר לא בתיקיית המדיה הנוכחית.")
    if writer and writer != root:
        findings.append(f"בשרת הזה קבצים חדשים נשמרו לתיקייה אחרת ({writer}) מזו שמוצגת ({root}).")

    folders = _walk_folders(client_id, root)
    paths = {f["id"]: f["path"] for f in folders}
    raw, truncated = [], False
    for folder in folders:
        page_token = ""
        while True:
            page = drive.list_children(folder["id"], page_token=page_token, page_size=200)
            for item in page["files"]:
                if item.get("mimeType") == drive.FOLDER_MIME:
                    continue
                if len(raw) >= max_files:
                    truncated = True
                    break
                shaped = _placed(item, paths)
                shaped["parent_id"] = (item.get("parents") or [""])[0]
                raw.append(shaped)
            page_token = page["next_page_token"]
            if truncated or not page_token:
                break
        if truncated:
            break
    tab = list_folder(client_id)
    tab_ids = {f["id"] for f in (tab.get("files") or [])} | {f["id"] for f in (tab.get("folders") or [])}
    try:
        from core import client_state
        agent_view = client_state._media_folder(client_id, fresh=True)
    except Exception as e:
        agent_view = {"error": type(e).__name__}
    agent_ids = {v.get("drive_file_id") for v in (agent_view.get("videos") or [])} | \
                {i.get("drive_file_id") for i in (agent_view.get("images") or [])}
    for f in raw:
        f["in_media_tab_first_page"] = f["id"] in tab_ids
        f["in_agent_list"] = f["id"] in agent_ids
    shortcuts = [f for f in raw if f["kind"] == "shortcut"]
    deep = [f for f in raw if "/" in f["folder"]]
    out.update({"folders": folders, "files": raw, "files_truncated": truncated,
                "raw_counts": {k: sum(1 for f in raw if f["kind"] == k)
                               for k in sorted({f["kind"] for f in raw})},
                "media_tab": {"cached": bool(tab.get("cached")), "listed_at": tab.get("listed_at"),
                              "root_page_items": len(tab_ids), "error": tab.get("error", "")},
                "agent_view": {"videos": len(agent_view.get("videos") or []),
                               "images": len(agent_view.get("images") or []),
                               "error": agent_view.get("error", "")}})
    if shortcuts:
        findings.append(f"{len(shortcuts)} פריטים הם קיצורי דרך (Shortcut) ולא הקובץ עצמו — Drive יוצר אותם "
                        f"כשגוררים קובץ שלא שלכם. אי אפשר להעלות מהם; משכו את הקובץ המקורי בעמוד הזה.")
    if deep:
        findings.append(f"{len(deep)} קבצים יושבים בתת-תיקייה עמוקה (למשל videos/tiktok). עד היום הסוכנים לא ראו "
                        f"אותם; מעכשיו הם רואים את כל העץ ויכולים לחפש לפי שם.")
    if tab.get("cached"):
        findings.append("לשונית המדיה הציגה רשימה שמורה (עד 3 דקות). קובץ שנוסף זה עתה מופיע אחרי רענון.")
    if not findings:
        findings.append("Drive והמערכת מראים את אותם קבצים.")
    return out
