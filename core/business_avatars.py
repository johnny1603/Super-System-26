"""The business's own characters (2026-11-25, docs/AVATARS.md §B).

The client creates an avatar from scratch on the Avatars page — a name, a
described personality, a described look: a typical-customer persona. What it
is technically: ONE generated face, stored by us, and used as THE reference
image of every pose made for that avatar afterwards (the model's documented
reference field — no vendor-side "reference element" exists or is used).

- Everything is generated on the CLIENT's own creative-tool key and credits
  (agents/media_agent._generation_key). No key → `no_tool`, nothing runs.
- Storage: app_settings `business_avatars:<client_id>` (the record) + the
  files in OUR Shared Drive under uallak-agents/business/<client_id>/.
- A pose is pending until the client approves it, like the team's poses.
- NOT wired into post production yet: a post's `characters` are still the
  uallak agents only (core/post_brief). Said in docs/AVATARS.md.

Never returns client-facing prose: refusals are CODES the page translates.
"""
import io
import re
import secrets
import threading
from datetime import datetime, timezone

SERVICE_NAME = "business_avatars"
PREFIX = "business_avatars:"
MAX_AVATARS = 6
MAX_POSES = 60                     # per avatar
MAX_REQUEST_POSES = 4              # per request, as the team's (fits one request's time)
NAME_MAX, TEXT_MAX, POSE_MAX = 40, 400, 200
ID_RE = re.compile(r"^b[a-f0-9]{10}$")
POSE_RE = re.compile(r"^p[a-f0-9]{11}$")
VERDICTS = ("approved", "rejected", "pending")
THUMB_PX = 420
FOLDER = "business"
# One visual family with the team's figures; never a photo of a real-looking person
FACE_PROMPT = ("A friendly stylized 3D character, head and shoulders portrait, facing the viewer, soft even studio "
               "light, plain flat light-grey seamless background, nothing else in the frame. The character: {look}.")
POSE_PROMPT = ("The character shown in the reference image, full body, {pose}, on a plain flat light-grey seamless "
               "studio background, nothing else in the frame. Same face, hair, body and clothing style as the "
               "reference image.")
TRANSLATE_SYSTEM = ("You turn a short description written in any language into ONE plain English phrase for an image "
                    "model. Keep every visual detail, add nothing, no names of real people or brands. At most 60 "
                    "words. Return JSON: {\"english\": \"...\"}")
_LOCK = threading.RLock()
_THUMBS = {}


class Refused(Exception):
    """A request that did not run. `code`: no_tool | name_needed | look_needed |
    pose_needed | limit | not_found | failed."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(detail or code)
        self.code = code


def _db():
    from agents.client_agent import _db as db
    return db()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _alert(lines: list):
    try:
        from core.agent_base import agent_alert
        agent_alert(SERVICE_NAME, lines)
    except Exception as e:
        print(f"[{SERVICE_NAME}] alert not sent ({type(e).__name__})")


def _short(text, n: int) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:n]


def load(client_id: int) -> list:
    rows = (_db().table("app_settings").select("value").eq("key", f"{PREFIX}{int(client_id)}").limit(1).execute().data or [])
    value = rows[0].get("value") if rows else None
    items = (value or {}).get("avatars") if isinstance(value, dict) else None
    return [a for a in (items or []) if isinstance(a, dict) and a.get("avatar_id")]


def _save(client_id: int, avatars: list):
    _db().table("app_settings").upsert({"key": f"{PREFIX}{int(client_id)}", "value": {"avatars": avatars},
                                        "updated_at": _now()}, on_conflict="key").execute()


def _key(client_id: int) -> str:
    from agents import media_agent
    return media_agent._generation_key(client_id)


def connected(client_id: int) -> bool:
    try:
        return bool(_key(client_id))
    except Exception:
        return False


def view(client_id: int) -> dict:
    """The page's part B. Never raises."""
    try:
        avatars = load(client_id)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: avatars unreadable ({type(e).__name__})")
        avatars = []
    out = []
    for a in avatars:
        poses = [{"pose_id": p["pose_id"], "status": p.get("status") or "pending", "description": p.get("description", "")}
                 for p in reversed(a.get("poses") or [])]
        out.append({"avatar_id": a["avatar_id"], "name": a.get("name", ""), "personality": a.get("personality", ""),
                    "look": a.get("look", ""), "created_at": a.get("created_at", ""), "poses": poses,
                    "counts": {v: sum(1 for p in poses if p["status"] == v) for v in VERDICTS}})
    return {"avatars": out, "max": MAX_AVATARS, "max_request": MAX_REQUEST_POSES, "connected": connected(client_id)}


# ─── Words → an English phrase for the image model ───────────────────────────

