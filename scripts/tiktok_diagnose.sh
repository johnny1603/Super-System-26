#!/usr/bin/env bash
# READ-ONLY: the last TikTok "send to drafts" attempts of one client — which
# Drive file each card held (name, size, type, Drive's video metadata), what
# the file is (container, video/audio codec, resolution, fps, length), the
# publish_id, and TikTok's LIVE status + fail_reason for it. Run in Cloud Shell:
#   bash scripts/tiktok_diagnose.sh CLIENT_ID [LIMIT]      (LIMIT default 2, max 5)
# Calls GET /api/admin/tiktok/diagnose. Never prints the admin key or a token.
set -u

if [ $# -lt 1 ] || [ $# -gt 2 ]; then
  echo "Usage: bash scripts/tiktok_diagnose.sh CLIENT_ID [LIMIT]"
  exit 1
fi
CLIENT_ID="$1"
LIMIT="${2:-2}"
case "$CLIENT_ID$LIMIT" in
  *[!0-9]*) echo "CLIENT_ID and LIMIT must be numbers."; exit 1 ;;
esac

PROJECT="super-system-500410"
SERVICE="super-system"
REGION="me-west1"
URL="https://app.uallak.com/api/admin/tiktok/diagnose?client_id=${CLIENT_ID}&limit=${LIMIT}"

AK=$(gcloud run services describe "$SERVICE" --region "$REGION" \
  --project="$PROJECT" --format=json 2>/dev/null | python3 -c '
import json, sys
try:
    spec = json.load(sys.stdin)["spec"]["template"]["spec"]
    env = spec["containers"][0].get("env", [])
except Exception:
    env = []
print(next((e.get("value", "") for e in env if e.get("name") == "ADMIN_KEY"), ""))
')

if [ -z "$AK" ]; then
  read -rsp "ADMIN_KEY not in Cloud Run env. Paste it (hidden), then Enter: " AK
  echo
fi
if [ -z "$AK" ]; then
  echo "No ADMIN_KEY - nothing read."
  exit 1
fi

OUT=$(mktemp)
echo "Reading... (each file is read from Drive to check its format; can take a minute)"
STATUS=$(curl -s -o "$OUT" -w "%{http_code}" -H "X-Admin-Key: $AK" "$URL")
unset AK

echo "HTTP $STATUS"
python3 - "$OUT" <<'PY'
import json, sys
try:
    body = json.load(open(sys.argv[1]))
except Exception:
    print("No JSON in the response (see the HTTP status above).")
    sys.exit()
data = body.get("data")
if not data:
    print(f"error: {body.get('detail')}")
    sys.exit()
for a in data.get("attempts") or []:
    f = a.get("file") or {}
    v = f.get("video") or {}
    vm = f.get("videoMediaMetadata") or {}
    t = a.get("tiktok_status") or {}
    print("-" * 70)
    print(f"proposal {a.get('proposal_id')}  card status: {a.get('status')} ({a.get('outcome')})")
    print(f"  created {a.get('created_at')}  approved {a.get('decided_at')}  finished {a.get('finished_at')}")
    print(f"  drive_file_id {a.get('drive_file_id')}")
    print(f"  file: {f.get('name')}  {f.get('size')} bytes  {f.get('mimeType')}  modified {f.get('modifiedTime')}")
    print(f"  drive video meta: {vm.get('width')}x{vm.get('height')}  {vm.get('durationMillis')} ms")
    if v:
        print(f"  probe: container={v.get('container')} video={v.get('video_codec')} audio={v.get('audio_codec')} "
              f"{v.get('width')}x{v.get('height')} fps={v.get('fps')} duration={v.get('duration_s')}s")
        print(f"  TikTok format problems: {f.get('tiktok_problems') or 'none'}  notes: {f.get('video_notes') or '-'}")
        warn = f.get("tiktok_warnings") or []
        if "codec_not_h264" in warn:
            print(f"  TikTok format WARNING: video codec is {v.get('video_codec')}, not H.264 (TikTok's recommended "
                  f"codec) - TikTok may reject it at processing after a successful upload")
        elif warn:
            print(f"  TikTok format warnings: {warn}")
    if f.get("error"):
        print(f"  file read error: {f.get('error')}")
    j = a.get("job") or {}
    if j:
        print(f"  upload job {j.get('id', '-')}: status={j.get('status')} outcome={j.get('outcome', '-')} "
              f"size={j.get('size')} chunk_size={j.get('chunk_size', '-')} chunks={j.get('chunks', '-')} "
              f"sent={j.get('next', 0)} attempts={j.get('attempts', 0)} restarts={j.get('restarts', 0)}")
        if j.get("warnings"):
            print(f"  job warnings: {j.get('warnings')}")
        if j.get("last_error_message") or j.get("last_error"):
            print(f"  LAST TikTok ERROR at step '{j.get('last_error_step', '-')}' ({j.get('last_error_at', '-')}):")
            print(f"    message: {j.get('last_error_message') or j.get('last_error')}")
            print(f"    code: {j.get('last_error_code') or '-'}   log_id: {j.get('last_error_log_id') or '-'}")
    print(f"  publish_id: {a.get('publish_id') or '-'}")
    if not a.get("publish_id") and j.get("last_error_step") == "init":
        print("  (no publish_id: TikTok refused the upload at init - see the LAST TikTok ERROR above)")
    print(f"  TikTok status: {json.dumps(t, ensure_ascii=False)}")
print("-" * 70)
print(f"last two attempts used the SAME file: {data.get('last_two_same_file')}")
PY
rm -f "$OUT"
