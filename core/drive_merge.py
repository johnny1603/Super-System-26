"""Merge a client's second Drive folder into the connected one — ADMIN ONLY
(2026-11-11, docs/DRIVE-PULL.md §Merge folders).

Client 27 ended up with two folders named like them. The system reads and
writes ONE (drive_mirror.shared_client_root — THE resolver, used by the
writer media_agent.ensure_client_media_root too); files in the other one are
invisible to the Media tab and to the agents.

plan(client_id): every folder named "client-{id} …" the service account can
see — name, id, which one is CONNECTED (the resolver's answer), where it sits,
how many files it holds (bounded walk).

start(client_id, source_folder_id): a durable core/drive_pull job, mode
"merge": everything in the source folder is COPIED (files.copy, inside Drive)
into the connected folder with the SAME sub-folder structure; a file already
there with the same name and size is skipped, and our own earlier copies are
found by appProperties. Nothing is moved, nothing is deleted — the source
folder stays exactly as it was. Progress per file, retries, the every-minute
scheduler tick: all drive_pull's.

media_diagnosis(client_id, find): Part 4's read-only report for
scripts/diagnose_media.py — the stored folder id, the raw tree of every
folder named like the client, the result of the find tools for a name.
"""
import time

SERVICE_NAME = "drive_merge"
TREE_MAX_FILES = 300           # per folder, in the diagnosis
COUNT_MAX_FILES = 2000         # per folder, in the plan
COUNT_MAX_FOLDERS = 200


class MergeRefused(ValueError):
    def __init__(self, code: str, text: str):
        super().__init__(code)
        self.code, self.text = code, text


def _sa():
    from core import drive_storage
    return drive_storage.as_service_account()


def _prefix(client_id: int) -> str:
    return f"client-{int(client_id)} "


def folders_named_like(client_id: int) -> list:
    """Every non-trashed folder named "client-{id} …" anywhere the service
    account can see. Raises on Drive trouble."""
    from core import drive_service as drive
    with _sa():
        found = drive.find_folders_named(_prefix(client_id), page_size=50)
    return [f for f in found if str(f.get("name") or "").startswith(_prefix(client_id))]


def _walk(folder_id: str, max_files: int, with_files: bool = False) -> dict:
    """{"files": n, "folders": n, "bytes": n, "truncated", "tree": [...]} — a
    bounded breadth-first walk, metadata only."""
    from core import drive_service as drive
    queue, out = [(folder_id, "")], {"files": 0, "folders": 0, "bytes": 0, "truncated": False, "tree": []}
    seen = 0
    while queue:
        current, path = queue.pop(0)
        seen += 1
        if seen > COUNT_MAX_FOLDERS:
            out["truncated"] = True
            break
        token = ""
        while True:
            page = drive.list_children(current, page_token=token, page_size=200)
            for item in page["files"]:
                if item.get("mimeType") == drive.FOLDER_MIME:
                    out["folders"] += 1
                    queue.append((item["id"], f"{path}/{item.get('name')}".lstrip("/")))
                    continue
                if out["files"] >= max_files:
                    out["truncated"] = True
                    break
                out["files"] += 1
                size = int(item["size"]) if str(item.get("size") or "").isdigit() else 0
                out["bytes"] += size
                if with_files:
                    out["tree"].append({"name": item.get("name"), "id": item.get("id"), "folder": path,
                                        "mime_type": item.get("mimeType"), "size": size or None,
                                        "modified_time": item.get("modifiedTime", "")})
            token = page["next_page_token"]
            if out["truncated"] or not token:
                break
        if out["truncated"]:
            break
    return out


def plan(client_id: int) -> dict:
    """The folders named like the client and which one is connected. Read-only."""
    from core import drive_mirror, drive_storage
    client_id = int(client_id)
    if drive_storage.location(client_id).get("kind") != drive_storage.SHARED:
        raise MergeRefused("client_drive", "הקבצים של הלקוח הזה נמצאים ב-Drive שלו — אין כאן תיקיות לאחד.")
    with _sa():
        connected = drive_mirror.shared_client_root(client_id)
        media_root = drive_storage.shared_parent_id()
        rows = []
        for f in folders_named_like(client_id):
            count = _walk(f["id"], COUNT_MAX_FILES)
            rows.append({"id": f.get("id"), "name": f.get("name"), "connected": f.get("id") == connected,
                         "under_media_root": media_root in (f.get("parents") or []),
                         "created": f.get("createdTime", ""), "link": f.get("webViewLink", ""),
                         "files": count["files"], "subfolders": count["folders"], "bytes": count["bytes"],
                         "count_truncated": count["truncated"]})
    rows.sort(key=lambda r: (not r["connected"], r["created"]))
    return {"client_id": client_id, "connected": connected, "folders": rows,
            "mergeable": [r["id"] for r in rows if not r["connected"]]}


def _inside(folder_id: str, root: str, max_hops: int = 8) -> bool:
    from core import drive_service as drive
    current, hops = folder_id, 0
    while current and hops < max_hops:
        if current == root:
            return True
        try:
            current = (drive.get_meta(current, "id,parents").get("parents") or [""])[0]
        except Exception:
            # the top of someone's My Drive (or a Shared Drive root) is not
            # readable as a folder: we walked past everything we can see
            return False
        hops += 1
    return False


