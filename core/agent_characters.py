"""The agents as their own characters in posts (2026-11-24,
docs/AGENT-CHARACTERS.md).

Each uallak agent (maya, lior, yoav, johnny, elinor, nissim, uri) has ONE
REFERENCE IMAGE, set by the manager by hand in /admin/characters/ (a pasted
https URL or an uploaded image) with its Higgsfield media / job id and
optional notes. A character generation passes that image as an INPUT IMAGE in
the model's own documented field (media_gen_service.generate_with_references,
`image_urls`). Soul models are NOT trained for this.

Why not Higgsfield "elements": the first version (052b1d6) listed them on a
path inferred from MCP tool descriptions; the public REST API has no such
endpoint (production answered 405). Nothing here lists, matches or re-reads
anything — the manager sets each agent explicitly, once.

Two modes per asset (core/post_brief `characters` + `character_mode`):
- IN_SCENE  — the character is generated inside the scene: the reference
  image(s) + STYLE_CLAUSE, always (in_scene_prompt). Several characters =
  several reference images in one call.
- COMPOSITE — a cut-out is drawn onto a brand-template slide by
  core/slide_renderer, UNDER the text: a pose is generated from the reference
  on a plain background, the background is removed (a transparent PNG,
  verified in code) and the pose is kept in the library. Reusing a stored
  pose costs nothing.

No reference stored for an agent = MissingReference, whose message is
"לא מוגדרת תמונת ייחוס עבור <agent>" — carried into the production line in
the manager's chat (core/post_pipeline._tell), never only a log line.

THE ONLY GUARDRAIL: an agent never creates or rebuilds a character — not its
own, not another agent's. Only the admin sets a reference (set_reference
refuses any other `by`); create_element / rebuild_element refuse and alert;
an agent that judges its character weak REPORTS it (report_element_weak).
Rebuilding identity from generated output compounds drift until the faces
change.

Storage (app_settings, global — not per client):
- `agent_references`: {"agents": {agent: {"images": [{ref_id, media_id, url,
  url_at, file_id, mime, width, height, added_at, by, source}], "notes",
  "updated_at"}}, "history": [...], "reports": [...]} — a reference SET per
  agent (up to MAX_REF_IMAGES); the whole set is sent, within the model's
  max (shared between the characters of one image, reference_plan); an older
  single-image record reads as a set of one. `file_id` = a durable copy in our Shared Drive
  (uallak-agents/<agent>/): Higgsfield tags uploads retention=temporary and
  keeps outputs ~7 days, so a URL older than REF_FRESH_HOURS is re-uploaded
  from that copy (the documented upload) before it is sent.
- `agent_pose_candidates`: {"agents": {agent: [test result]}, "jobs": {agent:
  {state, made, credits, errors}}} — the workspace's test poses waiting for
  the manager's decision (save / discard / promote), never used by posts.
- `agent_poses`: {"poses": [record], "last": {agent: pose_id}} — the pose
  library. Accumulates, never gates: a generated pose is stored at once
  (approved False, usable by the post that made it); when the manager approves
  a post, every character image it used is marked approved and tagged (agent,
  outfit, setting, framing, transparent). find_poses offers APPROVED poses
  before anything is generated, ranked by match minus the usage penalty
  (core/asset_feedback.penalty) and never the pose that agent used last.

Generation runs on uallak's OWN Higgsfield key (HIGGSFIELD_AGENTS_KEY). Without
the key the feature refuses plainly.
"""
import io
import os
import re
import secrets
from datetime import datetime, timezone, timedelta

SERVICE_NAME = "agent_characters"
AGENTS = ("maya", "lior", "yoav", "johnny", "elinor", "nissim", "uri")
AGENT_NAMES = {"maya": "מאיה", "lior": "ליאור", "yoav": "יואב", "johnny": "ג׳וני", "elinor": "אלינור",
               "nissim": "ניסים", "uri": "אורי"}
MODES = ("composite", "in_scene")
REFS_KEY = "agent_references"
POSES_KEY = "agent_poses"
MAX_CHARACTERS = 3                 # in one in-scene image (both documented models take ≥ 3)
MAX_POSES = 3000
REF_FRESH_HOURS = 24               # an older reference URL is re-uploaded from our copy
MAX_REF_BYTES = 10 * 1024 * 1024
STYLE_CLAUSE = "same stylized 3D character look and same face as the reference figure"
PLACEHOLDER_RE = re.compile(r"<<<\s*[^<>]*\s*>>>")   # stripped from model-written scenes
URL_RE = re.compile(r"^https://[^\s<>\"'`]{4,2000}$")
MEDIA_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-:.]{0,119}$")
POSE_ID_RE = re.compile(r"^[a-f0-9]{12}$")
KEY_ENV = "HIGGSFIELD_AGENTS_KEY"
POSE_FOLDER = "uallak-agents"
POSE_BACKGROUND = "on a plain flat light-grey seamless studio background, nothing else in the frame"
IMAGE_MIMES = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}
ADMIN = "admin"

MISSING_TEXT = {
    "he": "לא מוגדרת תמונת ייחוס עבור {name}",
    "en": "No reference image is set for {name}",
    "fr": "Aucune image de référence n'est définie pour {name}",
    "ar": "لا توجد صورة مرجعية محددة لـ {name}",
    "ru": "Не задано эталонное изображение для {name}",
}


VIDEO_TEXT = {
    "he": "סרטון עם דמות של סוכן עוד לא נתמך — אפשר תמונה או שקופית",
    "en": "A video with an agent character is not supported yet — an image or a slide works",
    "fr": "Une vidéo avec un personnage d'agent n'est pas encore prise en charge — une image ou une diapositive fonctionne",
    "ar": "فيديو مع شخصية وكيل غير مدعوم بعد — صورة أو شريحة تعمل",
    "ru": "Видео с персонажем агента пока не поддерживается — подойдёт изображение или слайд",
}


def video_unsupported_text(lang: str = "he") -> str:
    return VIDEO_TEXT.get(lang) or VIDEO_TEXT["he"]


def missing_text(agent: str, lang: str = "he") -> str:
    """The manager-facing line. Hebrew: "לא מוגדרת תמונת ייחוס עבור <agent>"
    (the agent's Hebrew name and its code)."""
    name = f"{AGENT_NAMES.get(agent, agent)} ({agent})" if lang == "he" else agent
    return (MISSING_TEXT.get(lang) or MISSING_TEXT["he"]).replace("{name}", name)


# Every way a character can fail, said plainly to the manager (2026-11-25):
# a failed character is never only an alert in a log.
FAILURE_TEXT = {
    "lead": {"he": "הדמות של {name} לא נוצרה", "en": "{name}'s character was not made",
             "fr": "Le personnage de {name} n'a pas été créé", "ar": "لم تُنشأ شخصية {name}",
             "ru": "Персонаж {name} не создан"},
    "not_configured": {"he": "חשבון יצירת הדמויות של הצוות לא מחובר", "en": "the team's character account is not connected",
                       "fr": "le compte de personnages de l'équipe n'est pas connecté",
                       "ar": "حساب شخصيات الفريق غير متصل", "ru": "аккаунт персонажей команды не подключён"},
    "background": {"he": "הסרת הרקע מהדמות נכשלה", "en": "removing the character's background failed",
                   "fr": "la suppression de l'arrière-plan a échoué", "ar": "فشلت إزالة خلفية الشخصية",
                   "ru": "не удалось убрать фон персонажа"},
    "model": {"he": "מודל יצירת הדמויות לא מוגדר נכון", "en": "the character model is not set correctly",
              "fr": "le modèle de personnages est mal configuré", "ar": "نموذج الشخصيات غير مضبوط",
              "ru": "модель персонажей настроена неверно"},
    "generation": {"he": "ספק היצירה החזיר שגיאה", "en": "the generation service returned an error",
                   "fr": "le service de génération a renvoyé une erreur", "ar": "أعادت خدمة التوليد خطأ",
                   "ru": "сервис генерации вернул ошибку"},
}


def failure_text(agent: str, error: Exception, lang: str = "he") -> str:
    """One plain line for the manager: why this agent's character is missing."""
    if isinstance(error, MissingReference):
        return missing_text(agent, lang)
    name = type(error).__name__
    key = ("not_configured" if getattr(error, "reason", "") == "not_configured"
           else "background" if name in ("BackgroundNotRemoved", "BackgroundModelMissing")
           else "model" if name == "UnverifiedModelPath" else "generation")
    pick = lambda t: t.get(lang) or t["he"]                     # noqa: E731
    who = AGENT_NAMES.get(agent, agent) if lang == "he" else agent
    return f"{pick(FAILURE_TEXT['lead']).replace('{name}', who)}: {pick(FAILURE_TEXT[key])}"


