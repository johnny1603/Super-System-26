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


def _wait_for_result(api_key: str, job: dict) -> dict:
    """Poll status_url until a terminal state; returns the completed payload."""
    request_id = job["request_id"]
    for _ in range(POLL_MAX_TRIES):
        time.sleep(POLL_SECONDS)
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


def generate_image(api_key: str, prompt: str, aspect_ratio: str = "1:1") -> dict:
    """One image on the CLIENT'S key/credits. Returns {'content': bytes,
    'mime': ..., 'credits': ...}. Raises on any failure."""
    if aspect_ratio not in VALID_ASPECT_RATIOS:
        aspect_ratio = "1:1"
    _count("image", DAILY_IMAGE_LIMIT)
    job = _create_job(api_key, IMAGE_PATH, {
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "resolution": IMAGE_RESOLUTION,
        "batch_size": 1,
    })
    data = _wait_for_result(api_key, job)
    return {"content": _download(_result_url(data)), "mime": "image/png",
            "credits": _credits_used(data), "model": IMAGE_MODEL}


# Presenter video (the client's character image + an audio track → a
# lip-synced clip) — THE presenter path since HeyGen was retired (2026-10-31).
#
# PRESENTER_MODEL is the ONE place the model is named; callers never see it.
# "wan2_7" (Wan 2.7, in Higgsfield's catalog as "Synchronized audio,
# character-consistent video") was picked because it takes exactly our two
# inputs — a `start_image` (the character) and `audio_references` (the voice)
# — keeps the face consistent, and syncs to the given audio rather than
# generating its own; 2-15 s, 720p/1080p, 9:16 among its ratios. Seedance
# 2.0/2.5 also accept audio references but add generated audio and video
# references on top, and cost more; Grok Video 1.5 is a preview.
#
# NOT YET RUN LIVE: the model id, roles and limits come from Higgsfield's
# model catalog (read 2026-10-31); the REST path and the shape of the media
# list below are our reading of it and are env settings, so a mismatch is a
# Cloud Run env fix, not a deploy. Until a live run, avatar_video keeps the
# presenter to internal test clients (avatar_video.open_to_all).
PRESENTER_MODEL = os.environ.get("MEDIA_PRESENTER_MODEL", "wan2_7")
PRESENTER_PATH = os.environ.get("MEDIA_PRESENTER_PATH", "generations")
PRESENTER_RESOLUTION = os.environ.get("MEDIA_PRESENTER_RESOLUTION", "720p")
PRESENTER_MIN_SECONDS = 2
PRESENTER_MAX_SECONDS = 15   # the model's ceiling — a longer text is several clips, never cut


def generate_talking_character(api_key: str, image_url: str, audio_url: str,
                               seconds: int = 0, aspect_ratio: str = "9:16") -> dict:
    """One lip-synced presenter clip from the character image and an audio
    URL, on the CLIENT'S key/credits. `seconds` is the audio's length (the
    caller refuses anything over PRESENTER_MAX_SECONDS before calling).
    Returns {'url', 'job_id', 'credits', 'duration', 'model'} — the caller
    downloads and stores it."""
    seconds = int(min(max(round(seconds or PRESENTER_MIN_SECONDS), PRESENTER_MIN_SECONDS),
                      PRESENTER_MAX_SECONDS))
    if aspect_ratio not in VALID_ASPECT_RATIOS:
        aspect_ratio = "9:16"
    _count("video", DAILY_VIDEO_LIMIT)
    job = _create_job(api_key, PRESENTER_PATH, {
        "model": PRESENTER_MODEL,
        "prompt": "The person in the image speaks directly to the camera, in sync with the audio. "
                  "Natural, subtle head and hand movement; same face, clothes and background.",
        "duration": seconds,
        "resolution": PRESENTER_RESOLUTION,
        "aspect_ratio": aspect_ratio,
        "medias": [{"role": "start_image", "url": image_url},
                   {"role": "audio_references", "url": audio_url}],
    })
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
