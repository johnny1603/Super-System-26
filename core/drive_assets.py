"""The index of a client's connected Drive folders, and the agents' lookup
(2026-11-23, docs/DRIVE-FOLDERS.md, knowledge/social-craft.md §10).

The business owner supplies a pool (core/drive_folders); the media agent
finds and CHOOSES from it on its own (find_assets) — the owner is never asked
to pick a file.

Indexing is cost-efficient, in this order:
  a. Tier 0, free: file name, path, folder role, dates, dimensions, EXIF
     (Drive's own image/video metadata). A file whose NAME already describes
     it is indexed from the name — no vision call at all.
  b. Dedupe before describing: a perceptual hash (dHash on the preview)
     groups near-identical images in the same folder (a product shot from
     many angles); ONE per group is described, the others get its
     description with an angle note.
  c. Every image sent to a model is downscaled to 512 px on its longest side
     (Drive's thumbnail at =s512, then Pillow).
  d. The cheapest capable vision model (claude_json.CLASSIFY_MODEL), a
     structured answer per core/asset_describe (subject, colours, framing,
     background, person, light, text/logo, usability + controlled tags,
     category-aware, versioned), up to 8 images per call.
  e. Newest first; older files on demand (the "index more" button, a credit
     load, or an agent's search that hits a not-yet-described file).
VIDEO: the first usable frame (black frames skipped) is described exactly
like one image — one frame, never several; duration, size and format come
from Drive's metadata; media_type "video".

FREE CAP: client_settings `drive_assets_free_items` (default 150) indexed
items per client. The ACTUAL cost is metered from the model calls' usage and
the running average per item is stored. At the cap indexing stops — nothing
more is spent — a quote for the remaining items is computed from that
measured average, and a billing request opens for a uallak representative.
The client sees a plain Hebrew line. Credits loaded by the team
(load_credits) resume indexing where it stopped.

The work runs in short steps inside requests (Cloud Run throttles CPU after a
response): the settings page's poll, the every-minute scheduler tick (with
what is left of its budget) and the scheduled check (`/api/drive/assets/scan`).
The scheduled check does NOT walk folders: it asks Drive's changes feed what
changed since each folder's stored token (check_changes) and indexes only
those files; a folder is walked when it is first connected, re-indexed, its
token is missing / refused, or a subfolder appeared. A chat ("I uploaded the
photos" — core/drive_index_chat) and an agent's first look at a never-indexed
folder (ensure_indexed) index at once. At use time a file that is gone or
moved is marked "missing" and skipped (verify_usable / mark_missing).

Storage: app_settings `drive_assets:<client_id>` (+ a lease row
`drive_assets_lock:<client_id>`); cleared with the client.
"""
import base64
import io
import re
import secrets
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone

from core import drive_folders

SERVICE_NAME = "drive_assets"
PREFIX = "drive_assets:"
LOCK_PREFIX = "drive_assets_lock:"
DEFAULT_FREE_ITEMS = 150
DEFAULT_BATCH_ITEMS = 60
MAX_ITEMS = 4000
VISION_BATCH = 8
PREVIEW_PX = 512
GROUP_DISTANCE = 10                 # of 64 dHash bits: near-identical / another angle
MAX_ATTEMPTS = 3
LEASE_S = 300
STEP_MARGIN_S = 12
MAX_PAGES = 50
PREVIEW_MAX_BYTES = 4 * 1024 * 1024
DOWNLOAD_MAX_BYTES = 15 * 1024 * 1024
THUMB_FRESH_S = 3 * 3600
FRAME_OFFSETS_S = (0.0, 0.5, 1.0, 2.0, 4.0, 8.0)
FFMPEG_TIMEOUT_S = 30
MODES = ("add", "manual", "daily", "resume", "on_demand", "chat")
INVALID_TOKEN_STATUS = (400, 404, 410)     # Drive: the change page token is no longer valid
CHANGE_PAGES_MAX = 20
CHANGE_FIELDS = ("nextPageToken,newStartPageToken,changes(fileId,removed,file(id,name,mimeType,modifiedTime,"
                 "createdTime,size,md5Checksum,thumbnailLink,trashed,parents,"
                 "imageMediaMetadata(width,height,time,cameraMake,cameraModel,location),"
                 "videoMediaMetadata(width,height,durationMillis)))")
# Never indexed: the client's social_output folder holds finished output (older
# publish snapshots, our posts) — indexing it would count published assets twice.
# The system's own backup folder (core/drive_backup) is an excluded subfolder.
NOT_INDEXED_ROLE = "social_output"
# Index entries that are NOT usable assets: "removed" by the media library,
# "missing" = the file was gone / moved when an agent went to use it
OUT_STATES = ("removed", "missing")
LIST_FIELDS = ("nextPageToken,files(id,name,mimeType,modifiedTime,createdTime,size,md5Checksum,thumbnailLink,"
               "trashed,shortcutDetails(targetId,targetMimeType),"
               "imageMediaMetadata(width,height,time,cameraMake,cameraModel,location),"
               "videoMediaMetadata(width,height,durationMillis))")
_HOLDER = uuid.uuid4().hex[:10]

STATE_TEXT = {
    "idle": "",
    "queued": "האינדוקס יתחיל עוד רגע.",
    "running": "מאנדקסים את הקבצים — החדשים קודם.",
    "cap": ("הגעתם ל-{free} הפריטים שאנחנו מאנדקסים בחינם. עצרנו כאן ולא נחייב דבר בלי אישורכם. "
            "נשארו עוד {remaining} קבצים; לפי העלות שמדדנו בפועל (כ-{per} ₪ לקובץ) זה בערך {quote} ₪. "
            "נציג uallak ייצור איתכם קשר, ואחרי טעינת הקרדיטים האינדוקס ימשיך בדיוק מאיפה שעצר."),
    "done": "הכול מאונדקס. סריקה אוטומטית רצה פעם ביום ומוסיפה קבצים חדשים.",
    "older": "{pending} קבצים ישנים יותר עוד לא תוארו — הם יתוארו כשסוכן יחפש אותם, או בלחיצה על \"אינדוקס עכשיו\".",
}


def _db():
    from agents.client_agent import _db as db
    return db()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt=None) -> str:
    return (dt or _now()).isoformat()


def _age_s(value) -> float:
    try:
        return (_now() - datetime.fromisoformat(str(value))).total_seconds()
    except (TypeError, ValueError):
        return 1e12


def _log(line: str):
    try:
        from core.agent_base import log_step
        log_step(SERVICE_NAME, "index", line)
    except Exception:
        print(f"[{SERVICE_NAME}] {line}")


def _alert(lines: list):
    try:
        from core.agent_base import agent_alert
        agent_alert(SERVICE_NAME, lines)
    except Exception:
        pass


# ─── Settings (client_settings: editable per client in the Control Room) ────

def settings(client_id: int) -> dict:
    try:
        from core import client_settings
        s = client_settings.get(client_id)
    except Exception:
        s = {}
    free = s.get("drive_assets_free_items")
    batch = s.get("drive_assets_batch_items")
    return {"free_items": int(free if free is not None else DEFAULT_FREE_ITEMS),
            "batch_items": int(batch if batch is not None else DEFAULT_BATCH_ITEMS)}


# ─── Storage ─────────────────────────────────────────────────────────────────

def _load(client_id: int) -> dict:
    rows = (_db().table("app_settings").select("value").eq("key", f"{PREFIX}{int(client_id)}")
            .limit(1).execute().data or [])
    value = rows[0].get("value") if rows else None
    value = value if isinstance(value, dict) else {}
    value.setdefault("items", {})
    value.setdefault("meter", {"items": 0, "usd": 0.0, "vision_items": 0, "calls": 0, "avg_usd": 0.0})
    value.setdefault("credits", {"extra_items": 0, "history": []})
    value.setdefault("cap", {})
    value.setdefault("job", {"state": "idle"})
    return value


def _save(client_id: int, state: dict):
    _db().table("app_settings").upsert({"key": f"{PREFIX}{int(client_id)}", "value": state,
                                        "updated_at": _iso()}, on_conflict="key").execute()