class CharacterUnavailable(RuntimeError):
    """No usable character for that agent (no reference, no key)."""

    def __init__(self, agent: str, reason: str, message: str = ""):
        super().__init__(message or f"agent {agent}: {reason}")
        self.agent, self.reason, self.message = agent, reason, message or f"agent {agent}: {reason}"


class MissingReference(CharacterUnavailable):
    """No reference image stored for that agent — the manager sets one."""

    def __init__(self, agent: str):
        super().__init__(agent, "no_reference", missing_text(agent))


class ElementCreationRefused(PermissionError):
    """THE guardrail: no agent creates or rebuilds a character."""


class BackgroundNotRemoved(RuntimeError):
    """The background remover returned an image with no transparency."""


def _db():
    from agents.client_agent import _db as db
    return db()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _alert(lines: list):
    try:
        from core.agent_base import agent_alert
        agent_alert(SERVICE_NAME, lines)
    except Exception:
        print(f"[{SERVICE_NAME}] " + " | ".join(lines))


def _read(key: str) -> dict:
    rows = (_db().table("app_settings").select("value").eq("key", key).limit(1).execute().data) or []
    value = rows[0].get("value") if rows else None
    return value if isinstance(value, dict) else {}


def _write(key: str, value: dict):
    _db().table("app_settings").upsert({"key": key, "value": value, "updated_at": _now().isoformat()},
                                       on_conflict="key").execute()


def clean_agent(agent) -> str:
    a = str(agent or "").strip().lower()
    if a not in AGENTS:
        raise ValueError(f"unknown agent {agent!r} (one of {', '.join(AGENTS)})")
    return a


# ─── The key (uallak's own account) ──────────────────────────────────────────

def agents_key() -> str:
    from core import media_gen_service as gen
    raw = os.environ.get(KEY_ENV, "")
    return gen.normalize_key(raw) if raw else ""


def configured() -> bool:
    from core import media_gen_service as gen
    key = agents_key()
    return bool(key) and gen.has_both_parts(key)


# ─── The reference SET per agent: {images: [...], notes} ─────────────────────

MAX_REF_IMAGES = 14                # the most any model takes; each model's own max applies when sending
REF_ID_RE = re.compile(r"^r[a-f0-9]{10}$")
IMAGE_FIELDS = ("media_id", "url", "url_at", "file_id", "mime", "width", "height")


def _ref_id(seed: str = "") -> str:
    if seed:
        import hashlib
        return "r" + hashlib.sha1(seed.encode()).hexdigest()[:10]
    return "r" + secrets.token_hex(5)


def _normal(entry) -> dict:
    """One agent's record as {"images": [...], "notes", "updated_at"}. A
    single-image record from before the set (url at the top level) becomes a
    set of one."""
    if not isinstance(entry, dict):
        return {"images": [], "notes": "", "updated_at": ""}
    if "images" not in entry:
        images = []
        if URL_RE.match(str(entry.get("url") or "")):
            images.append({**{k: entry.get(k) for k in IMAGE_FIELDS}, "ref_id": _ref_id(str(entry["url"])),
                           "added_at": entry.get("updated_at", ""), "by": entry.get("by", ADMIN),
                           "source": entry.get("source", "url")})
        entry = {"images": images, "notes": entry.get("notes", ""), "updated_at": entry.get("updated_at", "")}
    entry["images"] = [i for i in entry.get("images") or [] if isinstance(i, dict) and URL_RE.match(str(i.get("url") or ""))]
    entry.setdefault("notes", "")
    entry.setdefault("updated_at", "")
    return entry


def references() -> dict:
    """The stored records (never raises; unreadable = empty)."""
    try:
        data = _read(REFS_KEY)
    except Exception as e:
        print(f"[{SERVICE_NAME}] references unreadable ({type(e).__name__})")
        data = {}
    data["agents"] = {a: _normal(e) for a, e in (data.get("agents") or {}).items() if a in AGENTS}
    data.setdefault("history", [])
    data.setdefault("reports", [])
    return data


def image_info(content: bytes) -> dict:
    """{"mime", "width", "height"} of a real image, or ValueError."""
    if not content:
        raise ValueError("the image is empty")
    if len(content) > MAX_REF_BYTES:
        raise ValueError(f"the image is over {MAX_REF_BYTES // (1024 * 1024)} MB")
    from PIL import Image
    try:
        img = Image.open(io.BytesIO(content))
        img.verify()
        img = Image.open(io.BytesIO(content))
    except Exception:
        raise ValueError("this is not an image (PNG, JPEG, WEBP or GIF)")
    mime = IMAGE_MIMES.get(str(img.format or "").upper())
    if not mime:
        raise ValueError(f"unsupported image format {img.format} (PNG, JPEG, WEBP or GIF)")
    return {"mime": mime, "width": img.width, "height": img.height}


def _fetch(url: str) -> bytes:
    import httpx
    response = httpx.get(url, follow_redirects=True, timeout=60)
    if response.status_code != 200:
        raise ValueError(f"the URL answered {response.status_code}")
    return response.content


def _publish(content: bytes, mime: str) -> str:
    """Bytes → a Higgsfield public_url (the documented upload, uallak's key)."""
    from core import media_gen_service as gen
    if not configured():
        raise CharacterUnavailable("*", "not_configured", f"{KEY_ENV} is not set")
    return gen.upload_file(agents_key(), content, mime)


def _keep(agent: str, content: bytes, mime: str, kind: str = "reference") -> str:
    """A durable copy in our Shared Drive (uallak-agents/<agent>/) → file id.
    `kind`: the file name's part ("reference", "candidate-<id>")."""
    from core import drive_service as drive, drive_storage
    ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}.get(mime, "png")
    with drive_storage.as_service_account():
        folder = _pose_folder(agent)
        stamp = _now().strftime("%Y%m%d-%H%M%S")
        return (drive.upload_bytes(folder, f"{agent}-{kind}-{stamp}.{ext}", content, mime) or {}).get("id", "")


def _kept_bytes(file_id: str) -> bytes:
    from core import drive_service as drive, drive_storage
    with drive_storage.as_service_account():
        return drive.download_file(file_id)


def _admin_only(agent, by: str):
    """THE guardrail on every write here: the manager's screen only."""
    if by != ADMIN:
        create_element(agent, by=by)


def _history(data: dict, agent: str, action: str, by: str, **extra):
    data["history"] = (data["history"] + [{"at": _now().isoformat(), "agent": agent, "action": action,
                                           "by": by, **extra}])[-80:]


def add_reference(agent: str, url: str = "", media_id: str = "", by: str = ADMIN, content: bytes = None,
                  source: str = "", _fetch_fn=None, _publish_fn=None, _keep_fn=None) -> dict:
    """ONE more image into the agent's reference set: a pasted https URL, or
    uploaded bytes (`content`, through the documented upload). The image is
    read and checked; a durable copy is kept in our Drive. The manager only."""
    _admin_only(agent, by)
    agent = clean_agent(agent)
    media_id = str(media_id or "").strip()
    if media_id and not MEDIA_ID_RE.match(media_id):
        raise ValueError("the media id has letters, digits, - _ : . only")
    data = references()
    entry = data["agents"].setdefault(agent, _normal({}))
    if len(entry["images"]) >= MAX_REF_IMAGES:
        raise ValueError(f"at most {MAX_REF_IMAGES} reference images per agent — remove one first")
    uploaded = content is not None
    if uploaded:
        info = image_info(content)
        url = (_publish_fn or _publish)(content, info["mime"])
    else:
        url = str(url or "").strip()
        if not URL_RE.match(url):
            raise ValueError("paste an https:// URL of the image")
        if any(i["url"] == url for i in entry["images"]):
            raise ValueError("this image is already in the set")
        try:
            content = (_fetch_fn or _fetch)(url)
        except ValueError as e:
            raise ValueError(f"the image at this URL could not be read: {e}")
        except Exception as e:
            raise ValueError(f"the image at this URL could not be read ({type(e).__name__})")
        info = image_info(content)
    warning = ""
    try:
        file_id = (_keep_fn or _keep)(agent, content, info["mime"])
    except Exception as e:
        file_id, warning = "", f"no durable copy kept ({type(e).__name__}) — the URL is used as is"
        _alert([f"reference for {agent}: {warning}"])
    image = {"ref_id": _ref_id(), "media_id": media_id, "url": url, "url_at": _now().isoformat(),
             "file_id": file_id, **info, "added_at": _now().isoformat(), "by": by,
             "source": source if source in ("promoted",) else ("upload" if uploaded else "url")}
    entry["images"].append(image)
    entry["updated_at"] = _now().isoformat()
    _history(data, agent, "add", by, ref_id=image["ref_id"], source=image["source"])
    _write(REFS_KEY, data)
    out = view(data)
    out["warning"] = warning
    out["added"] = image["ref_id"]
    return out


