"""Low-level image/video generation plumbing — Higgsfield Cloud API.

Vendor decision (revised 2026-07-19, replacing the direct-Gemini v1):
Higgsfield as the aggregator platform — it fronts Google's models (Veo,
Nano Banana) plus Kling/Seedance/Sora-class video models, its own Soul image
model, and avatar/voice capabilities the future Avatar Agent will want. One
integration, many models.

BILLING MODEL — the client pays, we operate (same principle as ad spend, SEO
tools, and WordPress Application Passwords):
- Each client signs up at higgsfield.ai with THEIR OWN payment method and
  picks a plan sized to their volume (Starter $15 / Plus $39 / Ultra $99 per
  month, Stripe-billed to their card - we never see it).
- They create an API key in Higgsfield's console and paste it in ONE field
  (the tile); we store it per client (client_accounts, platform='higgsfield')
  and every generation runs on THEIR key, drawing THEIR plan credits.

AUTH (fixed 2026-11-22 from docs.higgsfield.ai/docs/authentication — the
earlier "Bearer" guess was answered 401 "Invalid credentials" on every call):
  Authorization: Key KEY_ID:KEY_SECRET        (base https://api.higgsfield.ai)
"Do not use a Bearer token" (their FAQ). A Higgsfield credential is a key ID
plus a secret; the ONE value the client pasted is turned into that header by
`auth_value()` — pasted as "id:secret" it is sent as is; pasted as the two
values separated by a space / tab / new line (copying both from the console)
they are joined with ":"; stray quotes, a pasted "Key "/"Bearer " prefix or an
"Authorization:" label are dropped. Nothing else is ever asked of the client,
and there is NO fallback to a key of ours.

API shape (docs/concepts/requests): POST {base}/{model path} with the inputs →
{request_id, status_url}; GET status_url (= {base}/requests/{id}/status) →
queued / in_progress / completed / failed / nsfw / canceled; a completed image
is images[0].url, a video video.url.
- Because the cost is the CLIENT'S, nothing here writes to client_costs
  (that table is OUR internal costs; recording client-paid generation there
  would corrupt the margin numbers). Credits usage is logged in activity
  rows instead.

Model paths are env-overridable (HIGGSFIELD_API_BASE, HIGGSFIELD_IMAGE_PATH,
HIGGSFIELD_VIDEO_PATH) — a rename is a Cloud Run env fix, not a deploy. The
OLD names (MEDIA_API_BASE / MEDIA_IMAGE_MODEL / MEDIA_VIDEO_MODEL) meant the
guessed API and are deliberately no longer read, so a stale value left on the
service can't keep pointing at it.

Daily caps (api_call_counters) now protect the CLIENT's credit balance from
a runaway loop on our side — arguably more important than when we paid.

Business logic lives in agents/media_agent.py; this module only talks HTTP.

VERIFICATION STATUS: auth, base, image/video paths and the request/status
shapes follow the docs as read 2026-11-22; the first live generation on a
client key is still the real proof. The presenter path below is NOT in the
docs read and stays an env setting.
"""
import os
import re
import time
import uuid

import httpx

from core.api_call_counters import increment_call_counter

API_BASE = os.environ.get("HIGGSFIELD_API_BASE", "https://api.higgsfield.ai").rstrip("/")
IMAGE_PATH = os.environ.get("HIGGSFIELD_IMAGE_PATH", "higgsfield-ai/soul/standard")
VIDEO_PATH = os.environ.get("HIGGSFIELD_VIDEO_PATH", "minimax/hailuo-2.3/standard/text-to-video")
IMAGE_MODEL = IMAGE_PATH          # what the activity rows record as "model"
VIDEO_MODEL = VIDEO_PATH
IMAGE_RESOLUTION = os.environ.get("HIGGSFIELD_IMAGE_RESOLUTION", "720p")
VIDEO_SECONDS = 6                 # the documented default (6 or 10)
TIMEOUT = 120
POLL_SECONDS = 8
POLL_MAX_TRIES = 75  # up to ~10 minutes
# The first checks come sooner (2026-11-25): a job that finishes in 12 s is
# seen at ~12 s, not at the next multiple of 8 — then every POLL_SECONDS
POLL_START = (2, 2, 3, 3, 4, 5, 6)
POLL_TOTAL_SECONDS = POLL_SECONDS * POLL_MAX_TRIES