def _lease_take(client_id) -> str:
    key, token = f"{LOCK_PREFIX}{int(client_id)}", f"{_HOLDER}:{uuid.uuid4().hex[:6]}"
    value = {"token": token, "until": _iso(_now() + timedelta(seconds=LEASE_S))}
    for _ in range(2):
        try:
            _db().table("app_settings").insert({"key": key, "value": value, "updated_at": _iso()}).execute()
            return token
        except Exception:
            rows = (_db().table("app_settings").select("value").eq("key", key).limit(1).execute().data) or []
            held = (rows[0].get("value") if rows else {}) or {}
            if rows and _age_s(held.get("until")) < 0:
                return ""
            q = _db().table("app_settings").delete().eq("key", key)
            if held.get("token"):
                q = q.eq("value->>token", held["token"])
            q.execute()
    return ""


def _lease_release(client_id, token):
    try:
        (_db().table("app_settings").delete().eq("key", f"{LOCK_PREFIX}{int(client_id)}")
         .eq("value->>token", token).execute())
    except Exception:
        pass


# ─── Tier 0: what the file already tells us (free) ──────────────────────────

_CAMERA_RE = re.compile(r"^(img|image|dsc[nf]?|pxl|mvimg|vid|video|mov|gopr|gp|dji|photo|pic|picture|"
                        r"screenshot|screen ?shot|scan|whatsapp|wa|signal|telegram|snapchat|fb|insta|received|"
                        r"download|untitled|new|copy|file|clip|record|rec)[\W_]*\d", re.IGNORECASE)
_GENERIC = {"img", "image", "images", "photo", "photos", "pic", "picture", "pictures", "dsc", "pxl", "vid", "video",
            "videos", "mov", "screenshot", "screen", "shot", "whatsapp", "final", "copy", "edited", "edit", "new",
            "untitled", "file", "jpeg", "jpg", "png", "heic", "mp4", "webp", "wa", "signal", "received",
            "download", "version", "ver", "draft", "export", "עותק", "סופי", "חדש", "חדשה", "תמונה", "תמונות",
            "צילום", "מסך", "סרטון", "סרטונים", "וידאו", "תמונת", "ללא", "שם", "טיוטה", "גרסה"}


def descriptive_name(name: str) -> dict:
    """{"description", "tags"} when the file NAME already describes it
    (two or more meaningful words, not a camera / app default name) — the
    vision call is skipped. {} otherwise."""
    from core import media_search
    stem = media_search.stem(name)
    if not stem or _CAMERA_RE.match(stem.strip()):
        return {}
    words = media_search.words(stem)
    meaningful = [w for w in words if not w.isdigit() and len(w) >= 2 and w not in _GENERIC
                  and not re.fullmatch(r"[0-9a-f]{6,}", w) and sum(ch.isalpha() for ch in w) >= 2]
    if len(meaningful) < 2 or len(meaningful) < len([w for w in words if not w.isdigit()]) / 2:
        return {}
    description = re.sub(r"\s+", " ", re.sub(r"[_\-.]+", " ", stem)).strip()[:200]
    return {"description": description, "tags": list(dict.fromkeys(meaningful))[:5]}


def _ext(name: str, mime: str) -> str:
    match = re.search(r"\.([A-Za-z0-9]{2,5})$", str(name or ""))
    return (match.group(1) if match else str(mime or "").split("/")[-1]).lower()[:8]


def tier0(raw: dict, node: dict) -> dict:
    """The free facts of one Drive file (never a coordinate: only whether
    the photo carries a location)."""
    mime = raw.get("mimeType") or ""
    image = raw.get("imageMediaMetadata") or {}
    video = raw.get("videoMediaMetadata") or {}
    is_video = mime.startswith("video/")
    try:
        size = int(raw.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    exif = {}
    if image.get("time"):
        exif["taken"] = str(image["time"])[:20]
    camera = " ".join(str(image.get(k) or "").strip() for k in ("cameraMake", "cameraModel")).strip()
    if camera:
        exif["camera"] = camera[:60]
    if image.get("location"):
        exif["has_location"] = True
    try:
        duration = round(int(video.get("durationMillis") or 0) / 1000, 1) if is_video else None
    except (TypeError, ValueError):
        duration = None
    return {"file_id": raw.get("id", ""), "name": str(raw.get("name") or "")[:200], "mime": mime,
            "media_type": "video" if is_video else "image", "format": _ext(raw.get("name"), mime),
            "path": str(node.get("path") or "")[:300], "connection_id": node.get("conn_id", ""),
            "root_id": node.get("root_id", ""), "created": raw.get("createdTime", ""),
            "modified": raw.get("modifiedTime", ""), "size": size, "md5": raw.get("md5Checksum", ""),
            "width": int((video if is_video else image).get("width") or 0) or None,
            "height": int((video if is_video else image).get("height") or 0) or None,
            "duration_s": duration, "exif": exif,
            "thumb_link": raw.get("thumbnailLink", ""), "thumb_at": _iso() if raw.get("thumbnailLink") else ""}


# ─── Previews (512 px), the perceptual hash, black frames ───────────────────

def _pil():
    try:
        from PIL import Image, ImageStat
        return Image, ImageStat
    except Exception:
        return None, None


def downscale(data: bytes, px: int = PREVIEW_PX) -> bytes:
    """JPEG, longest side ≤ px. Without Pillow the bytes pass unchanged
    (Drive's own =s512 thumbnail is already that size)."""
    Image, _ = _pil()
    if not Image or not data:
        return data
    try:
        img = Image.open(io.BytesIO(data))
        img = img.convert("RGB")
        img.thumbnail((px, px))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=82)
        return out.getvalue()
    except Exception:
        return b""