def set_reference(agent: str, url: str = "", media_id: str = "", notes: str = "", by: str = ADMIN,
                  content: bytes = None, **kw) -> dict:
    """Kept for callers of the single-image version: adds the image (and the
    notes, when given) to the set."""
    out = add_reference(agent, url=url, media_id=media_id, by=by, content=content, **kw)
    if notes:
        out = {**set_notes(agent, notes, by=by), "warning": out.get("warning", ""), "added": out.get("added")}
    return out


def remove_reference(agent: str, ref_id: str, by: str = ADMIN) -> dict:
    """One image out of the set (the Drive copy stays, for the record)."""
    _admin_only(agent, by)
    agent = clean_agent(agent)
    data = references()
    entry = data["agents"].get(agent) or _normal({})
    before = len(entry["images"])
    entry["images"] = [i for i in entry["images"] if i.get("ref_id") != ref_id]
    if len(entry["images"]) == before:
        raise ValueError("no such reference image")
    data["agents"][agent] = entry
    _history(data, agent, "remove", by, ref_id=ref_id)
    _write(REFS_KEY, data)
    return view(data)


def set_notes(agent: str, notes: str, by: str = ADMIN) -> dict:
    _admin_only(agent, by)
    agent = clean_agent(agent)
    data = references()
    entry = data["agents"].setdefault(agent, _normal({}))
    entry["notes"] = re.sub(r"\s+", " ", str(notes or "")).strip()[:300]
    entry["updated_at"] = _now().isoformat()
    _history(data, agent, "notes", by)
    _write(REFS_KEY, data)
    return view(data)


def clear_reference(agent: str, by: str = ADMIN) -> dict:
    """The whole set out (the agent's character generations then fail with
    the explicit message until an image is added)."""
    _admin_only(agent, by)
    agent = clean_agent(agent)
    data = references()
    entry = data["agents"].get(agent) or _normal({})
    entry["images"] = []
    data["agents"][agent] = entry
    _history(data, agent, "clear", by)
    _write(REFS_KEY, data)
    return view(data)


def reference_for(agent: str, data: dict = None) -> dict:
    """The agent's record with at least one image, or MissingReference."""
    agent = clean_agent(agent)
    data = data if data is not None else references()
    entry = _normal((data.get("agents") or {}).get(agent))
    if not entry["images"]:
        raise MissingReference(agent)
    return entry


def require_references(agents: list, data: dict = None) -> dict:
    """Every agent has a reference, or MissingReference for the first that
    has none. Returns the data read."""
    data = data if data is not None else references()
    for a in agents or []:
        reference_for(a, data)
    return data


def _model_max() -> int:
    from core import media_gen_service as gen
    return gen.max_references() or MAX_REF_IMAGES


