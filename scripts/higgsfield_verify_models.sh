#!/usr/bin/env bash
# Verify the post-image model, background removal and the presenter route
# against the REAL Higgsfield API, with uallak's key (2026-11-25).
#
#   bash scripts/higgsfield_verify_models.sh             # free: validation probes only
#   bash scripts/higgsfield_verify_models.sh --generate  # + ONE real image and ONE background removal (credits)
#
# The key is read from Secret Manager into memory — never printed, never
# written to disk; it is masked in everything shown.
#
# FREE part — nothing can generate: no request carries a prompt, so a real
# path answers 422 and lists what it rejected. Per field the server's own
# message decides:
#   - soul/standard and soul/v2/standard: is "4:5" an accepted aspect_ratio?
#     "720p" or "2K"? "batch_size" or "num_images"? (the model pages and
#     openapi.json disagree — this settles which one the server enforces);
#   - remove-background: 422 = the path exists, 404/405 = it does not;
#   - the presenter: our "generations" vs the documented wan/v2.7/image-to-video.
# Controls first: a documented path must answer 422, a made-up one 404/405.
# Anything ever accepted is cancelled at once and reported.
#
# --generate: one soul image (the payload the code sends, 3:4) and one
# background removal of it; prints status, size, transparency and credits.
#
# Paste the whole output back.
set -euo pipefail

PROJECT=super-system-500410
SECRET=HIGGSFIELD_AGENTS_KEY

HF_KEY=$(gcloud secrets versions access latest --secret "$SECRET" --project "$PROJECT")
if [ -z "$HF_KEY" ]; then
  echo "No $SECRET in Secret Manager — run scripts/set_higgsfield_agents_key.sh first."; exit 1
fi
export HF_KEY
export HF_GENERATE="${1:-}"

python3 - <<'PY'
import json, os, re, struct, time, urllib.request, urllib.error

BASE = "https://api.higgsfield.ai"
KEY = os.environ.pop("HF_KEY").strip()
GENERATE = os.environ.get("HF_GENERATE") == "--generate"
HEADERS = {"Authorization": f"Key {KEY}", "Content-Type": "application/json", "Accept": "application/json"}
SOUL = ["higgsfield-ai/soul/standard", "higgsfield-ai/soul/v2/standard"]


def clean(text, n=700):
    return re.sub(r"\s+", " ", (text or "").replace(KEY, "***"))[:n]


def call(method, path_or_url, body=None):
    url = path_or_url if path_or_url.startswith("https://") else f"{BASE}/{path_or_url}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def probe(path, body):
    code, text = call("POST", path, body)
    if code in (200, 201, 202):
        rid = ""
        try:
            rid = (json.loads(text) or {}).get("request_id") or ""
        except ValueError:
            pass
        c = call("POST", f"requests/{rid}/cancel", {})[0] if rid else "-"
        text += f"  !! ACCEPTED (request {rid or '?'}) — cancel answered {c}"
    return code, text


def mentions(text, word):
    """Does the 422 reject THIS field? By the error's `loc` when it is
    structured (an echoed `input` body would name every field); else text."""
    try:
        detail = (json.loads(text) or {}).get("detail")
    except (ValueError, AttributeError):
        detail = None
    if isinstance(detail, list):
        return any(word in [str(p) for p in (d.get("loc") or [])] for d in detail if isinstance(d, dict))
    return word in text


print("== Controls")
trusted = True
for path, ok in (("xai/grok-imagine-image-2.0", lambda c: c == 422),
                 ("uallak-probe/definitely-not-a-model", lambda c: c in (404, 405))):
    code, text = probe(path, {"aspect_ratio": "0:0"})
    trusted &= ok(code)
    print(f"{code:>4}  {path}  {'OK' if ok(code) else 'UNEXPECTED'}  {clean(text, 200)}")
if not trusted:
    print("!! The controls did not behave — do NOT trust what follows (a 401 = the key itself).")

