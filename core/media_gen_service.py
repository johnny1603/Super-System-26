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
- They create an API key at cloud.higgsfield.ai/api-keys and hand it to us;
  we store it per client (client_accounts, platform='higgsfield') and every
  generation runs on THEIR key, drawing THEIR plan credits. Verified from the
  docs: API auth is a Bearer key per account, and plan credits are what API
  generations consume - so per-client keys ARE the supported multi-tenant
  path. (The Team plan is the opposite: one shared org wallet = us absorbing
  costs - deliberately not used.)
- Because the cost is the CLIENT'S, nothing here writes to client_costs
  (that table is OUR internal costs; recording client-paid generation there
  would corrupt the margin numbers). Credits usage is logged in activity
  rows instead.

Job model: create generation → poll job status → download result. Endpoint
paths and model slugs are env-overridable (MEDIA_API_BASE, MEDIA_IMAGE_MODEL,
MEDIA_VIDEO_MODEL) because the platform is young and renames happen — a
rename is a Cloud Run env fix, not a deploy.

Daily caps (api_call_counters) now protect the CLIENT's credit balance from
a runaway loop on our side — arguably more important than when we paid.

Business logic lives in agents/media_agent.py; this module only talks HTTP.

VERIFICATION STATUS: written against the public docs/SDK, never run with a
live key — the first generation against a real client key is a required
test, and the job-status/result response shapes are the likeliest one-round
fix.
"""
import os
import time

import httpx

from core.api_call_counters import increment_call_counter

API_BASE = os.environ.get("MEDIA_API_BASE", "https://platform.higgsfield.ai/v1")
IMAGE_MODEL = os.environ.get("MEDIA_IMAGE_MODEL", "soul")
VIDEO_MODEL = os.environ.get("MEDIA_VIDEO_MODEL", "veo-3-fast")
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


def _headers(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def _create_job(api_key: str, path: str, payload: dict) -> str:
    response = httpx.post(f"{API_BASE}/{path.lstrip('/')}", headers=_headers(api_key),
                          json=payload, timeout=TIMEOUT)
    if response.status_code not in (200, 201, 202):
        raise RuntimeError(f"higgsfield {path} failed: {response.status_code} {response.text[:300]}")
    data = response.json()
    job_id = data.get("id") or data.get("job_id") or (data.get("job") or {}).get("id")
    if not job_id:
        raise RuntimeError(f"higgsfield {path} returned no job id: {str(data)[:300]}")
    return job_id


def _wait_for_result(api_key: str, job_id: str) -> dict:
    """Poll the job until completion; returns the job payload. Tolerates the
    two status vocabularies seen in the docs/SDK (completed/failed and
    succeeded/error)."""
    for _ in range(POLL_MAX_TRIES):
        time.sleep(POLL_SECONDS)
        response = httpx.get(f"{API_BASE}/jobs/{job_id}", headers=_headers(api_key),
                             timeout=TIMEOUT)
        if response.status_code != 200:
            raise RuntimeError(f"higgsfield job poll failed: {response.status_code} {response.text[:200]}")
        data = response.json()
        status = (data.get("status") or "").lower()
        if status in ("completed", "succeeded", "success", "done"):
            return data
        if status in ("failed", "error", "canceled", "cancelled"):
            raise RuntimeError(f"higgsfield job {job_id} failed: {str(data)[:300]}")
    raise RuntimeError(f"higgsfield job {job_id} timed out (~10 minutes)")


def _result_url(data: dict) -> str:
    """The generated asset's URL, across the response shapes in the docs:
    results/outputs lists or a single result object, each with url/raw url."""
    candidates = (data.get("results") or data.get("outputs") or data.get("assets") or [])
    if isinstance(candidates, dict):
        candidates = [candidates]
    if not candidates and data.get("result"):
        candidates = [data["result"]]
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
    job_id = _create_job(api_key, "text2image", {
        "model": IMAGE_MODEL,
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
    })
    data = _wait_for_result(api_key, job_id)
    return {"content": _download(_result_url(data)), "mime": "image/png",
            "credits": _credits_used(data), "model": IMAGE_MODEL}


def generate_video(api_key: str, prompt: str, aspect_ratio: str = "9:16") -> dict:
    """One short clip (model-default length, typically ~5-8s with native
    audio on the Veo-class models) on the CLIENT'S key/credits."""
    if aspect_ratio not in VALID_ASPECT_RATIOS:
        aspect_ratio = "9:16"
    _count("video", DAILY_VIDEO_LIMIT)
    job_id = _create_job(api_key, "text2video", {
        "model": VIDEO_MODEL,
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
    })
    data = _wait_for_result(api_key, job_id)
    return {"content": _download(_result_url(data)), "mime": "video/mp4",
            "credits": _credits_used(data), "model": VIDEO_MODEL}