# Runaway brakes — these now guard the CLIENT'S credits, not our bill
DAILY_IMAGE_LIMIT = 100
DAILY_VIDEO_LIMIT = 15

VALID_ASPECT_RATIOS = ("1:1", "3:4", "4:3", "9:16", "16:9")


def _count(kind: str, limit: int):
    count = increment_call_counter(f"media_{kind}", window_days=1)
    if count > limit:
        raise RuntimeError(f"daily {kind} generation cap reached ({limit}) - refusing call")


_PREFIX_RE = re.compile(r"^(authorization\s*:\s*)?((key|bearer)\s+)?", re.IGNORECASE)
_QUOTES = "\"'`“”‘’«»"


def normalize_key(raw: str) -> str:
    """The ONE pasted value, cleaned — on save AND on every read: surrounding
    whitespace / quotes, an "Authorization:" label and a "Key "/"Bearer "
    prefix are dropped; a key ID and secret pasted as two values (space, tab
    or new line between them) become "id:secret". Anything else is kept as
    the client pasted it."""
    text = str(raw or "").replace("​", "").replace("﻿", "").strip().strip(_QUOTES).strip()
    text = _PREFIX_RE.sub("", text, count=1).strip().strip(_QUOTES).strip()
    parts = [p.strip(_QUOTES + ",;") for p in re.split(r"\s+", text) if p.strip(_QUOTES + ",;")]
    if len(parts) == 2 and ":" not in text:
        return f"{parts[0]}:{parts[1]}"
    if len(parts) > 1:
        joined = "".join(parts)                 # "id : secret" / a wrapped line
        return joined if joined.count(":") == 1 else text
    return parts[0] if parts else ""


def clean_part(raw: str) -> str:
    """ONE field of the card (Key ID or Key Secret): whitespace, quotes, an
    "Authorization:" label and a "Key "/"Bearer " prefix dropped; inner
    whitespace too (a value never contains any)."""
    text = str(raw or "").replace("​", "").replace("﻿", "").strip().strip(_QUOTES).strip()
    text = _PREFIX_RE.sub("", text, count=1).strip().strip(_QUOTES).strip()
    if text.lower() in ("key", "bearer", "authorization", "authorization:"):
        return ""                                # only a prefix was pasted: the box is empty
    return re.sub(r"\s+", "", text)


class MissingPart(ValueError):
    """`part` is "key_id" or "key_secret": the card names that field."""
    def __init__(self, part: str):
        super().__init__(part)
        self.part = part


def split_credential(key_id: str, key_secret: str) -> tuple:
    """The card's two boxes → (id, secret), both required. A combined
    "id:secret" pasted into either box is split instead of refused (the
    other box may then be empty, or hold the same value again)."""
    boxes = [clean_part(key_id), clean_part(key_secret)]
    for i, value in enumerate(boxes):
        if ":" not in value:
            continue
        left, _, right = (clean_part(p) for p in value.partition(":"))
        if left and right:
            return left, right                   # the combined form: it IS both values
        boxes[i] = left or right                 # "id:" / ":secret" — that box's own value
    kid, secret = boxes
    if not kid:
        raise MissingPart("key_id")
    if not secret:
        raise MissingPart("key_secret")
    return kid, secret


def has_both_parts(stored: str) -> bool:
    left, sep, right = normalize_key(stored).partition(":")
    return bool(sep and left and right)


def key_shape(raw: str) -> dict:
    """For diagnosis ONLY — what the stored value looks like, never any part
    of it: lengths, whether it has the "id:secret" form, and whether cleaning
    changed it."""
    clean = normalize_key(raw)
    pieces = clean.split(":") if ":" in clean else [clean]
    return {"length": len(str(raw or "")), "clean_length": len(clean), "changed_by_cleaning": clean != str(raw or ""),
            "has_id_and_secret": len(pieces) == 2 and all(pieces),
            "part_lengths": [len(p) for p in pieces]}


def auth_value(api_key: str) -> str:
    return f"Key {normalize_key(api_key)}"


def _headers(api_key: str) -> dict:
    return {"Authorization": auth_value(api_key), "Content-Type": "application/json",
            "Accept": "application/json"}


class CredentialsRejected(RuntimeError):
    """Higgsfield answered 401/403: the stored key is not accepted. Callers
    mark the tool key_rejected (vendor_tools.mark_key_rejected)."""


def _raise_for(response, what: str):
    if response.status_code in (401, 403):
        raise CredentialsRejected(f"higgsfield {what}: {response.status_code} credentials not accepted")
    raise RuntimeError(f"higgsfield {what} failed: {response.status_code} {response.text[:300]}")


