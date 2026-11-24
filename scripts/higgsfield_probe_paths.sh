#!/usr/bin/env bash
# Discover which Higgsfield REST model paths REALLY exist — by what the server
# answers, not by guessing (core/media_gen_service CHARACTER_MODELS).
#
#   bash scripts/higgsfield_probe_paths.sh
#
# Runs in Cloud Shell with uallak's key (HIGGSFIELD_AGENTS_KEY, read from
# Secret Manager into memory — never printed, never written to disk).
#
# SAFE — nothing is generated, nothing is charged:
# - every request has NO prompt and an INVALID aspect_ratio ("0:0"), so a real
#   model path fails field validation (422) before any generation; a wrong
#   path answers 404 / 405;
# - two CONTROLS run first: a documented path (must answer 422) and a made-up
#   one (must answer 404/405). If the controls don't behave, the run says the
#   results can't be trusted;
# - if anything is ever ACCEPTED (200/201/202 with a request id), it is
#   cancelled at once (POST /requests/{id}/cancel) and reported loudly.
#
# For every path that answers 422, a second request per candidate reference
# field (image_urls, image_references, input_images, medias, …) with a wrong
# type shows whether the server validates that field by name.
#
# Paste the whole output back: a model is added to the code ONLY on a
# verified 422, with the field the server named.
set -euo pipefail

PROJECT=super-system-500410
SECRET=HIGGSFIELD_AGENTS_KEY

HF_KEY=$(gcloud secrets versions access latest --secret "$SECRET" --project "$PROJECT")
if [ -z "$HF_KEY" ]; then
  echo "No $SECRET in Secret Manager — run scripts/set_higgsfield_agents_key.sh first."; exit 1
fi
export HF_KEY

python3 - <<'PY'
import json, os, re, urllib.request, urllib.error

BASE = "https://api.higgsfield.ai"
KEY = os.environ.pop("HF_KEY").strip()
HEADERS = {"Authorization": f"Key {KEY}", "Content-Type": "application/json", "Accept": "application/json"}
SAFE_BODY = {"aspect_ratio": "0:0"}            # no prompt + an invalid enum value: can never generate

CONTROLS = [("xai/grok-imagine-image-2.0", "422"),          # documented — must be a validation error
            ("uallak-probe/definitely-not-a-model", "404")]  # made up — must not exist
CANDIDATES = {
    "Nano Banana Pro": [
        "google/nano-banana-pro", "google/nano-banana-pro/edit", "google/nano-banana-pro/text-to-image",
        "google/nano-banana-pro/image-to-image", "google/gemini-3-pro-image", "google/gemini-3-pro-image/edit",
        "google/gemini-3-pro-image-preview", "google/nano-banana", "google/nano-banana/edit",
        "nano-banana-pro", "nano_banana_pro", "higgsfield-ai/nano-banana-pro", "v1/nano-banana-pro",
    ],
    "FLUX 3": [
        "black-forest-labs/flux-3", "black-forest-labs/flux-3/edit", "black-forest-labs/flux-3/text-to-image",
        "black-forest-labs/flux-3/image-to-image", "black-forest-labs/flux-3-image", "bfl/flux-3", "bfl/flux-3/edit",
        "flux/flux-3", "flux-3", "flux_3_image", "black-forest-labs/flux-2", "black-forest-labs/flux-kontext",
    ],
}
FIELDS = ["image_urls", "image_references", "input_images", "reference_images", "medias", "images", "image_url"]


def clean(text):
    text = text.replace(KEY, "***")                                   # the key never reaches the screen
    return re.sub(r"\s+", " ", text)[:260]


def post(path, body):
    req = urllib.request.Request(f"{BASE}/{path}", data=json.dumps(body).encode(), headers=HEADERS, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def cancel_if_accepted(path, code, text):
    if code not in (200, 201, 202):
        return ""
    try:
        rid = (json.loads(text) or {}).get("request_id") or ""
    except ValueError:
        rid = ""
    if rid:
        c, _ = post(f"requests/{rid}/cancel", {})
        return f"  !! ACCEPTED (request {rid}) — cancel answered {c}"
    return "  !! ACCEPTED without a request id — check the Higgsfield console"


def probe(path):
    code, text = post(path, SAFE_BODY)
    warn = cancel_if_accepted(path, code, text)
    return code, clean(text), warn


print("== Controls")
trusted = True
for path, expect in CONTROLS:
    code, text, warn = probe(path)
    ok = (code == 422) if expect == "422" else code in (404, 405)
    trusted &= ok
    print(f"{code:>4}  {path}  (expected {expect}) {'OK' if ok else 'UNEXPECTED'}  {text}{warn}")
if not trusted:
    print("!! The controls did not behave as expected — do NOT trust the results below.")

verified = {}
for model, paths in CANDIDATES.items():
    print(f"\n== {model}")
    for path in paths:
        code, text, warn = probe(path)
        verdict = ("EXISTS (validation error)" if code == 422 else "not found" if code in (404, 405)
                   else "key refused" if code in (401, 403) else "unclear")
        print(f"{code:>4}  {path}  → {verdict}  {text}{warn}")
        if code == 422:
            verified.setdefault(model, []).append(path)

print("\n== Reference field, per path that answered 422")
for model, paths in verified.items():
    for path in paths:
        for field in FIELDS:
            code, text = post(path, {**SAFE_BODY, field: 12345})        # wrong type on purpose
            named = field in text
            print(f"{code:>4}  {path}  {field}: {'NAMED in the error' if named else 'not named'}  {clean(text)}")

print("\n== Summary")
for model in CANDIDATES:
    print(f"{model}: {', '.join(verified.get(model, [])) or 'no path answered 422'}")
print("Paste this whole output back. Nothing was generated.")
PY
