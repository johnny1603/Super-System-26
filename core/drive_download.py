"""Preview + direct download of files in a client's Drive media folder — the
Media tab (client session) and the Control Room (admin session).

Builds on core/drive_mirror: same folder (the client's root from OUR record,
drive_mirror.client_root_id), same service account. Files the agents created
(client_activity rows carrying a file_id) and files Johnny dropped into Drive
by hand go through the SAME endpoint, because the authority is Drive's own
`parents` chain, never the activity row: a stale row whose file was moved
or deleted is refused like any other id.

Rules this module holds:
- **Never trust a file id from the browser.** Every request re-walks the
  file's parents up to the client's root, fresh (not drive_mirror's
  long-lived folder cache, so a subfolder moved to another client stops
  working at once). A verified (client, file) pair is remembered for
  VERIFY_SECONDS, so the lightbox preview + the download of one file are not
  six Drive calls each. The media root, other clients' folders, folders and
  shortcuts (a shortcut can point anywhere) are refused.
- **Stream, never buffer.** alt=media / export, CHUNK_BYTES at a time, piped
  to the response. No Content-Length on purpose: Cloud Run caps a
  non-streamed HTTP/1 response at 32 MiB; a chunked response is exempt and
  only the service's request timeout (--timeout=300 here, on the backend AND
  on the app.uallak.com proxy) bounds it. That timeout is why there is a
  per-file cap (admin setting `media_download_max_mb`): above it the UI says
  "open it in Drive", where Google serves the download itself.
- **Inline only for raster images** (jpeg/png/webp/gif) and only on request
  (?inline=1). This origin carries the dashboard session cookie, so SVG,
  HTML, XML, JS and anything unknown are attachment-only and sent as
  application/octet-stream; every response has nosniff and a sandbox CSP.
- Google Docs/Sheets/Slides/Drawings have no bytes to download: they are
  EXPORTED (PDF by default, Office format on request). Drive's export
  endpoint caps its output at 10 MB — larger exports get the same "too large,
  open in Drive" answer.
- **Per-client rate limit** (in memory, per instance — a soft brake against a
  runaway loop, not an accounting system) and the bytes actually sent are
  logged to client_costs (`drive_download_egress`, details.bytes) so the
  admin cost view can see egress.
- Nothing here ever reaches a chat prompt.
"""
import os
import re
import threading
import time
import unicodedata
from urllib.parse import quote

from core import drive_mirror
from core import drive_service as drive

SERVICE_NAME = "drive_download"

CHUNK_BYTES = 256 * 1024
VERIFY_SECONDS = 180
VERIFY_CACHE_ENTRIES = 5000
DEFAULT_MAX_MB = 100           # admin setting media_download_max_mb overrides
MAX_MB_CEILING = 2000
PREVIEW_MAX_BYTES = 25 * 1024 * 1024   # inline fallback preview (raster only)
SETTINGS_CACHE_SECONDS = 60
EXPORT_LIMIT_BYTES = 10 * 1024 * 1024  # Drive's own files.export output cap

RATE_WINDOW_SECONDS = 600
RATE_MAX_REQUESTS = 60         # downloads + previews per client per window
RATE_MAX_CONCURRENT = 3
SLOT_MAX_SECONDS = 3600        # a slot never outlives Cloud Run's max request time

# Cloud Run internet egress, Premium Tier list price (USD/GB) — an ESTIMATE,
# like every client_costs figure. The proxy hop doubles part of it.
EGRESS_USD_PER_GB = 0.12
COST_CATEGORY = "drive_download_egress"