print("\n== Post images: which fields does the server accept? (no prompt → never generates)")
for path in SOUL:
    print(f"\n-- {path}")
    code, text = probe(path, {})
    print(f"{code:>4}  empty body (only 'prompt' should be named)  {clean(text)}")
    for field, values in (("aspect_ratio", ["4:5", "3:4", "0:0"]), ("resolution", ["720p", "1080p", "2K"]),
                          ("batch_size", [1, 0]), ("num_images", [1, 0])):
        for v in values:
            code, text = probe(path, {field: v})
            verdict = ("REJECTED" if mentions(text, field) else "accepted (only prompt missing)") if code == 422 \
                else f"answered {code}"
            print(f"{code:>4}  {field}={v!r:8} → {verdict}  {clean(text, 220)}")
print("(An unknown field the server ignores also reads 'accepted' — compare value 0 with 1: only a field it")
print(" validates rejects 0.)")

print("\n== Background removal")
for path in ("remove-background", "v1/remove-background", "higgsfield-ai/remove-background"):
    code, text = probe(path, {})
    print(f"{code:>4}  {path}  → {'EXISTS' if code == 422 else 'not found' if code in (404, 405) else 'unclear'}  "
          f"{clean(text, 300)}")

print("\n== Presenter (talking clip)")
for path, body in (("generations", {"model": "wan2_7"}), ("wan/v2.7/image-to-video", {}),
                   ("wan/v2.7/reference-to-video", {})):
    code, text = probe(path, body)
    print(f"{code:>4}  {path}  → {'EXISTS' if code == 422 else 'not found' if code in (404, 405) else 'unclear'}  "
          f"{clean(text, 400)}")


def wait(rid):
    for _ in range(60):
        time.sleep(5)
        code, text = call("GET", f"requests/{rid}/status")
        try:
            data = json.loads(text)
        except ValueError:
            data = {}
        if data.get("status") in ("completed", "failed", "nsfw", "canceled", "cancelled"):
            return data
    return {"status": "timeout"}


def png_shape(url):
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            head = r.read(64)
            mime = r.headers.get("Content-Type", "")
    except Exception as e:
        return f"download failed: {type(e).__name__}"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", head[16:24])
        alpha = head[25] in (4, 6)
        return f"PNG {w}x{h}, {'HAS an alpha channel' if alpha else 'NO alpha channel'}"
    if head[:3] == b"\xff\xd8\xff":
        return f"JPEG ({mime}) — no transparency possible"
    return f"unknown format ({mime})"


if GENERATE:
    print("\n== --generate: ONE real post image + ONE background removal (credits)")
    body = {"prompt": "a ceramic coffee cup on a plain light grey background, studio light",
            "aspect_ratio": "3:4", "resolution": "720p", "batch_size": 1}
    code, text = call("POST", SOUL[0], body)
    print(f"{code:>4}  POST {SOUL[0]} {json.dumps({k: v for k, v in body.items() if k != 'prompt'})}  {clean(text, 300)}")
    rid = ""
    try:
        rid = (json.loads(text) or {}).get("request_id") or ""
    except ValueError:
        pass
    image_url = ""
    if rid:
        data = wait(rid)
        image_url = ((data.get("images") or [{}])[0] or {}).get("url") or ""
        print(f"      status {data.get('status')}  credits {data.get('credits') or data.get('cost') or '?'}  "
              f"{png_shape(image_url) if image_url else clean(json.dumps(data), 300)}")
    if image_url:
        code, text = call("POST", "remove-background", {"image_url": image_url, "media_type": "image"})
        print(f"{code:>4}  POST remove-background  {clean(text, 300)}")
        try:
            rid = (json.loads(text) or {}).get("request_id") or ""
        except ValueError:
            rid = ""
        if rid:
            data = wait(rid)
            out = ((data.get("images") or [{}])[0] or {}).get("url") or (data.get("image") or {}).get("url") or ""
            print(f"      status {data.get('status')}  credits {data.get('credits') or data.get('cost') or '?'}  "
                  f"{png_shape(out) if out else clean(json.dumps(data), 400)}")
else:
    print("\n(Free run only. Add --generate for one real image + one background removal.)")
print("\nPaste this whole output back.")
PY