def dhash(data: bytes) -> str:
    """64-bit difference hash, hex; "" without Pillow or on unreadable bytes."""
    Image, _ = _pil()
    if not Image or not data:
        return ""
    try:
        img = Image.open(io.BytesIO(data)).convert("L").resize((9, 8))
        px = list(img.getdata())
        bits = 0
        for row in range(8):
            for col in range(8):
                bits = (bits << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
        return f"{bits:016x}"
    except Exception:
        return ""


def hamming(a: str, b: str) -> int:
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except (TypeError, ValueError):
        return 64


def is_black(data: bytes) -> bool:
    """A black / near-uniform dark frame (fade-in, lens cap)."""
    Image, ImageStat = _pil()
    if not Image or not data:
        return False
    try:
        stat = ImageStat.Stat(Image.open(io.BytesIO(data)).convert("L"))
        mean, dev = stat.mean[0], stat.stddev[0]
        return mean < 16 or (mean < 40 and dev < 6)
    except Exception:
        return False


def _sized_link(link: str, px: int = PREVIEW_PX) -> str:
    return re.sub(r"=s\d+(-[a-z0-9-]+)?$", "", str(link or "")) + f"=s{px}"


def fetch_preview(raw: dict) -> bytes:
    """A Drive file's thumbnail at 512 px (as the service account),
    downscaled — b"" when there is none."""
    link = raw.get("thumbnailLink") or raw.get("thumb_link") or ""
    if not link:
        return b""
    from core import drive_service, drive_storage
    try:
        with drive_storage.as_service_account():
            data, _type = drive_service.fetch_thumbnail(_sized_link(link), PREVIEW_MAX_BYTES)
    except Exception:
        return b""
    return downscale(data)


def _fresh_thumb_link(item: dict) -> str:
    if item.get("thumb_link") and _age_s(item.get("thumb_at")) < THUMB_FRESH_S:
        return item["thumb_link"]
    from core import drive_service, drive_storage
    try:
        with drive_storage.as_service_account():
            link = drive_service.get_meta(item["file_id"], "id,thumbnailLink").get("thumbnailLink") or ""
    except Exception:
        link = ""
    item["thumb_link"], item["thumb_at"] = link, _iso() if link else ""
    return link


def _download_image(item: dict) -> bytes:
    """Fallback when Drive has no thumbnail: the file itself (≤ 15 MB)."""
    if not item.get("size") or item["size"] > DOWNLOAD_MAX_BYTES:
        return b""
    from core import drive_service, drive_storage
    try:
        with drive_storage.as_service_account():
            client, response = drive_service.open_download_stream(item["file_id"])
            try:
                chunks, total = [], 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > DOWNLOAD_MAX_BYTES:
                        return b""
                    chunks.append(chunk)
            finally:
                response.close()
                client.close()
    except Exception:
        return b""
    return downscale(b"".join(chunks))


def _ffmpeg_frame(file_id: str, at_s: float) -> bytes:
    """ONE decoded frame at `at_s`, scaled to ≤ 512 px, as JPEG — streamed
    from Drive by ffmpeg with Range requests (the file is never downloaded
    whole). b"" when ffmpeg is missing or fails. The bearer token is passed
    to the child process only; the command is never logged."""
    binary = shutil.which("ffmpeg")
    if not binary:
        return b""
    from core import drive_service
    try:
        token = drive_service._access_token()
    except Exception:
        return b""
    url = f"{drive_service.DRIVE_FILES_URL}/{file_id}?alt=media&supportsAllDrives=true"
    args = [binary, "-v", "error", "-nostdin", "-threads", "1",
            "-headers", f"Authorization: Bearer {token}\r\n",
            "-ss", f"{float(at_s):.2f}", "-i", url, "-frames:v", "1",
            "-vf", f"scale='min({PREVIEW_PX},iw)':'min({PREVIEW_PX},ih)':force_original_aspect_ratio=decrease",
            "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"]
    try:
        run = subprocess.run(args, capture_output=True, timeout=FFMPEG_TIMEOUT_S)
    except Exception:
        return b""
    return run.stdout if run.returncode == 0 and run.stdout[:2] == b"\xff\xd8" else b""


def video_frame(item: dict) -> tuple:
    """(jpeg, {"frame_at_s", "frame_source"}) — the FIRST usable frame:
    offsets 0, 0.5, 1, 2, 4, 8 s, the first that is not black wins. When
    ffmpeg can't read it, Drive's own video thumbnail (if not black).
    (b"", {}) when there is no usable frame."""
    duration = item.get("duration_s") or 0
    for at in FRAME_OFFSETS_S:
        if duration and at >= duration:
            break
        frame = _ffmpeg_frame(item["file_id"], at)
        if not frame:
            break                                  # ffmpeg can't read it: no point trying later offsets
        if not is_black(frame):
            return frame, {"frame_at_s": at, "frame_source": "first_frame"}
    link = _fresh_thumb_link(item)
    thumb = fetch_preview({"thumbnailLink": link}) if link else b""
    if thumb and not is_black(thumb):
        return thumb, {"frame_at_s": None, "frame_source": "drive_thumbnail"}
    return b"", {}


def _preview_for(item: dict) -> tuple:
    if item["media_type"] == "video":
        return video_frame(item)
    link = _fresh_thumb_link(item)
    data = fetch_preview({"thumbnailLink": link}) if link else b""
    if not data:
        data = _download_image(item)
    return data, {}


# ─── The vision call (cheapest capable model, batched, metered) ─────────────

# What a description must contain, the category profiles, the controlled tag
# vocabulary and the prompt version live in core/asset_describe.


def _vocab(client_id: int, state: dict) -> tuple:
    from core import asset_describe
    category = asset_describe.category_for(client_id)
    grown = ((state or {}).get("vocab") or {}).get(category) or []
    return category, asset_describe.vocabulary(category, grown)


def _grow_vocab(state: dict, category: str, new_tags: list):
    from core import asset_describe
    if not new_tags:
        return
    grown = state.setdefault("vocab", {}).setdefault(category, [])
    for tag in new_tags:
        if tag not in grown and len(grown) < asset_describe.MAX_GROWN_TAGS:
            grown.append(tag)


def _vision(client_id: int, leaders: list, category: str = None, vocab: list = None) -> tuple:
    """([{"description", "tags", "attributes", "new_tags"}…] in order, usd
    measured). Raises on a failed call; the usd of whatever was spent is in
    the exception's .usd."""
    from core import asset_describe
    from core.claude_json import CLASSIFY_MODEL, collect_usage, safe_claude_json_call
    from core.cost_tracker import USD_TO_ILS
    if category is None or vocab is None:
        category, vocab = _vocab(client_id, {})
    blocks = [{"type": "text", "text": f"{len(leaders)} items:"}]
    for n, (item, img, frame) in enumerate(leaders, 1):
        what = "first usable frame of a video" if item["media_type"] == "video" else "photo"
        blocks.append({"type": "text", "text": f"item {n} ({what}) — folder: {item.get('path', '')[:120]}; "
                                               f"file: {item.get('name', '')[:120]}"})
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                   "data": base64.b64encode(img).decode("ascii")}})
    with collect_usage() as sink:
        try:
            out = safe_claude_json_call(asset_describe.system_prompt(category, vocab), blocks,
                                        max_tokens=asset_describe.max_tokens(len(leaders)),
                                        model=CLASSIFY_MODEL, client_id=client_id,
                                        cost_category="claude_drive_index", timeout=90)
            error = None
        except Exception as e:
            out, error = None, e
    usd = round(sum(float(s.get("ils") or 0) for s in sink) / USD_TO_ILS, 6)
    if error is not None:
        error.usd = usd
        raise error
    rows = (out or {}).get("items") if isinstance(out, dict) else None
    rows = rows if isinstance(rows, list) else []
    by_n = {}
    for row in rows:
        if isinstance(row, dict):
            try:
                by_n[int(row.get("n"))] = row
            except (TypeError, ValueError):
                continue
    answers = []
    for n in range(1, len(leaders) + 1):
        row = by_n.get(n) or (rows[n - 1] if n - 1 < len(rows) and isinstance(rows[n - 1], dict) else {})
        answers.append(asset_describe.parse_item(row, vocab))
    return answers, usd


# ─── Meter, cap, quote, credits ──────────────────────────────────────────────

def allowance(client_id: int, state: dict) -> int:
    return settings(client_id)["free_items"] + int((state.get("credits") or {}).get("extra_items") or 0)


def _meter_add(state: dict, items: int, usd: float, vision_items: int = 0, calls: int = 0):
    m = state["meter"]
    m["items"] = int(m.get("items") or 0) + int(items)
    m["usd"] = round(float(m.get("usd") or 0) + float(usd or 0), 6)
    m["vision_items"] = int(m.get("vision_items") or 0) + int(vision_items)
    m["calls"] = int(m.get("calls") or 0) + int(calls)
    m["avg_usd"] = round(m["usd"] / m["items"], 6) if m["items"] else 0.0
    m["updated_at"] = _iso()


def _pending(state: dict, hidden: set) -> list:
    return [i for i in state["items"].values() if i.get("state") == "pending"
            and i.get("connection_id") not in hidden and int(i.get("attempts") or 0) < MAX_ATTEMPTS]


def quote(client_id: int, state: dict, hidden: set = None) -> dict:
    from core.cost_tracker import USD_TO_ILS
    remaining = len(_pending(state, hidden or set()))
    per = float(state["meter"].get("avg_usd") or 0)
    usd = round(remaining * per, 4)
    return {"remaining": remaining, "per_item_usd": round(per, 6), "per_item_ils": round(per * USD_TO_ILS, 4),
            "quote_usd": usd, "quote_ils": round(usd * USD_TO_ILS, 2),
            "measured_items": int(state["meter"].get("items") or 0),
            "measured_usd": round(float(state["meter"].get("usd") or 0), 6)}


def _cap_reached(client_id: int, state: dict, conns: list, hidden: set):
    """Stop, spend nothing more, quote the rest from the MEASURED average and
    open ONE billing request for a uallak representative (per cap episode)."""
    q = quote(client_id, state, hidden)
    if not q["remaining"]:
        return
    limit = allowance(client_id, state)
    cap = state.get("cap") or {}
    roots = [c for c in conns if not c.get("parent_connection_id")]
    folders = [f"{c.get('display_name', '')} ({c.get('role', '')})" for c in roots]
    if cap.get("active") and int(cap.get("allowance") or 0) == limit:
        cap.update({k: q[k] for k in ("remaining", "per_item_usd", "per_item_ils", "quote_usd", "quote_ils")})
        return
    cap = {"active": True, "reached_at": _iso(), "allowance": limit, "folders": folders, **q}
    text = (f"Drive asset index — the free allowance is used ({limit} items). Client {client_id}. "
            f"Folders: {'; '.join(folders)[:180]}. Remaining items: {q['remaining']}. Measured cost per item: "
            f"${q['per_item_usd']:.5f} (ILS {q['per_item_ils']:.4f}). Quote: ${q['quote_usd']:.2f} "
            f"(ILS {q['quote_ils']:.2f}). After billing, load credits in the Control Room (client → credits) — indexing resumes "
            f"where it stopped.")
    try:
        from core import agent_actions
        opened = agent_actions.open_team_request(client_id, "media", "billing", text[:500], origin="drive_assets")
        cap["request_id"] = ((opened.get("card") or {}).get("id")) or ""
        cap["request_already_open"] = bool(opened.get("already"))
    except Exception as e:
        cap["request_error"] = type(e).__name__
    _alert([f"client {client_id}: {text}"])
    state["cap"] = cap
    _log(f"client {client_id}: free cap reached ({limit}); {q['remaining']} left, quote ${q['quote_usd']}")


