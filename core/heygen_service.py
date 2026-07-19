"""Low-level HeyGen plumbing — REAL-PERSON avatars (digital twins / photo
avatars) and avatar video generation. HTTP only; business logic, consent
enforcement, and minutes tracking live in agents/avatar_agent.py.

Why HeyGen and not Higgsfield (research 2026-07-19): Higgsfield generates
INVENTED consistent characters; HeyGen is the specialist for cloning a real
person's face with accurate lip-sync and reusable custom avatars. Different
tool for a different job — never merge with media_gen_service.

Billing: the CLIENT'S own HeyGen account and API key (client_accounts,
platform='heygen') — same model as Higgsfield/WordPress/SEO tools. Their
plan, their card; we only operate.

API reality (verified against docs.heygen.com, 2026-07-19):
- Photo Avatar creation/training: available on standard API plans
  (photo_avatar group endpoints) — fully automatable here.
- VIDEO Digital Twin CREATION via API (training footage + consent video) is
  ENTERPRISE-ONLY. On a normal client account the twin is created in
  HeyGen's web UI (where HeyGen itself requires the client to record a
  consent-statement video — mirroring our own consent step); once it exists,
  listing it and GENERATING videos with it work on any API plan.
  submit_digital_twin() below is implemented for accounts that do have it
  and fails with a clear message for those that don't.

VERIFICATION STATUS: written against the public docs, never run with a live
key — first calls against a real client key are a required test.
"""
import os
import time

import httpx

API_BASE = os.environ.get("HEYGEN_API_BASE", "https://api.heygen.com")
UPLOAD_BASE = os.environ.get("HEYGEN_UPLOAD_BASE", "https://upload.heygen.com")
TIMEOUT = 60
STATUS_POLL_SECONDS = 15
STATUS_POLL_MAX_TRIES = 60  # video render: up to ~15 minutes


def _headers(api_key: str) -> dict:
    return {"X-Api-Key": api_key, "Content-Type": "application/json"}


def _check(response: httpx.Response, what: str) -> dict:
    if response.status_code != 200:
        raise RuntimeError(f"heygen {what} failed: {response.status_code} {response.text[:300]}")
    body = response.json()
    if body.get("error"):
        raise RuntimeError(f"heygen {what} error: {str(body['error'])[:300]}")
    return body.get("data") or body


def list_avatars(api_key: str) -> list:
    """Every avatar on the client's account (stock + their own twins/photo
    avatars). The readiness scan diffs this to detect a newly trained twin."""
    data = _check(httpx.get(f"{API_BASE}/v2/avatars", headers=_headers(api_key),
                            timeout=TIMEOUT), "list_avatars")
    return data.get("avatars") or []


def upload_asset(api_key: str, content: bytes, mime_type: str) -> dict:
    """Upload one media asset (image/video/audio) to HeyGen's asset store —
    returns {id, url, ...}. Used to move source material from Drive into the
    client's HeyGen account."""
    response = httpx.post(
        f"{UPLOAD_BASE}/v1/asset",
        headers={"X-Api-Key": api_key, "Content-Type": mime_type},
        content=content,
        timeout=120,
    )
    return _check(response, "upload_asset")


def create_photo_avatar_group(api_key: str, name: str, image_keys: list) -> dict:
    """Photo Avatar path (standard API plans): create a group from the
    client's photos, then train it. Returns the creation response (group id)."""
    response = httpx.post(
        f"{API_BASE}/v2/photo_avatar/avatar_group/create",
        headers=_headers(api_key),
        json={"name": name, "image_keys": image_keys},
        timeout=TIMEOUT,
    )
    return _check(response, "create_photo_avatar_group")


def train_photo_avatar_group(api_key: str, group_id: str) -> dict:
    response = httpx.post(
        f"{API_BASE}/v2/photo_avatar/train",
        headers=_headers(api_key),
        json={"group_id": group_id},
        timeout=TIMEOUT,
    )
    return _check(response, "train_photo_avatar_group")


def submit_digital_twin(api_key: str, avatar_name: str,
                        training_footage_url: str, video_consent_url: str) -> dict:
    """VIDEO digital twin creation — ENTERPRISE-ONLY on HeyGen's side. Both
    URLs must be publicly fetchable (drive_service.make_file_public). On a
    non-Enterprise key this returns HeyGen's permission error, which the
    agent surfaces with the web-UI fallback instructions."""
    response = httpx.post(
        f"{API_BASE}/v2/video_avatar",
        headers=_headers(api_key),
        json={"avatar_name": avatar_name,
              "training_footage_url": training_footage_url,
              "video_consent_url": video_consent_url},
        timeout=TIMEOUT,
    )
    return _check(response, "submit_digital_twin")


def generate_avatar_video(api_key: str, avatar_id: str, script_text: str,
                          voice_id: str = "", audio_url: str = "",
                          width: int = 720, height: int = 1280,
                          title: str = "") -> str:
    """One avatar video. Voice: either a HeyGen voice_id speaking
    script_text, or a ready audio file URL (e.g. ElevenLabs output made
    public). Returns HeyGen's video_id for polling."""
    if audio_url:
        voice = {"type": "audio", "audio_url": audio_url}
    else:
        voice = {"type": "text", "input_text": script_text, "voice_id": voice_id}
    response = httpx.post(
        f"{API_BASE}/v2/video/generate",
        headers=_headers(api_key),
        json={
            "title": title or "uallak avatar video",
            "video_inputs": [{
                "character": {"type": "avatar", "avatar_id": avatar_id,
                              "avatar_style": "normal"},
                "voice": voice,
            }],
            "dimension": {"width": width, "height": height},
        },
        timeout=TIMEOUT,
    )
    data = _check(response, "generate_avatar_video")
    video_id = data.get("video_id")
    if not video_id:
        raise RuntimeError(f"heygen generate returned no video_id: {str(data)[:300]}")
    return video_id


def wait_for_video(api_key: str, video_id: str) -> dict:
    """Poll until the render completes. Returns {video_url, duration, ...} —
    duration (seconds) is what the agent bills minutes against."""
    for _ in range(STATUS_POLL_MAX_TRIES):
        time.sleep(STATUS_POLL_SECONDS)
        response = httpx.get(
            f"{API_BASE}/v1/video_status.get",
            headers=_headers(api_key), params={"video_id": video_id},
            timeout=TIMEOUT,
        )
        data = _check(response, "video_status")
        status = (data.get("status") or "").lower()
        if status == "completed":
            return data
        if status in ("failed", "error"):
            raise RuntimeError(f"heygen video {video_id} failed: {str(data)[:300]}")
    raise RuntimeError(f"heygen video {video_id} timed out (~15 minutes)")
