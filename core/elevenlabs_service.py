"""Low-level ElevenLabs plumbing — professional VOICE CLONING of a real
person's voice. HTTP only; consent enforcement and business logic live in
agents/avatar_agent.py (a voice is someone's likeness exactly like a face —
the same mandatory-consent rigor applies).

Billing: the CLIENT'S own ElevenLabs account and API key (client_accounts,
platform='elevenlabs') — their plan, their card; we only operate. Instant
Voice Cloning (v1/voices/add) is available on their standard paid plans.

VERIFICATION STATUS: written against the public docs (this API has been
stable for years), never run with a live key.
"""
import os

import httpx

API_BASE = os.environ.get("ELEVENLABS_API_BASE", "https://api.elevenlabs.io")
TIMEOUT = 120


def _headers(api_key: str) -> dict:
    return {"xi-api-key": api_key}


def list_voices(api_key: str) -> list:
    response = httpx.get(f"{API_BASE}/v1/voices", headers=_headers(api_key), timeout=TIMEOUT)
    if response.status_code != 200:
        raise RuntimeError(f"elevenlabs list_voices failed: {response.status_code} {response.text[:300]}")
    return response.json().get("voices") or []


def create_voice_clone(api_key: str, name: str, samples: list, description: str = "") -> str:
    """Instant Voice Clone from audio samples. samples: list of
    (filename, bytes, mime_type) tuples — 1-3 clean minutes of speech total
    is the sweet spot. Returns the new voice_id."""
    files = [("files", (fname, content, mime)) for fname, content, mime in samples]
    response = httpx.post(
        f"{API_BASE}/v1/voices/add",
        headers=_headers(api_key),
        data={"name": name, "description": description or "uallak client voice"},
        files=files,
        timeout=300,  # uploads several audio files
    )
    if response.status_code != 200:
        raise RuntimeError(f"elevenlabs voice clone failed: {response.status_code} {response.text[:300]}")
    voice_id = response.json().get("voice_id")
    if not voice_id:
        raise RuntimeError(f"elevenlabs voice clone returned no voice_id: {response.text[:300]}")
    return voice_id


def text_to_speech(api_key: str, voice_id: str, text: str,
                   model_id: str = "eleven_multilingual_v2") -> bytes:
    """Speech audio (MP3 bytes) in the cloned voice — Hebrew works on the
    multilingual model. Feeds HeyGen's audio_url path after a Drive upload +
    make_file_public."""
    response = httpx.post(
        f"{API_BASE}/v1/text-to-speech/{voice_id}",
        headers={**_headers(api_key), "Content-Type": "application/json"},
        json={"text": text, "model_id": model_id},
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        raise RuntimeError(f"elevenlabs tts failed: {response.status_code} {response.text[:300]}")
    return response.content