def load_credits(client_id: int, items: int, usd: float = None, by: str = "admin", note: str = "") -> dict:
    """The team billed the client: `items` more files may be indexed. The
    cap clears and indexing resumes from where it stopped (newest pending
    first, up to the items loaded)."""
    items = int(items)
    if not 1 <= items <= 100000:
        raise ValueError("items must be 1-100000")
    state = _load(client_id)
    credits = state["credits"]
    credits["extra_items"] = int(credits.get("extra_items") or 0) + items
    credits.setdefault("history", []).append({"at": _iso(), "items": items, "usd": usd, "by": str(by)[:40],
                                              "note": re.sub(r"\s+", " ", str(note or "")).strip()[:200]})
    credits["history"] = credits["history"][-50:]
    if state.get("cap"):
        state["cap"] = {**state["cap"], "active": False, "resolved_at": _iso()}
    _queue_job(state, "resume", limit=items)
    _save(client_id, state)
    _log(f"client {client_id}: {items} index credits loaded by {by}")
    return view_state(client_id, state)


def credits_view(client_id: int) -> dict:
    """The Control Room's credits section: the balance (free allowance +
    loaded credits − used), the open quote if indexing stopped at the cap
    (folders, items left, measured cost per item, total), the measured cost
    per item of the last run, the job (did it resume, how many are left) and
    every credit load (amount, when, who, note)."""
    from core.cost_tracker import USD_TO_ILS
    state = _load(client_id)
    conns = drive_folders.load(client_id)
    hidden = _hidden_connections(conns)
    free = int(settings(client_id)["free_items"])
    credits = state.get("credits") or {}
    extra = int(credits.get("extra_items") or 0)
    meter = state.get("meter") or {}
    used = int(meter.get("items") or 0)
    cap = state.get("cap") or {}
    job = state.get("job") or {}
    last = dict(meter.get("last_run") or {})
    if last:
        last["per_item_ils"] = round(float(last.get("per_item_usd") or 0) * USD_TO_ILS, 4)
    q = quote(client_id, state, hidden)
    return {"balance": {"free_items": free, "extra_items": extra, "allowance": free + extra, "used": used,
                        "remaining": max(0, free + extra - used)},
            "quote": ({k: cap.get(k) for k in ("reached_at", "folders", "remaining", "per_item_usd", "per_item_ils",
                                                "quote_usd", "quote_ils", "allowance", "request_id")}
                      if cap.get("active") else {}),
            "pending": q["remaining"],
            "average": {"per_item_usd": q["per_item_usd"], "per_item_ils": q["per_item_ils"],
                        "items": q["measured_items"], "usd": q["measured_usd"]},
            "last_run": last,
            "job": {"state": job.get("state", "idle"), "mode": job.get("mode", ""),
                    "described": int(job.get("described") or 0), "limit": job.get("limit") or 0},
            "history": list(reversed(credits.get("history") or []))}


# ─── Jobs: walk, then describe ───────────────────────────────────────────────

def _queue_job(state: dict, mode: str, roots: list = None, file_ids: list = None, limit: int = None):
    job = state.get("job") or {}
    active = job.get("state") in ("queued", "running")
    if active and mode == "on_demand":
        job["file_ids"] = list(dict.fromkeys(list(job.get("file_ids") or []) + list(file_ids or [])))[:200]
        return job
    if active and mode == "daily":
        return job                                      # a manual / add run is already on it
    if active:
        # Another folder connected / "re-index" while a run waits or runs: the
        # SAME run takes it in (listing again is free and idempotent)
        job["roots"] = list(dict.fromkeys(list(job.get("roots") or []) + list(roots or [])))
        if job.get("walk_only"):
            job["walk_only"] = list(dict.fromkeys(list(job["walk_only"]) + list(roots or [])))
        job.update(walk_started=False, walk_done=False, frontier=[])
        if job.get("mode") == "daily":
            job["mode"] = mode
        if limit and job.get("limit"):
            job["limit"] = max(int(job["limit"]), int(job.get("described") or 0) + int(limit))
        return job
    job = {"state": "queued", "mode": mode, "run_id": secrets.token_hex(4), "requested_at": _iso(),
           "roots": list(roots or []), "frontier": [], "walk_started": False,
           "walk_done": mode in ("resume", "on_demand"), "described": 0, "limit": limit,
           "file_ids": list(file_ids or [])[:200]}
    state["job"] = job
    return job


def request(client_id: int, mode: str = "manual", connection_ids: list = None) -> dict:
    """Queue a run: "add" (a folder was just connected), "manual" (the
    re-index button) or "daily" (the scheduled scan)."""
    if mode not in MODES:
        raise ValueError("bad mode")
    roots = [r["id"] for r in drive_folders.load(client_id) if not r.get("parent_connection_id")
             and (not connection_ids or r["id"] in connection_ids)]
    state = _load(client_id)
    batch = settings(client_id)["batch_items"]
    _queue_job(state, mode, roots=roots, limit=batch)
    _save(client_id, state)
    return view_state(client_id, state)


def _hidden_connections(conns: list) -> set:
    """Connections whose files are out: excluded subfolders (and below)."""
    return {c["id"] for c in conns if c.get("excluded_effective")}


def _walk(client_id: int, state: dict, deadline: float):
    from core import drive_service as drive, drive_storage
    job = state["job"]
    data = drive_folders._load(client_id)
    roots = {r["id"]: r for r in data["connections"] if not r.get("parent_connection_id")}
    if not job.get("walk_started"):
        job["frontier"], job["walked_roots"] = [], []
        # a change check's run walks only the roots that need it (walk_only);
        # the others' changed files are already in the index
        for rid in job.get("walk_only") or job.get("roots") or list(roots):
            root = roots.get(rid)
            if not root:
                continue
            if root.get("role") == NOT_INDEXED_ROLE:
                root.update(status="ok", error_code="", error_text="")
                continue                     # publish snapshots live there (core/asset_refs)
            try:
                with drive_storage.as_service_account():
                    meta = drive.get_meta(root["folder_id"], "id,name,trashed,driveId")
                if meta.get("trashed"):
                    raise drive_folders.FolderError("trashed", "התיקייה הזו נמצאת באשפה של Drive. שחזרו אותה.")
                root.update(status="indexing", error_code="", error_text="")
                _store_change_token(root, meta.get("driveId") or "")
            except drive_folders.FolderError as e:
                root.update(status="no_access", error_code=e.code, error_text=e.text)
                continue
            except Exception as e:
                err = drive_folders.access_error(e, root["folder_id"], root.get("display_name", ""))
                root.update(status="no_access", error_code=err.code, error_text=err.text)
                continue
            job["frontier"].append({"folder_id": root["folder_id"], "root_id": rid, "conn_id": rid, "depth": 0,
                                    "path": root.get("display_name", ""), "role": root.get("role", "")})
            job["walked_roots"].append(rid)
        job["walk_started"] = True
    while job["frontier"] and time.monotonic() < deadline:
        node = job["frontier"].pop(0)
        root = roots.get(node["root_id"])
        if not root:
            continue
        files, token, pages = [], "", 0
        try:
            with drive_storage.as_service_account():
                while pages < MAX_PAGES:
                    page = drive.list_children(node["folder_id"], page_token=token, page_size=200, fields=LIST_FIELDS)
                    files += page.get("files") or []
                    token, pages = page.get("next_page_token") or "", pages + 1
                    if not token:
                        break
        except Exception as e:
            if node["depth"] == 0:
                err = drive_folders.access_error(e, root["folder_id"], root.get("display_name", ""))
                root.update(status="no_access", error_code=err.code, error_text=err.text)
                job["walked_roots"] = [r for r in job.get("walked_roots") or [] if r != root["id"]]
            else:
                job.setdefault("partial_roots", []).append(root["id"])
            continue
        for f in files:
            mime = f.get("mimeType") or ""
            if f.get("trashed"):
                continue
            if mime == drive_folders.FOLDER_MIME:
                if drive_folders.is_system_folder(f.get("name")):
                    continue
                path = f"{node['path']} / {f.get('name', '')}"
                record = drive_folders.upsert_subfolder(data, root, f["id"], f.get("name", ""), node["folder_id"],
                                                        node["depth"] + 1, path)
                if not record or record.get("excluded") or not record.get("parent_connection_id"):
                    continue                 # excluded by the client, or connected on its own
                role = record.get("role_override") or node.get("role", "")
                if role == NOT_INDEXED_ROLE:
                    continue                 # a social_output subfolder: publish snapshots, not client assets
                if node["depth"] + 1 <= drive_folders.MAX_DEPTH:
                    job["frontier"].append({"folder_id": f["id"], "root_id": root["id"], "conn_id": record["id"],
                                            "depth": node["depth"] + 1, "path": path, "role": role})
                continue
            if mime.startswith(("image/", "video/")):
                _upsert_item(state, f, node, job["run_id"])
    drive_folders._save(client_id, data)
    if not job["frontier"]:
        walked = set(job.get("walked_roots") or []) - set(job.get("partial_roots") or [])
        for fid in [k for k, i in state["items"].items()
                    if i.get("root_id") in walked and i.get("seen_run") != job["run_id"]]:
            state["items"].pop(fid, None)       # gone from Drive (or now in an excluded subfolder)
        roles = {c["id"]: c.get("role") for c in drive_folders._resolve(data["connections"])}
        for fid in [k for k, i in state["items"].items() if roles.get(i.get("connection_id")) == NOT_INDEXED_ROLE]:
            state["items"].pop(fid, None)       # indexed before its folder became social_output
        job["walk_done"] = True