def reference_plan(agents: list, data: dict = None, max_total: int = None) -> list:
    """The images sent per agent, in order: [[image, …], …]. The model's max
    is shared between the characters (at least one each); within a set the
    first images go first."""
    data = data if data is not None else references()
    agents = list(agents or [])
    if not agents:
        return []
    total = max_total or _model_max()
    per = max(1, total // len(agents))
    return [reference_for(a, data)["images"][:per] for a in agents]


def _stale(image: dict) -> bool:
    try:
        then = datetime.fromisoformat(str(image.get("url_at") or "").replace("Z", "+00:00"))
    except ValueError:
        return True
    return _now() - then >= timedelta(hours=REF_FRESH_HOURS)


def reference_urls(agents: list, data: dict = None, _refresh=None, max_total: int = None) -> list:
    """The URLs to send, flat, in the plan's order. A URL older than
    REF_FRESH_HOURS is re-uploaded from our durable copy first (Higgsfield's
    uploads are temporary); if that fails the stored URL is sent as is."""
    data = data if data is not None else references()
    urls, changed = [], False
    for group in reference_plan(agents, data, max_total):
        for image in group:
            if image.get("file_id") and _stale(image):
                try:
                    fresh = (_refresh or (lambda i: _publish(_kept_bytes(i["file_id"]), i.get("mime") or "image/png")))(image)
                    image.update(url=fresh, url_at=_now().isoformat())
                    changed = True
                except Exception as e:
                    print(f"[{SERVICE_NAME}] reference not refreshed ({type(e).__name__}) — the stored URL is sent")
            urls.append(image["url"])
    if changed:
        try:
            _write(REFS_KEY, data)
        except Exception:
            pass
    return urls


def view(data: dict = None) -> dict:
    """The workspace: per agent its reference set, the test candidates, the
    running test, and its pose library."""
    from core import media_gen_service as gen
    data = data if data is not None else references()
    cands = _candidates()
    lib = _library()
    sent = gen.max_references()
    rows = []
    for agent in AGENTS:
        e = _normal((data.get("agents") or {}).get(agent))
        images = [{**{k: i.get(k, "") for k in ("ref_id", "url", "media_id", "source", "added_at", "width", "height")},
                   "durable": bool(i.get("file_id"))} for i in e["images"]]
        poses = [p for p in lib["poses"] if p.get("agent") == agent]
        rows.append({"agent": agent, "name": AGENT_NAMES[agent], "set": bool(images), "images": images,
                     "notes": e["notes"], "updated_at": e["updated_at"], "sent": min(len(images), sent or len(images)),
                     "candidates": (cands["agents"].get(agent) or [])[-MAX_CANDIDATES:],
                     "job": cands["jobs"].get(agent) or {},
                     "poses": [_pose_row(p) for p in sorted(poses, key=lambda p: p.get("created_at", ""), reverse=True)[:60]],
                     "pose_counts": {"approved": sum(1 for p in poses if p.get("approved")),
                                     "pending": sum(1 for p in poses if not p.get("approved")),
                                     "transparent": sum(1 for p in poses if p.get("transparent")),
                                     "total": len(poses)}})
    return {"agents": rows, "configured": configured(), "model": gen.CHARACTER_IMAGE_PATH,
            "model_verified": gen.CHARACTER_IMAGE_PATH.strip("/") in gen.CHARACTER_MODELS,
            "max_references": sent, "max_images": MAX_REF_IMAGES, "default_poses": list(DEFAULT_TEST_POSES),
            "reports": (data.get("reports") or [])[-20:], "poses": pose_counts()}


def _pose_row(p: dict) -> dict:
    return {"pose_id": p.get("pose_id"), "approved": bool(p.get("approved")), "transparent": bool(p.get("transparent")),
            "client_id": p.get("client_id", 0), "description": p.get("description", ""), "outfit": p.get("outfit", ""),
            "framing": p.get("framing", ""), "setting": p.get("setting", ""), "source": p.get("source", ""),
            "uses": (p.get("uses") or {}).get("count", 0), "created_at": p.get("created_at", "")}


# ─── THE guardrail ───────────────────────────────────────────────────────────

def create_element(*args, by: str = "agent", **kwargs):
    """Refused, always. A character's reference is set by the manager, by
    hand — never by an agent, never from generated output. Alerts so the
    attempt is seen."""
    _alert([f"refused: {by} tried to create/rebuild an agent character — agents never do this; "
            f"if a character is weak, report_element_weak"])
    raise ElementCreationRefused("agents never create or rebuild a character; report it instead")


rebuild_element = create_element
update_element = create_element


def report_element_weak(agent: str, reason: str, by: str = "media") -> dict:
    """An agent judges its (or another agent's) character weak: it REPORTS,
    the manager decides. Nothing else happens."""
    agent = clean_agent(agent)
    reason = re.sub(r"\s+", " ", str(reason or "")).strip()[:300] or "no reason given"
    entry = {"at": _now().isoformat(), "agent": agent, "reason": reason, "by": str(by)[:40]}
    try:
        data = references()
        data["reports"] = (data.get("reports") or [])[-49:] + [entry]
        _write(REFS_KEY, data)
    except Exception:
        pass
    _alert([f"agent character {agent} reported weak by {by}: {reason} — the manager replaces the "
            f"reference image if needed"])
    return entry


# ─── Prompts ─────────────────────────────────────────────────────────────────

def _strip_placeholders(text: str) -> str:
    return re.sub(r"\s{2,}", " ", PLACEHOLDER_RE.sub("", str(text or ""))).strip()


def _who_counts(counts: list) -> list:
    """How each character is named, by the reference images sent for it (in
    order): one character → "the character shown in the reference image(s)";
    several → "the character from reference image 2" / "… images 3-5"."""
    if len(counts) == 1:
        return ["the character shown in the reference images" if counts[0] > 1
                else "the character shown in the reference image"]
    out, start = [], 1
    for k in counts:
        k = max(1, k)
        out.append(f"the character from reference image {start}" if k == 1
                   else f"the character from reference images {start}-{start + k - 1}")
        start += k
    return out


def _who(n: int) -> list:
    return _who_counts([1] * n)


def in_scene_prompt(scene: str, agents: list, data: dict = None, crop_margin: bool = False) -> str:
    """The scene prompt naming each character by its reference images (sent
    in the same order, reference_plan) and the style clause, always.
    `crop_margin` = the result will be cropped to the requested ratio: ask for
    room at the top and bottom."""
    agents = [clean_agent(a) for a in agents or []]
    if not agents:
        raise ValueError("in_scene needs at least one character")
    if len(set(agents)) != len(agents):
        raise ValueError("a character appears twice")
    if len(agents) > MAX_CHARACTERS:
        raise ValueError(f"at most {MAX_CHARACTERS} characters in one image")
    data = require_references(agents, data)
    who = _who_counts([len(g) for g in reference_plan(agents, data)])
    scene = _strip_placeholders(scene).rstrip(". ")
    prompt = f"{scene}. Featuring {' and '.join(who)}, {STYLE_CLAUSE}."
    notes = [f"{who[i]}: {_strip_placeholders(reference_for(a, data).get('notes'))}"
             for i, a in enumerate(agents) if reference_for(a, data).get("notes")]
    if notes:
        prompt += " " + "; ".join(notes) + "."
    if crop_margin:
        prompt += " " + CROP_MARGIN
    return with_style(prompt)


def with_style(prompt: str) -> str:
    """The style clause is in every character prompt — appended if missing."""
    prompt = str(prompt or "").strip()
    if STYLE_CLAUSE not in prompt:
        prompt = f"{prompt.rstrip('. ')}. {STYLE_CLAUSE}."
    return prompt


def pose_prompt(agent: str, pose: str, outfit: str = "", framing: str = "full body", data: dict = None) -> str:
    """A composite pose: the character alone on a plain background (the
    background is removed afterwards)."""
    data = data if data is not None else references()
    ref = reference_for(agent, data)
    bits = [_who_counts([len(reference_plan([agent], data)[0])])[0],
            _strip_placeholders(pose) or "standing, relaxed, friendly"]
    if outfit:
        bits.append(f"wearing {_strip_placeholders(outfit)}")
    elif ref.get("notes"):
        bits.append(_strip_placeholders(ref["notes"]))
    bits.append(f"{framing or 'full body'} framing, the whole figure inside the frame, centred")
    bits.append(POSE_BACKGROUND)
    bits.append("no text, no props, no other people")
    return with_style(", ".join(bits))


def _local_remove():
    from core import background_removal
    return background_removal.remove_background


# ─── Background removal → a verified transparent PNG ─────────────────────────

def ensure_transparent(png: bytes) -> bytes:
    """The image as an RGBA PNG, or BackgroundNotRemoved when it has no
    fully transparent pixels (a remover that returned the original)."""
    from PIL import Image
    img = Image.open(io.BytesIO(png))
    if img.mode not in ("RGBA", "LA") and not (img.mode == "P" and "transparency" in img.info):
        raise BackgroundNotRemoved("the image has no alpha channel")
    img = img.convert("RGBA")
    lo, hi = img.getchannel("A").getextrema()
    if lo > 0:
        raise BackgroundNotRemoved("no transparent pixels — the background was not removed")
    if hi == 0:
        raise BackgroundNotRemoved("the image is fully transparent")
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


# ─── The requested aspect, exactly (crop from the centre) ────────────────────

CROP_MARGIN = ("Keep every character's whole head and feet well inside the frame, with clear space at the top "
               "and bottom.")


from core.image_fit import CROP_TOLERANCE, fit_aspect, needs_crop  # noqa: E402,F401  (shared with post images)
from core.image_fit import ratio as _ratio  # noqa: E402,F401


# ─── The pose library ────────────────────────────────────────────────────────

TAG_FIELDS = ("outfit", "setting", "framing")

# The library is ONE app_settings row, read-modify-written: poses made in
# parallel (poses_for) must not overwrite each other's records
import functools as _functools          # noqa: E402
import threading as _threading          # noqa: E402
_LIBRARY_LOCK = _threading.RLock()


def _library_locked(fn):
    @_functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with _LIBRARY_LOCK:
            return fn(*args, **kwargs)
    return wrapper


def _library() -> dict:
    try:
        data = _read(POSES_KEY)
    except Exception as e:
        print(f"[{SERVICE_NAME}] pose library unreadable ({type(e).__name__})")
        data = {}
    data.setdefault("poses", [])
    data.setdefault("last", {})
    return data


def _short(text, n: int = 160) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:n]


@_library_locked
def add_pose(agent: str, file_id: str, transparent: bool, client_id: int = 0, outfit: str = "",
             setting: str = "", framing: str = "", description: str = "", tags: list = None,
             source: str = "generated", prompt: str = "") -> dict:
    """A character image into the library (approved False until a post that
    used it is approved). client_id 0 = a global cut-out; an in-scene image
    belongs to the client whose scene it is."""
    agent = clean_agent(agent)
    record = {"pose_id": secrets.token_hex(6), "agent": agent, "file_id": str(file_id or "")[:200],
              "transparent": bool(transparent), "client_id": int(client_id or 0),
              "outfit": _short(outfit, 80), "setting": _short(setting, 120), "framing": _short(framing, 40),
              "description": _short(description, 300),
              "tags": [_short(t, 40) for t in (tags or []) if _short(t, 40)][:12],
              "source": source if source in ("generated", "in_scene", "manual") else "generated",
              "approved": False, "created_at": _now().isoformat(), "uses": {"count": 0, "last_used_at": ""},
              "prompt": _short(_strip_placeholders(prompt), 400)}
    data = _library()
    data["poses"] = (data["poses"] + [record])[-MAX_POSES:]
    _write(POSES_KEY, data)
    return record


def get_pose(pose_id: str, data: dict = None) -> dict:
    data = data if data is not None else _library()
    return next((p for p in data["poses"] if p.get("pose_id") == str(pose_id)), {})


@_library_locked
def record_pose_use(pose_id: str) -> dict:
    """A pose went into a post: counted, and remembered as that agent's last
    one (the next post gets another). Never raises."""
    try:
        data = _library()
        pose = get_pose(pose_id, data)
        if not pose:
            return {}
        u = pose.get("uses") or {}
        pose["uses"] = {"count": int(u.get("count") or 0) + 1, "last_used_at": _now().isoformat()}
        data["last"][pose["agent"]] = pose["pose_id"]
        _write(POSES_KEY, data)
        return pose
    except Exception as e:
        print(f"[{SERVICE_NAME}] pose use not recorded ({type(e).__name__})")
        return {}