def start(client_id: int, source_folder_id: str, who: str = "admin") -> dict:
    """Start the merge job. The source must be one of the folders named like
    THIS client, not the connected one and not inside it. Raises MergeRefused."""
    from core import drive_mirror, drive_pull
    client_id = int(client_id)
    info = plan(client_id)
    if not info["connected"]:
        raise MergeRefused("no_connected", "ללקוח עוד אין תיקייה מחוברת — אין לאן לאחד.")
    source_row = next((r for r in info["folders"] if r["id"] == source_folder_id), None)
    if not source_row:
        raise MergeRefused("not_listed", "התיקייה הזו לא ברשימת התיקיות של הלקוח.")
    if source_row["connected"]:
        raise MergeRefused("is_connected", "זו התיקייה המחוברת עצמה — בחרו את התיקייה השנייה.")
    with _sa():
        if _inside(source_folder_id, info["connected"]):
            raise MergeRefused("inside_connected", "התיקייה הזו נמצאת בתוך התיקייה המחוברת.")
    try:
        job = drive_pull.start(client_id, [{"id": source_folder_id, "kind": "folder"}], "auto", who=who,
                               mode="merge", source={"id": source_folder_id, "name": source_row["name"],
                                                     "kind": "folder"})
    except drive_pull.PullRefused as e:
        raise MergeRefused(e.code, e.text) from None
    drive_mirror.forget(client_id)
    return drive_pull.step(client_id) or job


# ─── Part 4: the read-only report for scripts/diagnose_media.py ─────────────

def media_diagnosis(client_id: int, find: str = "") -> dict:
    """For one client: the stored folder id (the media_folder_created hint)
    and the resolved one, the raw tree of every folder named like the client,
    and what the find tools return for `find` — the client-folder search
    (drive_mirror.find, the agents' find_media_file and the Media tab search)
    and the search everywhere (attach existing file). Never a token."""
    from core import drive_mirror, drive_pull, drive_service as drive, drive_storage
    client_id = int(client_id)
    out = {"client_id": client_id, "location": drive_storage.location(client_id).get("kind"),
           "media_root": drive_storage.shared_parent_id(), "at": int(time.time())}
    try:
        with _sa():
            out["service_account"] = drive.service_account_email()
    except Exception as e:
        out["service_account"] = ""
        out["error"] = f"service account key unreadable ({type(e).__name__})"
        return out
    out["stored_folder_id"] = drive_mirror._hinted_folder(client_id)
    with _sa():
        drive_mirror._root_cache.pop(client_id, None)
        out["resolved_folder_id"] = drive_mirror.shared_client_root(client_id)
        folders = []
        try:
            named = folders_named_like(client_id)
        except Exception as e:
            named, out["folders_error"] = [], drive.classify_error(e)
        for f in named:
            try:
                walk = _walk(f["id"], TREE_MAX_FILES, with_files=True)
            except Exception as e:
                walk = {"error": drive.classify_error(e), "tree": []}
            folders.append({"id": f.get("id"), "name": f.get("name"), "parents": f.get("parents") or [],
                            "shared_drive": f.get("driveId", ""), "created": f.get("createdTime", ""),
                            "connected": f.get("id") == out["resolved_folder_id"],
                            "under_media_root": out["media_root"] in (f.get("parents") or []), **walk})
    out["folders_named_like_client"] = folders
    # What the agents and the Media tab are built from (drive_mirror.catalog),
    # next to the raw tree of the connected folder: any file in Drive that the
    # catalog lacks is named here — the client-27 test3.mp4 miss, made visible.
    keep = ("id", "name", "folder", "kind", "size", "modified_time")
    connected = next((f for f in folders if f.get("connected")), None)
    try:
        with drive_storage.scope(client_id):
            cat = drive_mirror.catalog(client_id, refresh=True)
        cat_ids = {i.get("id") for i in cat.get("items") or []}
        out["catalog_files"] = len(cat_ids)
        if connected and not connected.get("error") and not connected.get("truncated") and not cat.get("truncated"):
            out["catalog_missing"] = [{k: t.get(k) for k in keep if k in t} for t in connected.get("tree") or []
                                      if t.get("mime_type") != drive.FOLDER_MIME and t.get("id") not in cat_ids]
    except Exception as e:
        out["catalog_error"] = drive.classify_error(e)
    if find:
        # EXACTLY the agents' find_media_file search (agent_actions.find_media_lookup)
        try:
            from core import agent_actions
            hit = agent_actions.find_media_lookup(client_id, find)
            out["find_in_client_folder"] = [{k: m.get(k) for k in keep} for m in hit.get("matches") or []]
        except Exception as e:
            out["find_in_client_folder_error"] = drive.classify_error(e)
        # EXACTLY the Media tab's search box (media_library.view)
        try:
            from core import media_library
            tab = media_library.view(client_id, q=find, limit=10)
            if tab.get("error"):
                out["find_in_media_tab_error"] = tab["error"]
            else:
                out["find_in_media_tab"] = [{k: m.get(k) for k in keep} for m in tab.get("items") or []]
        except Exception as e:
            out["find_in_media_tab_error"] = drive.classify_error(e)
        try:
            out["find_anywhere"] = drive_pull.find_anywhere(find, client_id)
        except Exception as e:
            out["find_anywhere_error"] = drive.classify_error(e)
    return out