def _upsert_item(state: dict, raw: dict, node: dict, run_id: str):
    items = state["items"]
    fresh = tier0(raw, node)
    old = items.get(fresh["file_id"])
    if old and old.get("state") == "removed":
        old["seen_run"] = run_id          # removed from the index by the media library: stays out
        return
    if old:
        same = ((old.get("md5") and fresh["md5"] and old["md5"] == fresh["md5"])
                or (not fresh["md5"] and old.get("modified") == fresh["modified"]))
        keep = {k: old[k] for k in ("state", "description", "tags", "tier", "phash", "group_of", "angle_note",
                                    "cost_usd", "indexed_at", "attempts", "first_seen_run", "frame", "attributes",
                                    "prompt_version", "category", "redescribe") if k in old}
        old.clear()
        old.update(fresh)
        old.update(keep)
        old["seen_run"] = run_id
        if old.get("state") == "missing":
            # it is back (moved back, restored): usable again
            old["state"] = "indexed" if old.get("description") else "pending"
        if not same:
            # NEW content under the same file: the old description is of another picture
            for k in ("description", "tags", "tier", "phash", "group_of", "angle_note", "cost_usd", "indexed_at",
                      "frame", "attributes", "prompt_version", "category"):
                old.pop(k, None)
            old.update(state="pending", attempts=0, first_seen_run=run_id, changed_at=_iso())
        return
    if len(items) >= MAX_ITEMS:
        return
    items[fresh["file_id"]] = {**fresh, "state": "pending", "attempts": 0, "first_seen_run": run_id,
                               "seen_run": run_id, "found_at": _iso()}


def _candidates(state: dict, job: dict, hidden: set) -> list:
    pending = _pending(state, hidden)
    if job["mode"] == "daily":
        pending = [i for i in pending if i.get("first_seen_run") == job["run_id"]]   # new / changed only
    newest = sorted(pending, key=lambda i: (i.get("modified") or i.get("created") or ""), reverse=True)
    if job.get("file_ids"):
        wanted = set(job["file_ids"])
        first = [i for i in newest if i["file_id"] in wanted]
        # on demand: only those; any other run: an agent's request goes first
        return first if job["mode"] == "on_demand" else first + [i for i in newest if i["file_id"] not in wanted]
    return newest


def _mark_indexed(state: dict, item: dict, description: str, tags: list, tier: str, cost: float = 0.0, **extra):
    from core import asset_describe
    item.update(state="indexed", description=description, tags=list(tags or [])[:asset_describe.MAX_TAGS],
                tier=tier, cost_usd=round(float(cost or 0), 6), indexed_at=_iso(), **extra)
    for key in ("last_error", "redescribe"):
        item.pop(key, None)


def _describe_batch(client_id: int, state: dict, batch: list) -> int:
    """Index up to VISION_BATCH files: name → free; near-duplicate of a
    described one → its description + an angle note (free); the rest in ONE
    vision call. Returns how many were indexed."""
    done = 0
    need = []
    for item in batch:
        named = descriptive_name(item["name"])
        if named:
            _mark_indexed(state, item, named["description"], named["tags"], "name")
            _meter_add(state, 1, 0.0)
            done += 1
            continue
        img, frame = _preview_for(item)
        if not img:
            item["attempts"] = int(item.get("attempts") or 0) + 1
            item["last_error"] = "no_preview"
            if item["attempts"] >= MAX_ATTEMPTS:
                item["state"] = "skipped"
            continue
        item["phash"] = dhash(img)
        if frame:
            item["frame"] = frame
        need.append((item, img, frame))
    described = [i for i in state["items"].values() if i.get("state") == "indexed" and i.get("tier") == "vision"
                 and i.get("phash")]
    leaders, followers = [], []
    for item, img, frame in need:
        pool = [d for d in described if d.get("connection_id") == item.get("connection_id")] + \
               [l for l, _i, _f in leaders if l.get("connection_id") == item.get("connection_id")]
        match = None
        if item.get("phash"):
            near = [(hamming(item["phash"], d["phash"]), d) for d in pool if d.get("phash")]
            near = [n for n in near if n[0] <= GROUP_DISTANCE]
            match = min(near, key=lambda n: n[0])[1] if near else None
        if match is not None and match.get("media_type") == item["media_type"]:
            followers.append((item, match))
        else:
            leaders.append((item, img, frame))
    if leaders:
        category, vocab = _vocab(client_id, state)
        try:
            answers, usd = _vision(client_id, leaders, category, vocab)
        except Exception as e:
            _meter_add(state, 0, getattr(e, "usd", 0.0), calls=1)
            for item, _img, _frame in leaders:
                item["attempts"] = int(item.get("attempts") or 0) + 1
                item["last_error"] = type(e).__name__
                if item["attempts"] >= MAX_ATTEMPTS:
                    item["state"] = "failed"
            _log(f"client {client_id}: vision batch failed ({type(e).__name__})")
            answers, usd = None, 0.0
        if answers is not None:
            per = usd / len(leaders) if leaders else 0.0
            ok = 0
            for (item, _img, _frame), answer in zip(leaders, answers):
                if not answer["description"]:
                    item["attempts"] = int(item.get("attempts") or 0) + 1
                    item["last_error"] = "empty_answer"
                    continue
                from core import asset_describe
                _mark_indexed(state, item, answer["description"], answer["tags"], "vision", per,
                              group_of=item["file_id"], attributes=answer.get("attributes") or {},
                              prompt_version=asset_describe.PROMPT_VERSION, category=category)
                _grow_vocab(state, category, answer.get("new_tags"))
                for follower in state["items"].values():
                    # a RE-described leader: its group's copies follow
                    if follower.get("group_of") == item["file_id"] and follower.get("tier") == "group":
                        follower.update(description=item["description"], tags=list(item["tags"]),
                                        attributes=dict(item.get("attributes") or {}),
                                        prompt_version=item["prompt_version"], category=category)
                ok += 1
            _meter_add(state, ok, usd, vision_items=ok, calls=1)
            done += ok
    for item, match in followers:
        if match.get("state") != "indexed":
            # its leader failed: this one counts an attempt too (never loops)
            item["attempts"] = int(item.get("attempts") or 0) + 1
            item["last_error"] = "group_leader_failed"
            if item["attempts"] >= MAX_ATTEMPTS:
                item["state"] = "failed"
            continue
        members = 1 + sum(1 for i in state["items"].values() if i.get("group_of") == match["file_id"]
                          and i["file_id"] != match["file_id"])
        _mark_indexed(state, item, match["description"], match.get("tags") or [], "group", 0.0,
                      group_of=match["file_id"], attributes=dict(match.get("attributes") or {}),
                      prompt_version=match.get("prompt_version", ""), category=match.get("category", ""),
                      angle_note=f"another shot / angle of the same item as \"{match.get('name', '')[:60]}\" "
                                 f"(group of {members + 1})")
        _meter_add(state, 1, 0.0)
        done += 1
    return done