def to_english(text: str, client_id: int = 0) -> str:
    """The client's own words (any language) as a plain English phrase. Text
    that is already Latin is used as written; a failed call falls back to it."""
    text = _short(text, TEXT_MAX)
    if not text or not re.search(r"[^\x00-\x7F]", text):
        return text
    try:
        from core.claude_json import safe_claude_json_call, CLASSIFY_MODEL
        got = safe_claude_json_call(TRANSLATE_SYSTEM, text, max_tokens=200, model=CLASSIFY_MODEL,
                                    client_id=client_id or None, cost_category="claude_media")
        return _short((got or {}).get("english"), TEXT_MAX) or text
    except Exception as e:
        print(f"[{SERVICE_NAME}] description not translated ({type(e).__name__})")
        return text


# ─── Files (our Shared Drive) ────────────────────────────────────────────────

def _store(client_id: int, name: str, content: bytes, mime: str = "image/png") -> str:
    from core import drive_service as drive, drive_storage
    with drive_storage.as_service_account():
        root = drive_storage.shared_parent_id()
        if not root:
            raise RuntimeError("Missing key: DRIVE_MEDIA_FOLDER_ID")
        from core import agent_characters
        folder = drive.ensure_folder(str(int(client_id)), drive.ensure_folder(
            FOLDER, drive.ensure_folder(agent_characters.POSE_FOLDER, root)))
        return (drive.upload_bytes(folder, name, content, mime) or {}).get("id", "")


def _bytes(file_id: str) -> bytes:
    from core import drive_service as drive, drive_storage
    with drive_storage.as_service_account():
        return drive.download_file(file_id)


def _trash(file_ids: list):
    from core import drive_service as drive, drive_storage
    for file_id in [f for f in file_ids if f]:
        try:
            with drive_storage.as_service_account():
                drive.delete_file(file_id)
        except Exception as e:
            print(f"[{SERVICE_NAME}] file not removed ({type(e).__name__})")


# ─── Create: one face ────────────────────────────────────────────────────────

def create(client_id: int, name: str, personality: str, look: str, _gen=None, _store_fn=None, _english=None) -> dict:
    """A new avatar: the face is generated ONCE and stored — every later pose
    uses it as its reference. Raises Refused."""
    name, personality, look = _short(name, NAME_MAX), _short(personality, TEXT_MAX), _short(look, TEXT_MAX)
    if len(name) < 2:
        raise Refused("name_needed")
    if len(look) < 4:
        raise Refused("look_needed")
    with _LOCK:
        if len(load(client_id)) >= MAX_AVATARS:
            raise Refused("limit")
    key = "" if _gen else _key(client_id)
    if not _gen and not key:
        raise Refused("no_tool")
    from core import social_formats
    try:
        english = (_english or to_english)(look, client_id)
        if _gen is None:
            from core import media_gen_service as gen
            _gen = gen.generate_image
        made = _gen(key, FACE_PROMPT.replace("{look}", english), social_formats.POSE_ASPECT)
        avatar_id = "b" + secrets.token_hex(5)
        file_id = (_store_fn or _store)(client_id, f"{avatar_id}-face.png", made["content"], made.get("mime") or "image/png")
    except Refused:
        raise
    except Exception as e:
        _alert([f"client {client_id}: business avatar face not made ({type(e).__name__}: {str(e)[:160]})"])
        raise Refused("failed", f"{type(e).__name__}")
    record = {"avatar_id": avatar_id, "name": name, "personality": personality, "look": look, "look_en": english,
              "face_file_id": file_id, "face_mime": made.get("mime") or "image/png", "created_at": _now(), "poses": [],
              "credits": made.get("credits") or 0}
    with _LOCK:
        avatars = load(client_id)
        _save(client_id, avatars + [record])
    return record


def _find(avatars: list, avatar_id: str) -> dict:
    found = next((a for a in avatars if a["avatar_id"] == str(avatar_id)), None)
    if not found:
        raise Refused("not_found")
    return found


# ─── Poses: the stored face is the reference ─────────────────────────────────

def _make_pose(client_id: int, key: str, avatar: dict, pose: str, deps: dict) -> dict:
    from core import agent_characters, image_fit, social_formats
    english = deps["english"](pose, client_id)
    face = deps["bytes"](avatar["face_file_id"])
    ref_url = deps["publish"](key, face, avatar.get("face_mime") or "image/png")       # the reference, for this job
    made = deps["gen"](key, POSE_PROMPT.replace("{pose}", english), [ref_url], social_formats.POSE_ASPECT)
    cut = deps["remove"](key, made["url"])
    png = agent_characters.ensure_transparent(cut["content"])
    png = image_fit.fit_aspect(png, social_formats.POSE_ASPECT, made.get("aspect_ratio") or social_formats.POSE_ASPECT)["content"]
    pose_id = "p" + secrets.token_hex(6)[:11]
    file_id = deps["store"](client_id, f"{avatar['avatar_id']}-{pose_id}.png", png, "image/png")
    return {"pose_id": pose_id, "file_id": file_id, "description": _short(pose, POSE_MAX), "status": "pending",
            "created_at": _now(), "credits": made.get("credits") or 0}