def _create_job(api_key: str, path: str, payload: dict) -> dict:
    """POST {base}/{model path} → {"request_id", "status_url"}."""
    headers = {**_headers(api_key), "Idempotency-Key": str(uuid.uuid4())}
    response = httpx.post(f"{API_BASE}/{path.strip('/')}", headers=headers, json=payload, timeout=TIMEOUT)
    if response.status_code not in (200, 201, 202):
        _raise_for(response, path)
    data = response.json() or {}
    request_id = data.get("request_id") or data.get("id") or data.get("job_id")
    if not request_id:
        raise RuntimeError(f"higgsfield {path} returned no request id: {str(data)[:300]}")
    status_url = data.get("status_url") or f"{API_BASE}/requests/{request_id}/status"
    if not str(status_url).startswith(API_BASE + "/"):
        status_url = f"{API_BASE}/requests/{request_id}/status"   # our key only ever goes to our base
    return {"request_id": request_id, "status_url": status_url}


def poll_waits() -> list:
    """The sleeps between status checks: POLL_START, then POLL_SECONDS, for
    about POLL_TOTAL_SECONDS in all."""
    waits, total = [], 0
    for w in list(POLL_START) + [POLL_SECONDS] * POLL_MAX_TRIES:
        if total + w > POLL_TOTAL_SECONDS:
            break
        waits.append(w)
        total += w
    return waits


def _wait_for_result(api_key: str, job: dict) -> dict:
    """Poll status_url until a terminal state; returns the completed payload."""
    request_id = job["request_id"]
    for wait in poll_waits():
        time.sleep(wait)
        response = httpx.get(job["status_url"], headers=_headers(api_key), timeout=TIMEOUT)
        if response.status_code != 200:
            _raise_for(response, "status poll")
        data = response.json() or {}
        status = (data.get("status") or "").lower()
        if status in ("completed", "succeeded", "success", "done"):
            return data
        if status == "nsfw":
            raise RuntimeError(f"higgsfield request {request_id} was blocked by moderation (nsfw)")
        if status in ("failed", "error", "canceled", "cancelled"):
            raise RuntimeError(f"higgsfield request {request_id} {status}: {str(data.get('error') or '')[:300]}")
    raise RuntimeError(f"higgsfield request {request_id} timed out (~10 minutes)")


def _result_url(data: dict) -> str:
    """The generated asset's URL: images[0].url / video.url (the documented
    shapes), then the older list/object shapes."""
    candidates = []
    for key in ("images", "videos", "results", "outputs", "assets"):
        value = data.get(key)
        if value:
            candidates += value if isinstance(value, list) else [value]
    for key in ("video", "image", "result"):
        if isinstance(data.get(key), dict):
            candidates.append(data[key])
    for item in candidates:
        if not isinstance(item, dict):
            continue
        url = (item.get("url") or (item.get("raw") or {}).get("url")
               or (item.get("video") or {}).get("url") or (item.get("image") or {}).get("url"))
        if url:
            return url
    raise RuntimeError(f"higgsfield job result had no asset url: {str(data)[:300]}")


def _credits_used(data: dict):
    return data.get("credits") or data.get("credits_used") or data.get("cost")