def _counts(client_id: int, state: dict):
    """item_count / indexed_count per folder record (roots count their whole tree)."""
    data = drive_folders._load(client_id)
    per = {}
    kept = [i for i in state["items"].values() if i.get("state") not in OUT_STATES]   # removed / gone
    for item in kept:
        rec = per.setdefault(item.get("connection_id"), [0, 0])
        rec[0] += 1
        rec[1] += 1 if item.get("state") == "indexed" else 0
    totals = {}
    for item in kept:
        rec = totals.setdefault(item.get("root_id"), [0, 0])
        rec[0] += 1
        rec[1] += 1 if item.get("state") == "indexed" else 0
    for r in data["connections"]:
        source = totals if not r.get("parent_connection_id") else per
        r["item_count"], r["indexed_count"] = (source.get(r["id"]) or [0, 0])
    return data


def step(client_id: int, budget_s: float = 20.0) -> dict:
    """Move this client's run forward inside `budget_s` seconds: the walk
    (listing — free), then describing, newest first, within the cap. The
    state is saved after every batch. Never raises on Drive trouble."""
    deadline = time.monotonic() + max(1.0, float(budget_s))
    token = _lease_take(client_id)
    if not token:
        return {"busy": True}
    try:
        state = _load(client_id)
        job = state.get("job") or {}
        if job.get("state") not in ("queued", "running"):
            return view_state(client_id, state)
        job["state"] = "running"
        job.setdefault("started_at", _iso())
        # what this run spends, for the "measured cost per item of the last run" line
        job.setdefault("meter_at_start", {"items": int(state["meter"].get("items") or 0),
                                          "usd": float(state["meter"].get("usd") or 0)})
        if not job.get("walk_done"):
            _walk(client_id, state, deadline - STEP_MARGIN_S)
            _save(client_id, state)
        conns = drive_folders.load(client_id)
        hidden = _hidden_connections(conns)
        while job.get("walk_done") and time.monotonic() < deadline - STEP_MARGIN_S:
            if job.get("limit") and job["described"] >= job["limit"]:
                break
            cands = _candidates(state, job, hidden)
            if not cands:
                break
            room = allowance(client_id, state) - int(state["meter"].get("items") or 0)
            if room <= 0:
                _cap_reached(client_id, state, conns, hidden)
                break
            take = min(VISION_BATCH, room, (job["limit"] - job["described"]) if job.get("limit") else VISION_BATCH)
            job["described"] += _describe_batch(client_id, state, cands[:max(1, take)])
            _save(client_id, state)
        finished = job.get("walk_done") and (
            not _candidates(state, job, hidden) or (job.get("limit") and job["described"] >= job["limit"])
            or (state.get("cap") or {}).get("active"))
        if finished:
            job.update(state="idle", finished_at=_iso(), frontier=[])
            data = _counts(client_id, state)
            stamp = _iso()
            capped = bool((state.get("cap") or {}).get("active"))
            for r in data["connections"]:
                if r.get("parent_connection_id") or r["id"] not in (job.get("roots") or [r["id"]]):
                    continue
                if r.get("status") == "no_access":
                    continue
                r["status"] = "cap_reached" if capped else "ok"
                r["last_indexed_at"] = stamp
                if job["mode"] == "daily":
                    r["last_auto_scan_at"] = stamp
            drive_folders._save(client_id, data)
            meter = state["meter"]
            if not meter.get("first_run") and meter.get("vision_items"):
                meter["first_run"] = {"at": stamp, "items": meter["items"], "usd": meter["usd"],
                                      "per_item_usd": meter["avg_usd"], "vision_items": meter["vision_items"]}
            start = job.get("meter_at_start") or {}
            run_items = int(meter.get("items") or 0) - int(start.get("items") or 0)
            if start and run_items > 0:
                run_usd = round(float(meter.get("usd") or 0) - float(start.get("usd") or 0), 6)
                meter["last_run"] = {"at": stamp, "mode": job.get("mode", ""), "items": run_items, "usd": run_usd,
                                     "per_item_usd": round(run_usd / run_items, 6)}
        else:
            _counts_data = _counts(client_id, state)
            drive_folders._save(client_id, _counts_data)
        _save(client_id, state)
        return view_state(client_id, state)
    finally:
        _lease_release(client_id, token)


def index_on_demand(client_id: int, file_ids: list, budget_s: float = 25.0) -> int:
    """An agent's search hit files not described yet: describe THEM now
    (within the cap). Returns how many are indexed afterwards."""
    ids = [str(f) for f in file_ids or [] if f][:VISION_BATCH]
    if not ids:
        return 0
    state = _load(client_id)
    _queue_job(state, "on_demand", file_ids=ids)
    _save(client_id, state)
    step(client_id, budget_s)
    state = _load(client_id)
    return sum(1 for f in ids if (state["items"].get(f) or {}).get("state") == "indexed")


def run_due(budget_s: float) -> dict:
    """The every-minute tick (with what is left of its budget) and the daily
    scan: move every client with a queued/running run."""
    deadline = time.monotonic() + float(budget_s)
    moved = []
    for client_id in drive_folders.client_ids():
        left = deadline - time.monotonic()
        if left < STEP_MARGIN_S + 5:
            break
        try:
            state = _load(client_id)
        except Exception:
            continue
        if (state.get("job") or {}).get("state") not in ("queued", "running"):
            continue
        try:
            step(client_id, min(left, 120.0))
            moved.append(client_id)
        except Exception as e:
            _log(f"client {client_id}: step failed ({type(e).__name__})")
    return {"moved": moved}


# ─── Change detection (2026-11-23): no blind walking ────────────────────────
#
# Each ROOT connection stores a Drive changes page token (`changes_token`,
# taken when its folder is walked, BEFORE the listing, so nothing is missed).
# The scheduled check asks Drive what changed since that token: nothing → the
# run ends there, no folder listing and no file enumeration. Changes → only
# those files are (re)indexed. A new or moved-in subfolder, a trashed known
# subfolder, a missing token or one Drive no longer accepts → that root is
# walked once, which stores a fresh token.

def _store_change_token(root: dict, drive_id: str):
    from core import drive_service as drive, drive_storage
    try:
        with drive_storage.as_service_account():
            token = drive.changes_start_token(drive_id)
    except Exception as e:
        token = ""
        _log(f"root {root.get('id')}: no change token ({type(e).__name__}); the next check walks it")
    root.update(changes_token=token, changes_drive_id=drive_id, changes_at=_iso() if token else "")


def _folder_nodes(data: dict) -> dict:
    """folder_id -> the walk's node for it (root, connection, path, depth, role)."""
    resolved = {c["id"]: c for c in drive_folders._resolve(data["connections"])}
    nodes = {}
    for rec in data["connections"]:
        eff = resolved.get(rec["id"]) or rec
        root_id = rec.get("parent_connection_id") or rec["id"]
        nodes[rec.get("folder_id")] = {
            "folder_id": rec.get("folder_id"), "root_id": root_id, "conn_id": rec["id"],
            "depth": int(rec.get("depth") or 0), "path": rec.get("path") or rec.get("display_name", ""),
            "role": eff.get("role", ""), "excluded": bool(eff.get("excluded_effective") or rec.get("excluded"))}
    return nodes


def _status(e: Exception):
    return getattr(getattr(e, "response", None), "status_code", None)


def _apply_changes(state: dict, root: dict, nodes: dict, changes: list, run_id: str) -> tuple:
    """(files upserted, walk needed). Only this root's folders count."""
    items, upserted, walk = state["items"], 0, False
    for change in changes:
        fid = str(change.get("fileId") or "")
        f = change.get("file") or {}
        item = items.get(fid)
        mine = bool(item) and item.get("root_id") == root["id"]
        if change.get("removed") or f.get("trashed"):
            if mine:
                items.pop(fid, None)                    # gone from Drive
            node = nodes.get(fid)
            if node and node["root_id"] == root["id"] and node["conn_id"] != root["id"]:
                walk = True                             # a known subfolder went: re-list its root
            continue
        parents = [nodes[p] for p in (f.get("parents") or []) if p in nodes and nodes[p]["root_id"] == root["id"]]
        mime = f.get("mimeType") or ""
        if mime == drive_folders.FOLDER_MIME:
            if parents and fid not in nodes and not drive_folders.is_system_folder(f.get("name")) \
                    and not any(p["excluded"] or p["role"] == NOT_INDEXED_ROLE for p in parents):
                # (a new folder inside an excluded one — e.g. the backup's month
                # folders, core/drive_backup — is never walked, so it needs no walk)
                walk = True                             # a new / moved-in subfolder: its files never show as changes
            continue
        if not mime.startswith(("image/", "video/")):
            continue
        if not parents:
            if mine:
                items.pop(fid, None)                    # moved out of the connected folders
            continue
        node = parents[0]
        if node["excluded"] or node["role"] == NOT_INDEXED_ROLE:
            if mine:
                items.pop(fid, None)
            continue
        _upsert_item(state, f, node, run_id)
        upserted += 1
    return upserted, walk