def add_poses(client_id: int, avatar_id: str, poses: list, **deps) -> dict:
    """Up to MAX_REQUEST_POSES new poses of one avatar, made together, each
    from the avatar's stored face. Returns {"made": [...], "failed": n}.
    Raises Refused when nothing could start."""
    wanted = [_short(p, POSE_MAX) for p in poses or [] if _short(p, POSE_MAX)][:MAX_REQUEST_POSES]
    if not wanted:
        raise Refused("pose_needed")
    avatar = _find(load(client_id), avatar_id)
    if len(avatar.get("poses") or []) + len(wanted) > MAX_POSES:
        raise Refused("limit")
    injected = bool(deps.get("gen"))
    key = "" if injected else _key(client_id)
    if not injected and not key:
        raise Refused("no_tool")
    from core import agent_characters, media_gen_service as gen, parallel
    d = {"english": deps.get("english") or to_english, "bytes": deps.get("bytes") or _bytes,
         "publish": deps.get("publish") or gen.upload_file, "gen": deps.get("gen") or gen.generate_with_references,
         "remove": deps.get("remove") or agent_characters._local_remove(), "store": deps.get("store") or _store}
    got = parallel.gather({i: (lambda p=p: _make_pose(client_id, key, avatar, p, d)) for i, p in enumerate(wanted)})
    made = [value for _i, (ok, value) in sorted(got.items()) if ok]
    errors = [value for _i, (ok, value) in sorted(got.items()) if not ok]
    if errors:
        _alert([f"client {client_id}: {len(errors)} business avatar pose(s) not made "
                f"({type(errors[0]).__name__}: {str(errors[0])[:160]})"])
    if made:
        with _LOCK:
            avatars = load(client_id)
            target = _find(avatars, avatar_id)
            target["poses"] = (target.get("poses") or []) + made
            _save(client_id, avatars)
    return {"made": [p["pose_id"] for p in made], "failed": len(errors)}


def decide(client_id: int, pose_ids: list, verdict: str) -> dict:
    """The client's decision on poses of their own avatars."""
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}")
    wanted, changed = {str(p) for p in pose_ids or []}, []
    with _LOCK:
        avatars = load(client_id)
        for a in avatars:
            for p in a.get("poses") or []:
                if p["pose_id"] in wanted:
                    p["status"] = verdict
                    p["decided_at"] = _now()
                    changed.append(p["pose_id"])
        if changed:
            _save(client_id, avatars)
    return {"changed": changed, "verdict": verdict}


def remove(client_id: int, avatar_id: str) -> dict:
    """The avatar leaves the client's list; its files are removed from our Drive."""
    with _LOCK:
        avatars = load(client_id)
        target = _find(avatars, avatar_id)
        _save(client_id, [a for a in avatars if a is not target])
    _trash([target.get("face_file_id")] + [p.get("file_id") for p in target.get("poses") or []])
    return {"removed": target["avatar_id"]}


def approved_poses(client_id: int, avatar_id: str) -> list:
    """What a post may use of this avatar (the seam for production)."""
    return [p for p in _find(load(client_id), avatar_id).get("poses") or [] if p.get("status") == "approved"]


def thumb(client_id: int, avatar_id: str = "", pose_id: str = "", _bytes_fn=None) -> bytes:
    """A face or a pose at grid size — only this client's own. LookupError when it is not theirs."""
    avatars = load(client_id)
    if pose_id:
        file_id = next((p.get("file_id") for a in avatars for p in a.get("poses") or [] if p["pose_id"] == str(pose_id)), "")
    else:
        file_id = next((a.get("face_file_id") for a in avatars if a["avatar_id"] == str(avatar_id)), "")
    if not file_id:
        raise LookupError("no such image")
    if file_id in _THUMBS:
        return _THUMBS[file_id]
    from PIL import Image
    with Image.open(io.BytesIO((_bytes_fn or _bytes)(file_id))) as img:
        img = img.convert("RGBA")
        img.thumbnail((THUMB_PX, THUMB_PX))
        buf = io.BytesIO()
        img.save(buf, "PNG", optimize=True)
    if len(_THUMBS) >= 400:
        _THUMBS.pop(next(iter(_THUMBS)))
    _THUMBS[file_id] = buf.getvalue()
    return _THUMBS[file_id]