def find_poses(agent: str, query: str = "", transparent=None, client_id: int = None, limit: int = 5,
               data: dict = None) -> list:
    """APPROVED poses of one agent, best first: match score (the same scorer
    as core/drive_assets) minus the usage penalty (core/asset_feedback), and
    the pose this agent used last always ranks last — the same pose is not
    repeated in consecutive posts. `transparent` True = cut-outs only;
    `client_id` given = global cut-outs plus that client's own scenes."""
    from core import asset_feedback, drive_assets
    agent = clean_agent(agent)
    data = data if data is not None else _library()
    last = (data.get("last") or {}).get(agent, "")
    qwords = drive_assets._query_words(query)
    usage = {"usage": {p["pose_id"]: p.get("uses") or {} for p in data["poses"]}}
    out = []
    for p in data["poses"]:
        if p.get("agent") != agent or not p.get("approved") or not p.get("file_id"):
            continue
        if transparent is not None and bool(p.get("transparent")) != bool(transparent):
            continue
        if client_id is not None and p.get("client_id") not in (0, int(client_id)):
            continue
        item = {"tags": list(p.get("tags") or []) + [p.get(k, "") for k in TAG_FIELDS],
                "description": p.get("description", ""), "name": p.get("prompt", "")}
        score = drive_assets._match_score(item, "", qwords) if qwords else 0.0
        pen = asset_feedback.penalty(usage, p["pose_id"])
        out.append({**p, "score": round(score - pen, 4), "repeat": p["pose_id"] == last})
    out.sort(key=lambda p: (p["repeat"], -p["score"], p.get("created_at", "")))
    return out[:max(1, int(limit))]


def pose_counts() -> dict:
    try:
        data = _library()
    except Exception:
        return {}
    out = {}
    for p in data["poses"]:
        c = out.setdefault(p.get("agent", "?"), {"approved": 0, "pending": 0, "transparent": 0})
        c["approved" if p.get("approved") else "pending"] += 1
        c["transparent"] += 1 if p.get("transparent") else 0
    return out


def approve_poses(pose_ids: list, tags: dict = None, client_id: int = 0, item_id: str = "") -> list:
    """The manager approved a post: every character image it used enters the
    library as approved, tagged. Never raises."""
    ids = [str(p) for p in pose_ids or [] if POSE_ID_RE.match(str(p))]
    if not ids:
        return []
    try:
        data = _library()
        done = []
        for p in data["poses"]:
            if p.get("pose_id") in ids:
                p["approved"] = True
                p["approved_at"] = _now().isoformat()
                if item_id:
                    p["approved_item"] = f"{int(client_id or 0)}:{str(item_id)[:20]}"
                for k, v in ((tags or {}).get(p["pose_id"]) or {}).items():
                    if k in TAG_FIELDS and v and not p.get(k):
                        p[k] = _short(v, 120)
                done.append(p["pose_id"])
        if done:
            _write(POSES_KEY, data)
        return done
    except Exception as e:
        _alert([f"poses not marked approved ({type(e).__name__}: {str(e)[:120]})"])
        return []


def on_items_approved(client_id: int, items: list) -> list:
    """social_queue.approve's hook: the poses an approved item carries
    (item["characters"] = [{"agent", "pose_id"}]) and the poses in an
    approved design's spec/slides."""
    ids = []
    for item in items or []:
        ids += [c.get("pose_id") for c in item.get("characters") or [] if isinstance(c, dict)]
        d = item.get("design") or {}
        ids += poses_in_design(d)
    return approve_poses(list(dict.fromkeys(i for i in ids if i)), client_id=client_id,
                         item_id=(items[0]["id"] if items else ""))


def poses_in_design(d: dict) -> list:
    ids = []
    spec = (d or {}).get("spec") or {}
    for part in [spec] + list((spec.get("role_overrides") or {}).values()):
        if (part.get("character") or {}).get("pose_id"):
            ids.append(part["character"]["pose_id"])
    ids += [(s.get("character") or {}).get("pose_id") for s in (d or {}).get("slides") or []]
    return [i for i in ids if i]


# ─── Files: our Shared Drive, uallak-agents/<agent>/ ─────────────────────────

def _pose_folder(agent: str) -> str:
    from core import drive_service as drive, drive_storage
    root = drive_storage.shared_parent_id()
    if not root:
        raise RuntimeError("Missing key: DRIVE_MEDIA_FOLDER_ID")
    return drive.ensure_folder(agent, drive.ensure_folder(POSE_FOLDER, root))


def upload_pose_png(agent: str, png: bytes) -> dict:
    from core import drive_service as drive, drive_storage
    with drive_storage.as_service_account():
        folder = _pose_folder(agent)
        stamp = _now().strftime("%Y%m%d-%H%M%S")
        return drive.upload_bytes(folder, f"{agent}-pose-{stamp}.png", png, "image/png")


def pose_bytes(pose_id: str) -> bytes:
    """The pose's PNG (the renderer's loader, "pose:<id>")."""
    pose = get_pose(pose_id)
    if not pose or not pose.get("file_id"):
        raise LookupError(f"no pose {pose_id}")
    from core import drive_service as drive, drive_storage
    with drive_storage.as_service_account():
        return drive.download_file(pose["file_id"])


def make_pose(agent: str, pose: str, outfit: str = "", framing: str = "full body", setting: str = "",
              aspect_ratio: str = "3:4", _gen=None, _remove=None, _upload=None, _refs=None, _step=None) -> dict:
    """COMPOSITE step a: the pose from the agent's reference image on a plain
    background → background removal → a VERIFIED transparent PNG → the
    library. Runs on uallak's key. Raises MissingReference /
    CharacterUnavailable / BackgroundNotRemoved / the vendor's error.
    `_step`: the slide number its progress steps are reported under."""
    from core import media_gen_service as gen, production_progress as progress
    agent = clean_agent(agent)
    prompt = pose_prompt(agent, pose, outfit, framing)            # MissingReference first
    if _gen is None and not configured():
        raise CharacterUnavailable(agent, "not_configured", f"{KEY_ENV} is not set")
    refs = (_refs or reference_urls)([agent])
    key = agents_key()
    if _step is not None:
        progress.begin(f"pose:{_step}")
    made = (_gen or gen.generate_with_references)(key, prompt, refs, aspect_ratio)
    if _step is not None:
        progress.done(f"pose:{_step}")
        progress.begin(f"cutout:{_step}")
    cut = (_remove or _local_remove())(key, made["url"])
    png = ensure_transparent(cut["content"])
    if _step is not None:
        progress.done(f"cutout:{_step}")
    # The requested ratio exactly; the crop never clips the figure (warns instead)
    fitted = fit_aspect(png, aspect_ratio, made.get("aspect_ratio") or aspect_ratio)
    png = fitted["content"]
    if fitted["warning"]:
        _alert([f"pose for {agent}: {fitted['warning']}"])
    uploaded = (_upload or upload_pose_png)(agent, png)
    record = add_pose(agent, uploaded.get("id", ""), True, 0, outfit=outfit, setting=setting or "plain cut-out",
                      framing=framing, description=pose, tags=[w for w in re.findall(r"[a-z]{4,}", pose.lower())][:8],
                      source="generated", prompt=prompt)
    return {**record, "credits": (made.get("credits") or 0), "png": png, "cropped": fitted["cropped"],
            "warning": fitted["warning"]}


def pose_for(agent: str, pose: str, outfit: str = "", framing: str = "full body", **kw) -> dict:
    """COMPOSITE: a stored approved cut-out FIRST (free, no generation); else
    a new one (make_pose). The result's use is recorded."""
    found = find_poses(agent, " ".join([pose, outfit, framing]), transparent=True, limit=1)
    if found and not found[0]["repeat"]:
        record_pose_use(found[0]["pose_id"])
        return {**found[0], "reused": True, "credits": 0}
    made = make_pose(agent, pose, outfit, framing, **kw)
    record_pose_use(made["pose_id"])
    return {**made, "reused": False}


POSE_PARALLEL = 4        # poses generated at once for one post (each = one vendor job + local cut-out)