def check_changes(client_id: int) -> dict:
    """The scheduled check for ONE client: per root connection, what changed
    since its token. Queues a describe run only when files changed, and a
    walk only for roots that need one. Never raises on Drive trouble."""
    from core import drive_service as drive, drive_storage
    lease = _lease_take(client_id)
    if not lease:
        return {"busy": True}
    try:
        data = drive_folders._load(client_id)
        state = _load(client_id)
        job = state.get("job") or {}
        active = job.get("state") in ("queued", "running")
        run_id = job.get("run_id") if active else secrets.token_hex(4)
        nodes = _folder_nodes(data)
        walk_roots, touched, upserted, unchanged = [], [], 0, []
        for root in [r for r in data["connections"] if not r.get("parent_connection_id")]:
            if root.get("role") == NOT_INDEXED_ROLE:
                continue                                # snapshots only (core/asset_refs)
            token = root.get("changes_token") or ""
            if not token:
                walk_roots.append(root["id"])
                continue
            changes, pages, new_token, failed = [], 0, "", False
            while pages < CHANGE_PAGES_MAX:
                try:
                    with drive_storage.as_service_account():
                        page = drive.list_changes(token, root.get("changes_drive_id") or "", fields=CHANGE_FIELDS)
                except Exception as e:
                    if _status(e) in INVALID_TOKEN_STATUS:
                        root["changes_token"] = ""      # expired / invalid: ONE full walk stores a fresh one
                        walk_roots.append(root["id"])
                        _log(f"client {client_id}: change token of {root['id']} refused ({_status(e)}); walking once")
                    else:
                        _log(f"client {client_id}: change check of {root['id']} failed ({type(e).__name__}); "
                             f"next run tries again")
                    failed = True
                    break
                changes += page.get("changes") or []
                pages += 1
                if page.get("new_start_page_token"):
                    new_token = page["new_start_page_token"]
                    break
                token = page.get("next_page_token") or ""
                if not token:
                    break
                new_token = token                       # a very long feed: carry on from here next time
            if failed:
                continue
            count, walk = _apply_changes(state, root, nodes, changes, run_id)
            if new_token:
                root["changes_token"], root["changes_at"] = new_token, _iso()
            root["last_change_check_at"] = _iso()
            if walk:
                walk_roots.append(root["id"])
            if count:
                touched.append(root["id"])
                upserted += count
            if not changes:
                unchanged.append(root["id"])
        drive_folders._save(client_id, data)
        queued = ""
        new_pending = any(i.get("first_seen_run") == run_id and i.get("state") == "pending"
                          for i in state["items"].values())
        if active:
            if walk_roots:
                _queue_job(state, "daily", roots=walk_roots)
        elif walk_roots or new_pending:
            state["job"] = {"state": "queued", "mode": "daily", "run_id": run_id, "requested_at": _iso(),
                            "roots": list(dict.fromkeys(walk_roots + touched)), "frontier": [],
                            "walk_started": False, "walk_done": not walk_roots, "walk_only": walk_roots,
                            "described": 0, "limit": settings(client_id)["batch_items"], "file_ids": []}
            queued = "walk" if walk_roots else "describe"
        _save(client_id, state)
        return {"unchanged": unchanged, "changed_files": upserted, "walk": walk_roots, "queued": queued}
    finally:
        _lease_release(client_id, lease)


def daily_scan(budget_s: float = 200.0) -> dict:
    """The scheduled check (job drive-assets-scan): every connected client's
    roots are asked what CHANGED (check_changes) — no folder is walked unless
    it needs it; then as much of the queued work as fits now — the rest
    continues on the every-minute tick."""
    queued, checked = [], {}
    for client_id in drive_folders.client_ids():
        try:
            out = check_changes(client_id)
            checked[client_id] = out
            if out.get("queued"):
                queued.append(client_id)
        except Exception as e:
            _log(f"client {client_id}: change check failed ({type(e).__name__})")
    return {"queued": queued, "checked": checked, **run_due(budget_s)}


# ─── Indexing NOW: a chat ("I uploaded the photos"), an agent's first look ──

def index_now(client_id: int, root_ids: list, budget_s: float = 0.0, mode: str = "chat") -> dict:
    """Walk these roots now (new files found, newest described first). With
    `budget_s`, the work also starts in this call; the rest continues on the
    every-minute tick."""
    roots = [r["id"] for r in drive_folders.load(client_id) if not r.get("parent_connection_id")
             and r["id"] in set(root_ids or []) and r.get("role") != NOT_INDEXED_ROLE]
    if not roots:
        return {"queued": []}
    state = _load(client_id)
    _queue_job(state, mode, roots=roots, limit=settings(client_id)["batch_items"])
    _save(client_id, state)
    if budget_s:
        step(client_id, budget_s)
    return {"queued": roots}


def ensure_indexed(client_id: int, roles: list = None, budget_s: float = 25.0) -> list:
    """An agent is about to select from connections that were NEVER indexed
    (no items, never finished a run): index them now rather than answer
    with nothing. Returns the roots indexed."""
    try:
        conns = drive_folders.load(client_id)
        state = _load(client_id)
    except Exception:
        return []
    have = {i.get("root_id") for i in state["items"].values()}
    roles = [r for r in (roles or []) if r]
    fresh = []
    for root in (c for c in conns if not c.get("parent_connection_id")):
        if root["id"] in have or root.get("last_indexed_at") or root.get("role") == NOT_INDEXED_ROLE:
            continue
        if root.get("status") == "no_access":
            continue
        tree_roles = {root.get("role")} | {c.get("role") for c in conns if c.get("parent_connection_id") == root["id"]}
        if roles and not (tree_roles & set(roles)):
            continue
        fresh.append(root["id"])
    if not fresh:
        return []
    if (state.get("job") or {}).get("state") == "running":
        return []                                       # a run is already on it
    _log(f"client {client_id}: never-indexed {fresh} indexed on first use")
    index_now(client_id, fresh, budget_s, mode="add")
    return fresh


# ─── Use-time verification ───────────────────────────────────────────────────

def mark_missing(client_id: int, file_id: str, reason: str = "gone"):
    """The file was gone / moved when an agent went to use it: out of the
    selection until a walk or change check sees it again."""
    try:
        state = _load(client_id)
        item = state["items"].get(str(file_id))
        if not item:
            return
        item.update(state="missing", missing_at=_iso(), missing_reason=str(reason)[:40])
        _save(client_id, state)
        _log(f"client {client_id}: {file_id} marked missing ({reason})")
    except Exception as e:
        _log(f"client {client_id}: mark_missing {file_id} failed ({type(e).__name__})")


def verify_usable(client_id: int, file_id: str) -> str:
    """"ok" when the file still exists, is not trashed and still sits in the
    client's connected folders; "missing" / "moved" / "trashed" otherwise;
    "unknown" when Drive could not answer (the caller goes on)."""
    from core import drive_service as drive, drive_storage
    try:
        conns = drive_folders.load(client_id)
        known = {c.get("folder_id") for c in conns if not c.get("excluded_effective")}
        with drive_storage.as_service_account():
            meta = drive.get_meta(file_id, "id,trashed,parents")
            if meta.get("trashed"):
                return "trashed"
            parents = list(meta.get("parents") or [])
            for _depth in range(drive_folders.MAX_DEPTH + 1):
                if not parents or set(parents) & known:
                    break
                parents = list(drive.get_meta(parents[0], "id,parents").get("parents") or [])
        return "ok" if set(parents) & known else "moved"
    except Exception as e:
        status = _status(e) or (404 if getattr(e, "kind", "") == "no_access" else None)
        return "missing" if status in (403, 404) else "unknown"


# ─── Re-describing only what an older prompt described ──────────────────────