RASTER_INLINE = {"image/jpeg", "image/png", "image/webp", "image/gif"}
# Types that are safe to label truthfully on an attachment; anything else goes
# out as application/octet-stream.
SAFE_ATTACHMENT_TYPES = RASTER_INLINE | {
    "application/pdf", "application/zip", "text/plain", "text/csv",
    "application/msword", "application/vnd.ms-excel", "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
_PDF = ("application/pdf", ".pdf")
EXPORTS = {
    "application/vnd.google-apps.document": {
        "pdf": _PDF,
        "office": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx")},
    "application/vnd.google-apps.spreadsheet": {
        "pdf": _PDF,
        "office": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx")},
    "application/vnd.google-apps.presentation": {
        "pdf": _PDF,
        "office": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx")},
    "application/vnd.google-apps.drawing": {"pdf": _PDF},
}
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"

FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,200}$")

_verified = {}                 # (client_id, file_id) -> (at, root)
_settings = {"at": 0.0, "max_mb": DEFAULT_MAX_MB}
_rate_lock = threading.Lock()
_recent = {}                   # rate key -> [timestamps]
_active = {}                   # rate key -> [slot start times]


class DownloadRefused(Exception):
    """A request we answer with a calm message, not a crash.
    code: not_found | too_large | not_downloadable | busy | unavailable"""
    STATUS = {"not_found": 404, "too_large": 413, "not_downloadable": 415,
              "busy": 429, "unavailable": 503}

    def __init__(self, code: str, **extra):
        super().__init__(code)
        self.code = code
        self.status = self.STATUS.get(code, 400)
        self.extra = extra


# ─── What a file is ───────────────────────────────────────────────────────────

def download_kind(mime: str) -> str:
    """"file" (alt=media), "export" (Google-native) or "none"."""
    if mime in EXPORTS:
        return "export"
    if not mime or mime == drive.FOLDER_MIME or mime.startswith("application/vnd.google-apps."):
        return "none"   # folders, shortcuts, Forms, Sites, Maps: nothing to download
    return "file"


def is_previewable(mime: str) -> bool:
    return mime in RASTER_INLINE


def max_bytes() -> int:
    """The per-file proxy cap from admin settings (cached briefly)."""
    if _settings["at"] < time.time() - SETTINGS_CACHE_SECONDS:
        try:
            from core import admin_service
            value = admin_service.get_settings().get("media_download_max_mb", DEFAULT_MAX_MB)
            _settings["max_mb"] = max(1, min(MAX_MB_CEILING, int(float(value))))
        except Exception as e:
            print(f"[{SERVICE_NAME}] settings read failed ({e}) — keeping {_settings['max_mb']} MB")
        _settings["at"] = time.time()
    return _settings["max_mb"] * 1024 * 1024


# ─── Ownership ────────────────────────────────────────────────────────────────

def _verify_in_client_tree(client_id: int, root: str, file_id: str, parent: str):
    """Walk parents from the file up to THIS client's root, fresh. Raises
    DownloadRefused("not_found") when the chain never reaches it."""
    hit = _verified.get((client_id, file_id))
    if hit and hit[1] == root and hit[0] > time.time() - VERIFY_SECONDS:
        return
    media_root = os.environ.get("DRIVE_MEDIA_FOLDER_ID", "")
    current, hops = parent, 0
    while current and hops <= drive_mirror.MAX_PARENT_HOPS:
        if current == root:
            if len(_verified) > VERIFY_CACHE_ENTRIES:
                _verified.clear()
            _verified[(client_id, file_id)] = (time.time(), root)
            return
        if current == media_root:
            break   # reached the shared media root without passing this client's folder
        try:
            parents = drive.get_meta(current, "id,parents").get("parents") or []
        except Exception as e:
            print(f"[{SERVICE_NAME}] parent walk for client {client_id} failed at {current}: {e}")
            if drive.classify_error(e, target="file") in ("file_not_found", "file_permission"):
                break
            raise DownloadRefused("unavailable")
        current, hops = (parents[0] if parents else ""), hops + 1
    print(f"[{SERVICE_NAME}] client {client_id}: refused file {file_id} (not in their folder)")
    raise DownloadRefused("not_found")


# ─── Planning a request (all checks, no body yet) ─────────────────────────────

def plan(client_id: int, file_id: str, inline: bool = False, fmt: str = "pdf") -> dict:
    """Every check for one preview/download, without touching the file body.
    Raises DownloadRefused. Returns what open_stream() needs."""
    if not FILE_ID_RE.match(file_id or ""):
        raise DownloadRefused("not_found")
    root = drive_mirror.client_root_id(client_id)
    if not root or file_id == root:
        raise DownloadRefused("not_found")
    try:
        meta = drive.get_meta(file_id, "id,name,mimeType,size,parents,trashed,webViewLink")
    except Exception as e:
        reason = drive.classify_error(e, target="file")
        print(f"[{SERVICE_NAME}] client {client_id}: metadata for {file_id} failed ({reason}): {e}")
        if reason in ("file_not_found", "file_permission"):
            raise DownloadRefused("not_found")
        raise DownloadRefused("unavailable")
    if meta.get("trashed"):
        raise DownloadRefused("not_found")
    mime = meta.get("mimeType") or ""
    if mime == drive.FOLDER_MIME:
        raise DownloadRefused("not_found")
    _verify_in_client_tree(client_id, root, file_id, (meta.get("parents") or [""])[0])

    link = meta.get("webViewLink") or ""
    kind = download_kind(mime)
    if kind == "none":
        raise DownloadRefused("not_downloadable", web_view_link=link)
    size = int(meta["size"]) if str(meta.get("size") or "").isdigit() else None
    cap = max_bytes()

    export_mime, ext = "", ""
    if kind == "export":
        if inline:
            raise DownloadRefused("not_downloadable", web_view_link=link)
        formats = EXPORTS[mime]
        export_mime, ext = formats.get(fmt) or formats["pdf"]
        send_mime = export_mime
        limit = EXPORT_LIMIT_BYTES
    else:
        if inline and not is_previewable(mime):
            raise DownloadRefused("not_downloadable", web_view_link=link)
        limit = min(cap, PREVIEW_MAX_BYTES) if inline else cap
        if size is not None and size > limit:
            raise DownloadRefused("too_large", web_view_link=link, max_mb=limit // (1024 * 1024))
        send_mime = mime if (inline or mime in SAFE_ATTACHMENT_TYPES) else "application/octet-stream"
    return {"file_id": file_id, "mime": mime, "send_mime": send_mime, "size": size,
            "export_mime": export_mime, "inline": bool(inline), "limit": limit,
            "filename": safe_filename(meta.get("name") or "", ext), "web_view_link": link,
            "max_mb": cap // (1024 * 1024)}


# ─── File names ───────────────────────────────────────────────────────────────

def safe_filename(name: str, ext: str = "") -> str:
    """The original name, kept readable (Hebrew stays Hebrew), minus control
    and bidi-format characters (a U+202E "right-to-left override" can make
    'gpj.exe' display as 'exe.jpg') and path/quote characters."""
    name = unicodedata.normalize("NFC", name or "")
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")
    name = re.sub(r'[\\/:*?"<>|]+', "_", name).strip(" .")
    if ext and not name.lower().endswith(ext):
        name = (name or "file") + ext
    if len(name) > 150:
        stem, dot, tail = name.rpartition(".")
        name = (stem[:140] + "." + tail) if dot and len(tail) <= 8 else name[:150]
    return name or "file" + ext


def content_disposition(disposition: str, filename: str) -> str:
    """RFC 6266 header with an ASCII fallback and the real name in RFC 5987
    filename* (UTF-8, percent-encoded) — so the header itself is pure ASCII."""
    if all(32 <= ord(c) < 127 and c not in '"\\%;' for c in filename):
        fallback = filename
    else:   # e.g. a Hebrew name: every current browser reads filename* instead
        _, dot, tail = filename.rpartition(".")
        fallback = "download" + ("." + tail if dot and tail.isascii() and tail.isalnum() else "")
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"


# ─── Rate limit ───────────────────────────────────────────────────────────────

def _prune(key: str, now: float):
    _recent[key] = [t for t in _recent.get(key, []) if t > now - RATE_WINDOW_SECONDS]
    _active[key] = [t for t in _active.get(key, []) if t > now - SLOT_MAX_SECONDS]


def check_rate(key: str):
    """Raise DownloadRefused("busy") if this key is over its limits (no slot taken)."""
    with _rate_lock:
        now = time.time()
        _prune(key, now)
        if len(_recent[key]) >= RATE_MAX_REQUESTS or len(_active[key]) >= RATE_MAX_CONCURRENT:
            raise DownloadRefused("busy")


def _acquire(key: str) -> float:
    with _rate_lock:
        now = time.time()
        _prune(key, now)
        if len(_recent[key]) >= RATE_MAX_REQUESTS or len(_active[key]) >= RATE_MAX_CONCURRENT:
            raise DownloadRefused("busy")
        _recent[key].append(now)
        _active[key].append(now)
        return now


def _release(key: str, slot: float):
    with _rate_lock:
        try:
            _active.get(key, []).remove(slot)
        except ValueError:
            pass


# ─── Streaming ────────────────────────────────────────────────────────────────

def response_headers(p: dict) -> dict:
    disposition = "inline" if p["inline"] else "attachment"
    return {
        "Content-Disposition": content_disposition(disposition, p["filename"]),
        "X-Content-Type-Options": "nosniff",
        # If a response is ever opened as a document, it can run nothing
        "Content-Security-Policy": "sandbox; default-src 'none'; img-src 'self'; style-src 'unsafe-inline'",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "private, max-age=300" if p["inline"] else "private, no-store",
    }


def open_stream(p: dict, client_id: int, by: str = "client"):
    """Open the Drive stream for a plan() result and return a generator of
    chunks. Takes a rate-limit slot (released when the generator finishes,
    is closed, or is garbage-collected after a client disconnect) and logs
    the bytes actually sent. Raises DownloadRefused before any byte is sent."""
    key = f"{by}:{client_id}"
    slot = _acquire(key)
    try:
        http, upstream = drive.open_download_stream(p["file_id"], p["export_mime"])
    except Exception as e:
        _release(key, slot)
        text = str(e).lower()
        reason = drive.classify_error(e, target="file")
        print(f"[{SERVICE_NAME}] client {client_id}: opening {p['file_id']} failed ({reason}): {e}")
        if "exportsizelimitexceeded" in text or "too large to be exported" in text:
            raise DownloadRefused("too_large", web_view_link=p["web_view_link"],
                                  max_mb=EXPORT_LIMIT_BYTES // (1024 * 1024))
        if reason in ("file_not_found", "file_permission"):
            raise DownloadRefused("not_found")
        raise DownloadRefused("unavailable")

    def body():
        sent, complete = 0, False
        try:
            for chunk in upstream.iter_bytes(CHUNK_BYTES):
                sent += len(chunk)
                if sent > p["limit"]:
                    # Drive's size was missing or wrong: stop rather than pass the cap
                    print(f"[{SERVICE_NAME}] client {client_id}: {p['file_id']} passed the cap mid-stream")
                    break
                yield chunk
            else:
                complete = True
        except Exception as e:
            print(f"[{SERVICE_NAME}] client {client_id}: stream of {p['file_id']} broke after {sent} bytes: {e}")
        finally:
            for closer in (upstream.close, http.close):
                try:
                    closer()
                except Exception:
                    pass
            _release(key, slot)
            _log_bytes(client_id, p, sent, complete, by)
    return body()


def _log_bytes(client_id: int, p: dict, sent: int, complete: bool, by: str):
    """Fire-and-forget on a thread: this can run from a generator finalizer."""
    details = {"bytes": sent, "file_id": p["file_id"], "mime": p["mime"],
               "export": bool(p["export_mime"]), "purpose": "preview" if p["inline"] else "download",
               "complete": complete, "by": by, "estimate": True}
    print(f"[{SERVICE_NAME}] client {client_id}: sent {sent} bytes of {p['file_id']} "
          f"({details['purpose']}, by {by}, complete={complete})")

    def write():
        from core.cost_tracker import record_cost, usd_to_ils
        record_cost(COST_CATEGORY, usd_to_ils(sent / 1e9 * EGRESS_USD_PER_GB), client_id, details)
    threading.Thread(target=write, daemon=True).start()


# ─── Admin cost view ──────────────────────────────────────────────────────────

def egress_summary(since_iso: str, client_id: int = None) -> dict:
    """Bytes sent through the proxy since a date — from details.bytes, because
    per-download amounts are fractions of an agora and vanish when the cost
    view rounds category totals to 2 decimals."""
    try:
        from core.cost_tracker import _db, usd_to_ils
        query = (_db().table("client_costs").select("client_id, details")
                 .eq("category", COST_CATEGORY).gte("created_at", since_iso).limit(10000))
        if client_id is not None:
            query = query.eq("client_id", client_id)
        rows = query.execute().data or []
    except Exception as e:
        print(f"[{SERVICE_NAME}] egress summary read failed: {e}")
        return {"bytes": 0, "downloads": 0, "est_ils": 0, "available": False}
    total = sum(int((r.get("details") or {}).get("bytes") or 0) for r in rows)
    downloads = sum(1 for r in rows if (r.get("details") or {}).get("purpose") == "download")
    return {"bytes": total, "downloads": downloads, "previews": len(rows) - downloads,
            "est_ils": round(usd_to_ils(total / 1e9 * EGRESS_USD_PER_GB), 2), "available": True}