def poses_for(requests: list, stopped=None, steps: list = None) -> list:
    """pose_for for several slides of ONE post at once (2026-11-25): the
    library lookups run IN ORDER (so "the last pose is never repeated" holds
    exactly as before), then every pose that has to be MADE is generated in
    parallel (POSE_PARALLEL at a time) — four poses take about as long as
    one. requests = [(agent, pose)]; returns one result per request: the
    pose dict, or the exception it raised. `stopped()` True = a pose not yet
    started is not started (StopRequested). `steps`: per request, the slide
    number its progress steps (pose:<n>, cutout:<n>) are reported under."""
    from core import parallel, production_progress as progress
    steps = list(steps or [])

    def step_of(i):
        return steps[i] if i < len(steps) else None

    def settle(i, ok=True, reason="", skipped=False):
        n = step_of(i)
        if n is not None:
            progress.done(f"pose:{n}", ok, reason, skipped)
            progress.done(f"cutout:{n}", ok, reason, skipped)
    if stopped is None:
        def stopped():                  # inside a run: its card's stop
            try:
                from core import agent_actions
                return agent_actions.stop_requested()
            except Exception:
                return False
    out, to_make = [None] * len(requests), []
    for i, (agent, pose) in enumerate(requests):
        try:
            found = find_poses(agent, " ".join([pose, "", "full body"]), transparent=True, limit=1)
            if found and not found[0]["repeat"]:
                record_pose_use(found[0]["pose_id"])
                out[i] = {**found[0], "reused": True, "credits": 0}
                settle(i, skipped=True)               # a stored cut-out: nothing to make
                continue
        except Exception as e:
            out[i] = e
            settle(i, False, failure_text(agent, e))
            continue
        to_make.append(i)
    gate = _threading.Semaphore(POSE_PARALLEL)

    def make(i):
        with gate:
            if stopped and stopped():
                raise StopRequested()
            agent, pose = requests[i]
            try:
                made = make_pose(agent, pose, _step=step_of(i))
            except Exception as e:
                settle(i, False, failure_text(agent, e))
                raise
            record_pose_use(made["pose_id"])
            return {**made, "reused": False}
    got = parallel.gather({i: (lambda i=i: make(i)) for i in to_make})
    for i, (ok, value) in got.items():
        out[i] = value
    return out


class StopRequested(RuntimeError):
    """The run was stopped before this step started — nothing was submitted."""
    reason = "stopped"


# ─── Approval-card notices (shown, never blocking) ───────────────────────────

AI_PEOPLE_FORBIDDEN_RE = re.compile(
    r"(no|never|without|avoid|forbid\w*|do not use|don't use)\b[^.\n]{0,40}\b(ai|generated|artificial)[ -]?"
    r"(generated\s+)?(people|persons?|faces?|humans?|characters?|models?|avatars?)"
    r"|ללא\s+(אנשים|דמויות)\s+(שנוצרו|מחוללים|AI)|בלי\s+(אנשים|דמויות)\s+(של\s+)?(AI|בינה)", re.IGNORECASE)


def approval_flags(client_id: int, has_characters: bool, _brief_text=None) -> list:
    """["ai_person_forbidden"] when the client's brand brief forbids AI people,
    ["ai_label"] when an AI character appears (the platforms' AI-content
    label) — both only SHOWN on the approval card. Never raises."""
    if not has_characters:
        return []
    flags = ["ai_label"]
    try:
        if _brief_text is not None:
            text = _brief_text
        else:
            from core import brand_brief
            text = brand_brief.text(client_id) or ""
        if AI_PEOPLE_FORBIDDEN_RE.search(str(text)):
            flags.insert(0, "ai_person_forbidden")
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: brand brief unreadable for the AI-person check ({type(e).__name__})")
    return flags


# ─── The workspace: test poses from the reference set (the manager's only) ───
#
# The manager presses "generate" on /admin/characters/: a few poses are made
# with the SAME path as a real post's composite pose (pose_prompt — the
# reference set, the style clause, the plain background — on the configured
# model, at POSE_ASPECT). Each result waits as a CANDIDATE; nothing enters the
# library or the reference set by itself:
# - save    → the background removed + verified transparent (as make_pose)
#             → the pose library, APPROVED and tagged (pose, outfit, framing),
#             reused by posts like any library pose;
# - discard → gone;
# - promote → into the reference set, ONLY with an explicit confirm (identity
#             built from generated output drifts) — and only by the manager.

CANDS_KEY = "agent_pose_candidates"
MAX_CANDIDATES = 24
POSE_ASPECT = "3:4"
CAND_ID_RE = re.compile(r"^c[a-f0-9]{10}$")
DEFAULT_TEST_POSES = ("standing, waving hello with a big welcoming smile, turned slightly toward the viewer",
                      "pointing with one hand to the side, explaining, friendly and confident",
                      "giving a thumbs up, proud and relaxed")
JOB_STALE_MIN = 20                 # a "running" test older than this is treated as dead


def _candidates() -> dict:
    try:
        data = _read(CANDS_KEY)
    except Exception as e:
        print(f"[{SERVICE_NAME}] candidates unreadable ({type(e).__name__})")
        data = {}
    data.setdefault("agents", {})
    data.setdefault("jobs", {})
    data.setdefault("requests", {})
    return data


def _running(job: dict) -> bool:
    if (job or {}).get("state") != "running":
        return False
    try:
        then = datetime.fromisoformat(str(job.get("started_at") or "").replace("Z", "+00:00"))
    except ValueError:
        return False
    return _now() - then < timedelta(minutes=JOB_STALE_MIN)


def _begin(agent: str, poses: list, by: str, request: str = "") -> dict:
    """The checks and the job record every test run goes through — the
    workspace's button and a request from the chat alike. Refuses plainly:
    no reference, no key, an unverified model, a test already running."""
    reference_for(agent)                                          # MissingReference: the explicit line
    if not configured():
        raise CharacterUnavailable(agent, "not_configured", f"{KEY_ENV} is not set")
    from core import media_gen_service as gen
    gen.character_model()                                         # an unverified model path is refused
    data = _candidates()
    if _running(data["jobs"].get(agent)):
        raise ValueError("a test for this agent is already running")
    data["jobs"][agent] = {"state": "running", "started_at": _now().isoformat(), "poses": poses, "made": 0,
                           "credits": 0, "errors": [], "by": by, **({"request": request} if request else {})}
    _write(CANDS_KEY, data)
    return {"poses": poses}


def start_test(agent: str, instruction: str = "", count: int = 3, by: str = ADMIN) -> dict:
    """The workspace's button: checks and records a test run; returns
    {"poses"} for run_test (the route runs it in the background)."""
    _admin_only(agent, by)
    agent = clean_agent(agent)
    instruction = _strip_placeholders(re.sub(r"\s+", " ", str(instruction or ""))).strip()[:200]
    count = min(max(int(count or 3), 1), 3)
    poses = [instruction] * count if instruction else list(DEFAULT_TEST_POSES[:count])
    return _begin(agent, poses, by)


_CANDS_LOCK = _threading.Lock()    # the candidates row is read-modify-written by every pose of a run


def _default_budget(count: int) -> float:
    """A run's credit budget when the caller gives none: what `count` poses
    cost on average so far (estimate_credits), 0 = not measured yet = no cap."""
    try:
        each = estimate_credits()["each"]
    except Exception:
        each = None
    return float(each) * count if each else 0.0