def outdated(client_id: int, state: dict = None) -> list:
    """Vision-described assets whose prompt version is not the current one."""
    from core import asset_describe
    state = state if state is not None else _load(client_id)
    return [k for k, i in state["items"].items() if i.get("state") == "indexed" and i.get("tier") == "vision"
            and i.get("prompt_version") != asset_describe.PROMPT_VERSION]


def queue_redescribe(client_id: int, limit: int = 200) -> dict:
    """Re-describe ONLY the outdated ones (their group copies follow). Each
    counts as an indexed item against the allowance, like any description."""
    state = _load(client_id)
    ids = outdated(client_id, state)[:max(1, min(int(limit or 200), 200))]
    for fid in ids:
        state["items"][fid].update(state="pending", attempts=0, redescribe=True)
    if ids:
        _queue_job(state, "on_demand", file_ids=ids)
    _save(client_id, state)
    return {"queued": len(ids)}


def forget_root(client_id: int, root_id: str) -> int:
    """A root was disconnected: its files leave the index."""
    state = _load(client_id)
    gone = [k for k, i in state["items"].items() if i.get("root_id") == root_id]
    for k in gone:
        state["items"].pop(k, None)
    _save(client_id, state)
    return len(gone)


# ─── What the settings page shows ────────────────────────────────────────────

def view_state(client_id: int, state: dict = None) -> dict:
    from core.cost_tracker import USD_TO_ILS
    state = state if state is not None else _load(client_id)
    conns = drive_folders.load(client_id)
    hidden = _hidden_connections(conns)
    job = state.get("job") or {}
    cap = state.get("cap") or {}
    free = settings(client_id)["free_items"]
    pending = len(_pending(state, hidden))
    indexed = sum(1 for i in state["items"].values() if i.get("state") == "indexed")
    if cap.get("active"):
        message, kind = STATE_TEXT["cap"].format(
            free=free, remaining=cap.get("remaining", pending),
            per=f"{float(cap.get('per_item_ils') or 0):.3f}", quote=f"{float(cap.get('quote_ils') or 0):.2f}"), "cap"
    elif job.get("state") in ("queued", "running"):
        message, kind = STATE_TEXT[job["state"]], job["state"]
    elif conns and pending:
        message, kind = STATE_TEXT["older"].format(pending=pending), "older"
    elif conns:
        message, kind = STATE_TEXT["done"], "done"
    else:
        message, kind = "", "none"
    meter = state.get("meter") or {}
    return {"state": kind, "message": message, "job_mode": job.get("mode", ""),
            "outdated_descriptions": len(outdated(client_id, state)),
            "indexed": indexed, "pending": pending, "free_items": free,
            "allowance": allowance(client_id, state), "used": int(meter.get("items") or 0),
            "cost": {"items": int(meter.get("items") or 0), "usd": round(float(meter.get("usd") or 0), 6),
                     "per_item_usd": round(float(meter.get("avg_usd") or 0), 6),
                     "per_item_ils": round(float(meter.get("avg_usd") or 0) * USD_TO_ILS, 5),
                     "first_run": meter.get("first_run") or {}},
            "cap": cap if cap.get("active") else {}}


# ─── The agents' lookup ──────────────────────────────────────────────────────

_QUERY_STOP = {"the", "and", "with", "of", "on", "in", "a", "an", "for", "from", "to", "at", "by", "is", "are",
               "photo", "photos", "image", "images", "shot", "shots", "show", "shows", "showing", "real", "client",
               "business", "style", "frame", "composition", "camera", "picture", "pictures", "video", "clip",
               "visible", "view", "seen", "its", "their", "our", "your", "this", "that", "very", "some",
               "תמונה", "תמונות", "של", "עם", "את", "על", "סרטון"}


def _query_words(query: str) -> list:
    from core import media_search
    words = [w for w in media_search.words(query) if w not in _QUERY_STOP and not w.isdigit()]
    words = [w for w in words if len(w) >= 3 or (len(w) >= 2 and "֐" <= w[0] <= "׿")]
    return list(dict.fromkeys(words))[:24]


def _match_score(item: dict, folder_name: str, qwords: list) -> float:
    from core import media_search
    if not qwords:
        return 0.0
    from core import asset_describe
    fields = ((media_search.compact(" ".join(item.get("tags") or [])), 3.0),
              (media_search.compact(f"{item.get('description') or ''} "
                                    f"{asset_describe.search_text(item.get('attributes'))}"), 2.0),
              (media_search.compact(f"{item.get('name', '')} {item.get('path', '')} {folder_name}"), 1.0))
    total = 0.0
    for word in qwords:
        variants = media_search._variants(word)
        total += max((weight for text, weight in fields if any(v in text for v in variants)), default=0.0)
    return round(total / len(qwords), 4)


def find_assets(client_id: int, roles: list = None, query: str = "", media_type: str = "", limit: int = 10,
                include_pending: bool = True, on_demand: bool = False) -> list:
    """Ranked real assets from the client's connected folders:
    [{file_id, description, tags, role, media_type,
      folder, folder_path, root_folder, name, state, score, width, height,
      duration_s, connection_id}]. `roles` filters AND orders (the caller's
    priority); `media_type` "image" / "video"; excluded subfolders never
    appear. `on_demand`: matching files not described yet are described now
    (within the cap) before the final ranking."""
    try:
        conns = drive_folders.load(client_id)
        state = _load(client_id)
    except Exception as e:
        _log(f"client {client_id}: find_assets unreadable ({type(e).__name__})")
        return []
    roles = [r for r in (roles or []) if r in drive_folders.ROLES]
    if ensure_indexed(client_id, roles):
        # a connection never indexed: indexed now, at first use
        conns, state = drive_folders.load(client_id), _load(client_id)
    by_conn = {c["id"]: c for c in conns}
    roots = {c["id"]: c for c in conns if not c.get("parent_connection_id")}
    qwords = _query_words(query)
    # Usage + the owner's rejections (core/asset_feedback): a bounded penalty
    # on the rank — recently / often used and rejected assets come later,
    # never excluded. No history = ranked exactly as before.
    from core import asset_feedback
    feedback = asset_feedback.load(client_id)

    def rank():
        out = []
        for item in state["items"].values():
            conn = by_conn.get(item.get("connection_id"))
            if not conn or conn.get("excluded_effective"):
                continue
            if item.get("state") not in ("indexed",) + (("pending",) if include_pending else ()):
                continue
            if media_type and item.get("media_type") != media_type:
                continue
            role = conn.get("role") or ""
            if roles and role not in roles:
                continue
            score = _match_score(item, conn.get("display_name", ""), qwords)
            if qwords and score <= 0:
                continue
            out.append({"file_id": item["file_id"], "name": item.get("name", ""),
                        "description": item.get("description", ""), "tags": list(item.get("tags") or []),
                        "angle_note": item.get("angle_note", ""), "role": role,
                        "attributes": dict(item.get("attributes") or {}),
                        "role_text": drive_folders.ROLE_TEXT.get(role, role),
                        "media_type": item.get("media_type", ""),
                        "folder": conn.get("display_name", ""), "folder_path": item.get("path", ""),
                        "root_folder": (roots.get(item.get("root_id")) or {}).get("display_name", ""),
                        "connection_id": conn["id"], "state": item.get("state", ""), "score": score,
                        "width": item.get("width"), "height": item.get("height"),
                        "duration_s": item.get("duration_s"), "modified": item.get("modified", ""),
                        "penalty": asset_feedback.penalty(feedback, item["file_id"]),
                        **asset_feedback.annotate(feedback, item["file_id"])})
        out.sort(key=lambda r: r["modified"], reverse=True)
        out.sort(key=lambda r: (-(r["score"] - r["penalty"]), roles.index(r["role"]) if r["role"] in roles else 99,
                                0 if r["state"] == "indexed" else 1))
        return out

    ranked = rank()
    if on_demand:
        waiting = [r["file_id"] for r in ranked[:max(limit, VISION_BATCH)] if r["state"] == "pending"]
        if waiting:
            index_on_demand(client_id, waiting)
            state = _load(client_id)
            ranked = rank()
    return ranked[:max(1, int(limit or 10))]


def has_pool(client_id: int) -> bool:
    try:
        ensure_indexed(client_id)
        state = _load(client_id)
        return any(i.get("state") in ("indexed", "pending") for i in state["items"].values())
    except Exception:
        return False