def _download(url: str) -> bytes:
    response = httpx.get(url, follow_redirects=True, timeout=TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(f"asset download failed: {response.status_code}")
    return response.content


# Post images — VERIFIED against the live server 2026-11-25
# (scripts/higgsfield_verify_models.sh, the field validation of
# higgsfield-ai/soul/standard): batch_size is validated (1 or 4; num_images is
# ignored — not a field), resolution only "720p" / "1080p" ("2K" rejected),
# aspect_ratio only the five below — 4:5 is rejected. So a 4:5 post image is
# made at the nearest one (3:4) and core/image_fit crops it from the centre;
# nothing outside these lists is ever sent. The model page was right,
# openapi.json was wrong. soul/v2/standard answered 400 with no field detail
# to every probe: unverified, so not in this list (an env value naming it is
# refused, never sent).
IMAGE_MODELS = {
    "higgsfield-ai/soul/standard": {
        "aspects": ("9:16", "16:9", "4:3", "3:4", "1:1"),
        "resolutions": ("720p", "1080p"), "count_field": "batch_size"},
}

def image_model(path: str = "") -> dict:
    """The documented entry for the post-image path, or UnverifiedModelPath."""
    path = (path or IMAGE_PATH).strip("/")
    model = IMAGE_MODELS.get(path)
    if not model:
        raise UnverifiedModelPath(f"HIGGSFIELD_IMAGE_PATH={path} is not a documented model path "
                                  f"(one of: {', '.join(IMAGE_MODELS)})")
    return {"path": path, **model}


def image_payload(prompt: str, aspect_ratio: str, path: str = "") -> dict:
    """The request body in the model's own fields; `aspect_ratio` is the
    model's nearest to the request."""
    from core import image_fit
    model = image_model(path)
    resolution = IMAGE_RESOLUTION if IMAGE_RESOLUTION in model["resolutions"] else model["resolutions"][0]
    return {"prompt": prompt, "aspect_ratio": image_fit.nearest(aspect_ratio, model["aspects"]),
            "resolution": resolution, model["count_field"]: 1}


def generate_image(api_key: str, prompt: str, aspect_ratio: str = "1:1") -> dict:
    """One image on the CLIENT'S key/credits, at the REQUESTED ratio (the
    model's nearest, then cropped). Returns {'content': bytes, 'mime',
    'credits', 'model', 'aspect_ratio' (the one sent), 'cropped'}. Raises on
    any failure."""
    from core import image_fit
    payload = image_payload(prompt, aspect_ratio)          # an undocumented path is refused here
    _count("image", DAILY_IMAGE_LIMIT)
    job = _create_job(api_key, IMAGE_PATH, payload)
    data = _wait_for_result(api_key, job)
    content = _download(_result_url(data))
    fitted = image_fit.fit_aspect(content, aspect_ratio, payload["aspect_ratio"]) if aspect_ratio else {
        "content": content, "cropped": False}
    return {"content": fitted["content"], "mime": "image/png", "credits": _credits_used(data), "model": IMAGE_MODEL,
            "aspect_ratio": payload["aspect_ratio"], "cropped": fitted["cropped"]}


# Presenter video (the client's character image + an audio track → a clip)
# — THE presenter path since HeyGen was retired (2026-10-31).
#
# 2026-11-25: the old `POST /generations {model: "wan2_7", medias: [...]}` was
# a reading of the MCP catalog and the live server answers 404. Now the
# DOCUMENTED Wan 2.7 image-to-video route (docs: models/wan-2-7/image-to-video,
# read 2026-11-25; the path answered the verify script's no-prompt probe as a
# validation error, i.e. it exists):
#   image_url (required) · audio_url ("audio_url can supply driving audio") ·
#   prompt · duration 2-15 (default 5) · resolution 720p | 1080p · seed ·
#   prompt_extend · negative_prompt · end_image_url.
# What the docs do NOT say: they never call it lip sync — the audio is
# "driving audio", nothing more is promised; and there is NO aspect_ratio
# field — the clip's frame follows the input image, so the caller passes a
# 9:16 image for a 9:16 clip. reference-to-video does not fit: no audio field,
# video_urls/image_urls references, 16:9 or 9:16 only.
# NOT YET RUN LIVE with a real generation: avatar_video keeps the presenter to
# internal test clients until it has (avatar_video.open_to_all).
PRESENTER_PATH = os.environ.get("MEDIA_PRESENTER_PATH", "wan/v2.7/image-to-video")
PRESENTER_MODEL = PRESENTER_PATH            # what the activity rows record as "model"
PRESENTER_RESOLUTION = os.environ.get("MEDIA_PRESENTER_RESOLUTION", "720p")
PRESENTER_RESOLUTIONS = ("720p", "1080p")
PRESENTER_MIN_SECONDS = 2
PRESENTER_MAX_SECONDS = 15   # the documented ceiling — a longer text is several clips, never cut
PRESENTER_PROMPT = ("The person in the image speaks directly to the camera, their mouth moving in sync with the "
                    "audio. Natural, subtle head and hand movement; same face, clothes and background.")


def presenter_payload(image_url: str, audio_url: str, seconds: int) -> dict:
    """The documented image-to-video body (no aspect_ratio field exists)."""
    resolution = PRESENTER_RESOLUTION if PRESENTER_RESOLUTION in PRESENTER_RESOLUTIONS else "720p"
    return {"image_url": image_url, "audio_url": audio_url, "prompt": PRESENTER_PROMPT,
            "duration": seconds, "resolution": resolution}


def generate_talking_character(api_key: str, image_url: str, audio_url: str,
                               seconds: int = 0, aspect_ratio: str = "9:16") -> dict:
    """One presenter clip from the character image and an audio URL, on the
    CLIENT'S key/credits. `seconds` is the audio's length (the caller refuses
    anything over PRESENTER_MAX_SECONDS before calling). `aspect_ratio` is
    not sent — the route has no such field; the frame follows the image.
    Returns {'url', 'job_id', 'credits', 'duration', 'model'} — the caller
    downloads and stores it."""
    seconds = int(min(max(round(seconds or PRESENTER_MIN_SECONDS), PRESENTER_MIN_SECONDS),
                      PRESENTER_MAX_SECONDS))
    _count("video", DAILY_VIDEO_LIMIT)
    job = _create_job(api_key, PRESENTER_PATH, presenter_payload(image_url, audio_url, seconds))
    data = _wait_for_result(api_key, job)
    return {"url": _result_url(data), "job_id": job["request_id"], "credits": _credits_used(data),
            "duration": data.get("duration") or seconds, "model": PRESENTER_MODEL}

def generate_video(api_key: str, prompt: str, aspect_ratio: str = "9:16") -> dict:
    """One short clip (VIDEO_SECONDS) on the CLIENT'S key/credits. The
    documented text-to-video model takes prompt + duration only (no aspect
    ratio field); `aspect_ratio` stays in the signature for the callers."""
    _count("video", DAILY_VIDEO_LIMIT)
    job = _create_job(api_key, VIDEO_PATH, {
        "prompt": prompt,
        "duration": VIDEO_SECONDS,
    })
    data = _wait_for_result(api_key, job)
    return {"content": _download(_result_url(data)), "mime": "video/mp4",
            "credits": _credits_used(data), "model": VIDEO_MODEL}


# ─── Character reference images (the AGENTS' characters, core/agent_characters)
#
# Fixed 2026-11-24 after production answered 405 on the element-list call:
# Higgsfield's public REST API has NO endpoint that lists reference elements
# (docs.higgsfield.ai, read 2026-11-24 — elements are a web-UI / MCP feature).
# So the manager stores ONE reference image per agent (/admin/characters/) and
# a generation passes it as an INPUT IMAGE in the model's own documented field:
#   xai/grok-imagine-image-2.0   image_urls: 0-10 URIs   (the default here)
#   alibaba/qwen-image-3/edit    image_urls: 1-3 URIs, required
# Both take several references, so several characters go in ONE image. The
# upload is the documented POST /files/generate-upload-url → PUT the bytes to
# upload_url with upload_headers (never our key) → public_url. Uploads are
# tagged retention=temporary by Higgsfield, so agent_characters keeps a
# durable copy in our Drive and re-uploads it when the URL is old.
# Runs on uallak's OWN key (HIGGSFIELD_AGENTS_KEY). No video model is wired
# for characters: none of the video pages read take character references.
#
# ONLY VERIFIED PATHS: a model enters CHARACTER_MODELS when its REST path,
# reference field and aspect enum are confirmed — from the REST docs, or by a
# 422 from scripts/higgsfield_probe_paths.sh (never by a guess). An env value
# that is not in the list is REFUSED with a clear line, never sent.
# Nano Banana Pro and FLUX 3 exist on Higgsfield's MCP / web platform but not
# in the REST docs (read 2026-11-24): not here until the probe verifies them.
#
# Aspect: the model gets the requested ratio when its enum has it; otherwise
# the NEAREST ratio it has, and agent_characters.fit_aspect crops the result to
# the requested one from the centre (4:5 from 3:4 = 6.25% off the height).
CHARACTER_IMAGE_PATH = os.environ.get("HIGGSFIELD_CHARACTER_IMAGE_PATH", "xai/grok-imagine-image-2.0")
CHARACTER_MODELS = {
    "xai/grok-imagine-image-2.0": {          # docs: models/grok-image-2/generate-and-edit
        "field": "image_urls", "max": 10,
        "aspects": ("1:1", "1:2", "2:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16")},
    "alibaba/qwen-image-3/edit": {           # docs: models/qwen-image-3/edit
        "field": "image_urls", "max": 3,
        "aspects": ("1:1", "2:3", "3:2", "3:4", "4:3", "7:9", "9:7", "9:16", "16:9", "21:9")},
}
UPLOAD_PATH = "files/generate-upload-url"
UPLOAD_TYPES = ("image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif")


class CharacterVideoUnsupported(ValueError):
    """No documented video model takes character reference images (yet)."""


class UnverifiedModelPath(ValueError):
    """HIGGSFIELD_CHARACTER_IMAGE_PATH names a path that is not verified."""


def character_model(path: str = "") -> dict:
    """The verified entry for `path` (default: the env setting), or
    UnverifiedModelPath — an unverified path is never sent."""
    path = (path or CHARACTER_IMAGE_PATH).strip("/")
    model = CHARACTER_MODELS.get(path)
    if not model:
        raise UnverifiedModelPath(f"HIGGSFIELD_CHARACTER_IMAGE_PATH={path} is not a verified model path "
                                  f"(one of: {', '.join(CHARACTER_MODELS)})")
    return {"path": path, **model}


def max_references(path: str = "") -> int:
    try:
        return character_model(path)["max"]
    except UnverifiedModelPath:
        return 0


def _ratio(ar: str) -> float:
    w, _, h = str(ar).partition(":")
    return float(w) / float(h)


def character_aspect(aspect_ratio: str, path: str = "") -> str:
    """The ratio sent to the model: the requested one when its enum has it,
    else the NEAREST one it has (the result is cropped to the request)."""
    from core import image_fit
    try:
        allowed = character_model(path)["aspects"]
    except UnverifiedModelPath:
        allowed = VALID_ASPECT_RATIOS
    if aspect_ratio not in allowed:
        try:
            image_fit.ratio(aspect_ratio)
        except (ValueError, ZeroDivisionError):
            return "3:4" if "3:4" in allowed else allowed[0]
    return image_fit.nearest(aspect_ratio, allowed)


def upload_file(api_key: str, content: bytes, content_type: str) -> str:
    """The documented upload: a presigned URL, the bytes PUT there with the
    returned headers (our key never goes to the storage host) → public_url."""
    content_type = "image/jpeg" if content_type == "image/jpg" else content_type
    if content_type not in UPLOAD_TYPES:
        raise ValueError(f"unsupported content type {content_type!r}")
    response = httpx.post(f"{API_BASE}/{UPLOAD_PATH}", headers=_headers(api_key),
                          json={"content_type": content_type}, timeout=TIMEOUT)
    if response.status_code not in (200, 201):
        _raise_for(response, "upload url")
    data = response.json() or {}
    upload_url, public_url = data.get("upload_url"), data.get("public_url")
    if not upload_url or not str(public_url or "").startswith("https://"):
        raise RuntimeError(f"higgsfield upload url: unexpected answer {str(list(data))[:200]}")
    headers = {str(k): str(v) for k, v in (data.get("upload_headers") or {}).items()} or {"Content-Type": content_type}
    put = httpx.put(upload_url, headers=headers, content=content, timeout=TIMEOUT)
    if put.status_code not in (200, 201, 204):
        raise RuntimeError(f"higgsfield upload failed: {put.status_code}")
    return public_url


def generate_with_references(api_key: str, prompt: str, reference_urls: list, aspect_ratio: str = "4:5",
                             kind: str = "image") -> dict:
    """One image with the characters' reference images in the model's OWN
    reference field (CHARACTER_MODELS), on uallak's key. Returns {'url',
    'content', 'mime', 'credits', 'model', 'request_id', 'aspect_ratio'} —
    aspect_ratio is what was SENT (the caller crops when it differs from the
    request); the url is kept for background removal."""
    if kind == "video":
        raise CharacterVideoUnsupported("a video with an agent character is not supported: no documented "
                                        "Higgsfield video model takes character reference images")
    model = character_model()                                   # an unverified path is refused here
    refs = [str(u) for u in reference_urls or []]
    if not refs or not all(u.startswith("https://") for u in refs):
        raise ValueError("a character generation needs the reference image URL(s)")
    if len(refs) > model["max"]:
        raise ValueError(f"{model['path']} takes at most {model['max']} reference images")
    sent = character_aspect(aspect_ratio, model["path"])
    _count("image", DAILY_IMAGE_LIMIT)
    job = _create_job(api_key, model["path"], {"prompt": prompt, model["field"]: refs, "aspect_ratio": sent})
    data = _wait_for_result(api_key, job)
    url = _result_url(data)
    return {"url": url, "content": _download(url), "mime": "image/png", "credits": _credits_used(data),
            "model": model["path"], "request_id": job["request_id"], "aspect_ratio": sent}