def run_test(agent: str, poses: list, _gen=None, request: str = "", stopped=None, budget: float = None,
             _keep_fn=None) -> dict:
    """The test itself (background): one generation per pose, each result a
    candidate. Never raises; the job record says what happened. `request`: a
    chat request's id — its record gets each result too.

    In parallel (2026-11-25) with production's rules (post_pipeline
    ._generate_all): POSE_PARALLEL (4) at the vendor at once, waited for as a
    set — three poses take about as long as one. `stopped` is checked before
    EACH pose is submitted: once True nothing more is submitted (one already at
    the vendor finishes, paid). The credit `budget` (default: the measured
    average × the number of poses; 0 = none): before a pose is submitted, what
    was spent plus what the poses still running reserved (budget / poses
    each) must be under it.

    Each image is KEPT: copied to our Drive (uallak-agents/<agent>/) the
    moment it is made and served from there (`candidate_image`) — the
    provider's link lasts about seven days. A copy that fails keeps the link
    and alerts."""
    from core import media_gen_service as gen, parallel
    poses = list(poses or [])
    budget = _default_budget(len(poses)) if budget is None else float(budget or 0)
    each = budget / len(poses) if budget and poses else 0.0
    gate, lock = _threading.Semaphore(POSE_PARALLEL), _threading.Lock()
    ledger = {"spent": 0.0, "reserved": 0.0, "made": 0, "halted": False}
    errors = []

    def record(index, changes, cand=None):
        with _CANDS_LOCK:
            data = _candidates()
            if cand:
                data["agents"][agent] = ((data["agents"].get(agent) or []) + [cand])[-MAX_CANDIDATES:]
                job = data["jobs"].get(agent) or {}
                job.update(made=ledger["made"], credits=round(ledger["spent"], 2))
                data["jobs"][agent] = job
            if request:
                _request_result(data, request, index, changes)
            _write(CANDS_KEY, data)

    def one(index, pose):
        with gate:
            if stopped is not None and stopped():
                ledger["halted"] = True             # not submitted: nothing paid
                return
            with lock:
                if budget and ledger["spent"] + ledger["reserved"] >= budget:
                    errors.append("over the run's credit budget — not submitted")
                    over = True
                else:
                    ledger["reserved"] += each
                    over = False
            if over:
                record(index, {"state": "failed"})
                return
            ok, credits = False, 0.0
            try:
                prompt = pose_prompt(agent, pose, framing="full body")
                refs = reference_urls([agent])
                out = (_gen or gen.generate_with_references)(agents_key(), prompt, refs, POSE_ASPECT)
                ok, credits = True, float(out.get("credits") or 0)
            except Exception as e:
                errors.append(getattr(e, "message", "") or f"{type(e).__name__}: {str(e)[:200]}")
            finally:
                with lock:
                    ledger["reserved"] -= each
                    if ok:
                        ledger["spent"] += credits
                        ledger["made"] += 1
            if not ok:
                record(index, {"state": "failed"})
                return
        cand = {"cand_id": "c" + secrets.token_hex(5), "url": out["url"], "pose": _short(pose, 200),
                "outfit": "", "framing": "full body", "prompt": _short(prompt, 600),
                "credits": out.get("credits") or 0, "aspect_sent": out.get("aspect_ratio") or POSE_ASPECT,
                "model": out.get("model", ""), "refs": len(refs), "request_id": str(out.get("request_id") or ""),
                "created_at": _now().isoformat(), "promoted": False}
        if request:
            cand["request"] = request
        kept = keep_candidate(agent, cand["cand_id"], cand["url"], _keep_fn)    # outside the gate: not a vendor slot
        cand.update(kept)
        record(index, {"cand_id": cand["cand_id"], "url": cand["url"], "credits": cand["credits"],
                       "state": "pending", **kept}, cand)

    got = parallel.gather({i: (lambda i=i, p=p: one(i, p)) for i, p in enumerate(poses)})
    for _i, (fine, value) in got.items():
        if not fine:                                 # a bug in the bookkeeping, never silent
            errors.append(f"{type(value).__name__}: {str(value)[:200]}")
    with _CANDS_LOCK:
        data = _candidates()
        job = data["jobs"].get(agent) or {}
        made, credits = ledger["made"], round(ledger["spent"], 2)
        job.update(state="stopped" if ledger["halted"] else "done" if made else "failed",
                   finished_at=_now().isoformat(), made=made, credits=credits, errors=errors[:5],
                   parallel=POSE_PARALLEL, budget=round(budget, 2))
        data["jobs"][agent] = job
        if request and request in data["requests"]:
            data["requests"][request].update(state=job["state"], made=made, credits=credits,
                                             finished_at=job["finished_at"])
        _write(CANDS_KEY, data)
    if errors:
        _alert([f"character test for {agent}: {len(errors)} of {len(poses)} failed — {errors[0]}"])
    return job


def keep_candidate(agent: str, cand_id: str, url: str, _keep_fn=None) -> dict:
    """A generated pose copied to our Drive at once → {"file_id", "mime"};
    {} when the copy failed (the provider's link is used, alerted)."""
    try:
        content = _fetch(url)
        info = image_info(content)
        file_id = (_keep_fn or _keep)(agent, content, info["mime"], f"candidate-{cand_id}")
        if not file_id:
            raise RuntimeError("Drive returned no file id")
        return {"file_id": file_id, "mime": info["mime"]}
    except Exception as e:
        _alert([f"pose {cand_id} of {agent}: no durable copy kept ({type(e).__name__}: {str(e)[:150]}) — "
                f"its image shows from the provider's link, which expires in about seven days"])
        return {}


def image_source(entry: dict) -> dict:
    """Where a pose result's image is served from: {"file_id", "mime"} (our
    Drive copy — never expires) or {"url"} (the provider's link, only when no
    copy was kept)."""
    if (entry or {}).get("file_id"):
        return {"file_id": entry["file_id"], "mime": entry.get("mime") or "image/png"}
    if URL_RE.match(str((entry or {}).get("url") or "")):
        return {"url": entry["url"]}
    raise ValueError("no image")


def kept_image(file_id: str) -> bytes:
    return _kept_bytes(file_id)


def candidate_image(agent: str, cand_id: str) -> dict:
    """The workspace's candidate image source (see image_source)."""
    agent = clean_agent(agent)
    return image_source(_candidate(agent, cand_id, _candidates()))


def _candidate_bytes(cand: dict) -> bytes:
    """The candidate's image bytes: our kept copy first, else its link."""
    if cand.get("file_id"):
        return _kept_bytes(cand["file_id"])
    return _fetch(cand["url"])


# ─── Poses on request, from the chat (2026-11-25) ────────────────────────────
#
# The manager asks the media agent (ליאור) for poses of an agent character
# outside a post ("three poses of Maya to compare"). It is the SAME run as the
# workspace's button — _begin + run_test: the reference set, the style clause,
# the plain background, the configured model, POSE_ASPECT — never a second
# path. Generating from the manager's reference set writes no character (the
# set is read, never changed). Each result waits as a candidate, exactly like
# the button's: kept (שמירה למאגר) or discarded by the manager, in the chat or
# on /admin/characters/. A request record ties the results to the chat card:
# CANDS_KEY "requests": {request_id: {agent, client_id, proposal_id, poses,
# estimate, results[i]: {cand_id, url, credits, state}, state, made, credits}}.

MAX_REQUEST_POSES = 4              # per request: a mistyped "40" never makes 40 (and fits one request's time)
MAX_REQUESTS = 40
REQUEST_RE = re.compile(r"^q[a-f0-9]{10}$")
RESULT_STATES = ("waiting", "pending", "kept", "discarded", "failed")


def new_request_id() -> str:
    return "q" + secrets.token_hex(5)


def clean_pose(text) -> str:
    return _strip_placeholders(re.sub(r"\s+", " ", str(text or ""))).strip()[:200]


def _request_result(data: dict, request: str, index: int, changes: dict):
    entry = data["requests"].get(request)
    if not entry:
        return
    results = entry.setdefault("results", [])
    while len(results) <= index:
        results.append({"state": "waiting"})
    results[index].update(changes)


def estimate_credits() -> dict:
    """The cost BEFORE a run, from what past runs really cost: {"each",
    "runs"} — each = the average credits of one pose over the stored runs
    (jobs and requests); None when nothing was measured yet."""
    data = _candidates()
    made, spent = 0, 0.0
    for job in list(data["jobs"].values()) + list(data["requests"].values()):
        if int(job.get("made") or 0) > 0 and job.get("state") != "running":
            made += int(job["made"])
            spent += float(job.get("credits") or 0)
    if not made:
        return {"each": None, "runs": 0}
    return {"each": round(spent / made, 2), "runs": made}


def begin_request(agent: str, poses: list, request: str, client_id: int, proposal_id=None,
                  estimate=None) -> dict:
    """A chat request's run: checked and recorded through _begin (the same
    checks as the button), plus its request record."""
    agent = clean_agent(agent)
    poses = [clean_pose(p) for p in poses or [] if clean_pose(p)]
    if not 1 <= len(poses) <= MAX_REQUEST_POSES:
        raise ValueError(f"1-{MAX_REQUEST_POSES} poses per request")
    if not REQUEST_RE.match(str(request or "")):
        raise ValueError("bad request id")
    out = _begin(agent, poses, "media_agent", request)
    data = _candidates()
    data["requests"][request] = {"agent": agent, "client_id": int(client_id or 0), "proposal_id": proposal_id,
                                 "poses": poses, "estimate": estimate, "state": "running",
                                 "started_at": _now().isoformat(), "made": 0, "credits": 0,
                                 "results": [{"state": "waiting"} for _ in poses]}
    if len(data["requests"]) > MAX_REQUESTS:
        for old in sorted(data["requests"], key=lambda k: data["requests"][k].get("started_at", ""))[:-MAX_REQUESTS]:
            data["requests"].pop(old, None)
    _write(CANDS_KEY, data)
    return out


def request_view(request: str) -> dict:
    """What the chat card shows: per pose its instruction, state, credits and
    our own image path (never the vendor's URL); {} when unknown."""
    if not REQUEST_RE.match(str(request or "")):
        return {}
    entry = _candidates()["requests"].get(request)
    if not entry:
        return {}
    poses = []
    for i, text in enumerate(entry.get("poses") or []):
        r = (entry.get("results") or [])[i] if i < len(entry.get("results") or []) else {}
        state = r.get("state") if r.get("state") in RESULT_STATES else "waiting"
        row = {"pose": text, "state": state, "credits": r.get("credits") if state != "waiting" else None}
        if r.get("cand_id") and state in ("pending", "kept", "discarded"):
            row["cand_id"] = r["cand_id"]
            if state != "discarded":
                row["image_url"] = f"/api/client/characters/poses/{request}/{r['cand_id']}"
        poses.append(row)
    return {"request": request, "agent": entry.get("agent", ""), "name": AGENT_NAMES.get(entry.get("agent"), ""),
            "state": entry.get("state", ""), "made": int(entry.get("made") or 0),
            "credits": round(float(entry.get("credits") or 0), 2), "estimate": entry.get("estimate"),
            "client_id": int(entry.get("client_id") or 0), "poses": poses}


def _request_entry(client_id: int, request: str, cand_id: str) -> tuple:
    if not REQUEST_RE.match(str(request or "")) or not CAND_ID_RE.match(str(cand_id or "")):
        raise ValueError("bad id")
    entry = _candidates()["requests"].get(request) or {}
    if int(entry.get("client_id") or -1) != int(client_id or 0):
        raise ValueError("no such result")                       # another account's request = a missing one
    result = next((r for r in entry.get("results") or [] if r.get("cand_id") == cand_id), None)
    if not result:
        raise ValueError("no such result")
    return entry, result


def request_image_url(client_id: int, request: str, cand_id: str) -> str:
    """The stored result URL behind our own path, for THIS account only."""
    _entry, result = _request_entry(client_id, request, cand_id)
    if result.get("state") == "discarded" or not URL_RE.match(str(result.get("url") or "")):
        raise ValueError("no such result")
    return result["url"]


def request_image(client_id: int, request: str, cand_id: str) -> dict:
    """Where our own path serves this result's image from, for THIS account
    only: the kept Drive copy (never expires), else the provider's link."""
    _entry, result = _request_entry(client_id, request, cand_id)
    if result.get("state") == "discarded":
        raise ValueError("no such result")
    return image_source(result)


def request_decide(client_id: int, request: str, cand_id: str, verb: str, **kw) -> dict:
    """Keep / discard one result from the chat — the SAME save_candidate /
    discard_candidate as the workspace's buttons. The route allows it on the
    team's own (internal) accounts only, where the manager is the one person
    who also runs /admin/characters/."""
    entry, result = _request_entry(client_id, request, cand_id)
    if result.get("state") != "pending":
        raise ValueError("this result was already kept or discarded")
    if verb == "keep":
        save_candidate(entry["agent"], cand_id, by=ADMIN, **kw)
    elif verb == "discard":
        discard_candidate(entry["agent"], cand_id, by=ADMIN)
    else:
        raise ValueError("keep or discard")
    return request_view(request)


def _mark_result(cand_id: str, state: str, pose_id: str = ""):
    """A candidate kept or discarded (here or on the workspace): its request
    record says so, so the chat card shows it."""
    data = _candidates()
    changed = False
    for entry in data["requests"].values():
        for r in entry.get("results") or []:
            if r.get("cand_id") == cand_id and r.get("state") == "pending":
                r["state"] = state
                if pose_id:
                    r["pose_id"] = pose_id
                changed = True
    if changed:
        _write(CANDS_KEY, data)


def _candidate(agent: str, cand_id: str, data: dict) -> dict:
    if not CAND_ID_RE.match(str(cand_id or "")):
        raise ValueError("bad candidate id")
    cand = next((c for c in data["agents"].get(agent) or [] if c.get("cand_id") == cand_id), None)
    if not cand:
        raise ValueError("no such result (already saved or discarded?)")
    return cand


def _drop_candidate(agent: str, cand_id: str):
    data = _candidates()
    data["agents"][agent] = [c for c in data["agents"].get(agent) or [] if c.get("cand_id") != cand_id]
    _write(CANDS_KEY, data)


def save_candidate(agent: str, cand_id: str, pose: str = "", outfit: str = "", framing: str = "",
                   by: str = ADMIN, _remove=None, _upload=None) -> dict:
    """"שמירה למאגר": the result's background removed and VERIFIED
    transparent, cropped like make_pose, into the pose library APPROVED and
    tagged. A failed background removal keeps the result and says so."""
    _admin_only(agent, by)
    agent = clean_agent(agent)
    from core import media_gen_service as gen
    cand = _candidate(agent, cand_id, _candidates())
    if cand.get("file_id") and _remove is None:
        # the kept copy: still there after the provider's link expired
        from core import background_removal
        cut = {"content": background_removal.cut_out(_candidate_bytes(cand))}
    else:
        cut = (_remove or _local_remove())(agents_key(), cand["url"])
    png = ensure_transparent(cut["content"])
    fitted = fit_aspect(png, POSE_ASPECT, cand.get("aspect_sent") or POSE_ASPECT)
    uploaded = (_upload or upload_pose_png)(agent, fitted["content"])
    pose = _short(pose or cand.get("pose"), 200)
    record = add_pose(agent, uploaded.get("id", ""), True, 0, outfit=outfit, setting="plain cut-out",
                      framing=framing or cand.get("framing", ""), description=pose,
                      tags=[w for w in re.findall(r"[a-z]{4,}", pose.lower())][:8], source="manual",
                      prompt=cand.get("prompt", ""))
    approve_poses([record["pose_id"]])
    _drop_candidate(agent, cand_id)
    _mark_result(cand_id, "kept", record["pose_id"])
    out = view()
    out["saved"] = record["pose_id"]
    out["warning"] = fitted["warning"]
    return out


def discard_candidate(agent: str, cand_id: str, by: str = ADMIN) -> dict:
    _admin_only(agent, by)
    agent = clean_agent(agent)
    _candidate(agent, cand_id, _candidates())
    _drop_candidate(agent, cand_id)
    _mark_result(cand_id, "discarded")
    return view()


def promote_candidate(agent: str, cand_id: str, confirm: bool = False, by: str = ADMIN, **kw) -> dict:
    """A generated result INTO the reference set — deliberate only: `confirm`
    must be True (the screen shows the drift warning first). Never automatic,
    never by an agent."""
    _admin_only(agent, by)
    agent = clean_agent(agent)
    if confirm is not True:
        raise ValueError("promoting a generated image needs an explicit confirmation")
    data = _candidates()
    cand = _candidate(agent, cand_id, data)
    rid = str(cand.get("request_id") or "")[:120]
    if cand.get("file_id") and "_fetch_fn" not in kw:
        # from the kept copy (the provider's link may have expired): uploaded afresh
        out = add_reference(agent, content=_candidate_bytes(cand), media_id=rid if MEDIA_ID_RE.match(rid) else "",
                            by=by, source="promoted", **kw)
    else:
        out = add_reference(agent, url=cand["url"], media_id=rid if MEDIA_ID_RE.match(rid) else "", by=by,
                            source="promoted", **kw)
    data = _candidates()
    for c in data["agents"].get(agent) or []:
        if c.get("cand_id") == cand_id:
            c["promoted"] = True
    _write(CANDS_KEY, data)
    _alert([f"a GENERATED image was promoted into {agent}'s reference set by {by} (deliberate, confirmed)"])
    return {**view(), "warning": out.get("warning", ""), "added": out.get("added")}


def remove_pose(pose_id: str, by: str = ADMIN) -> dict:
    """A pose out of the library (the manager's choice). Its file is trashed
    in our Drive, best effort; a client's in-scene image is only unlisted."""
    pose_id = str(pose_id or "")
    if not POSE_ID_RE.match(pose_id):
        raise ValueError("bad pose id")
    data = _library()
    pose = get_pose(pose_id, data)
    if not pose:
        raise ValueError("no such pose")
    _admin_only(pose.get("agent"), by)
    data["poses"] = [p for p in data["poses"] if p.get("pose_id") != pose_id]
    if data["last"].get(pose.get("agent")) == pose_id:
        data["last"].pop(pose["agent"], None)
    _write(POSES_KEY, data)
    if pose.get("transparent") and not pose.get("client_id") and pose.get("file_id"):
        try:
            from core import drive_service as drive, drive_storage
            with drive_storage.as_service_account():
                drive.trash_file(pose["file_id"])
        except Exception as e:
            print(f"[{SERVICE_NAME}] pose file not trashed ({type(e).__name__})")
    return view()

