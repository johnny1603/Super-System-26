#!/usr/bin/env bash
# Live test of the creative tools on the INTERNAL client 27 (docs/CREATIVE-TOOLS.md).
# The client's keys are pasted ONLY in the dashboard cards - never here, never in chat.
# Run in Cloud Shell:
#   bash scripts/creative_tools_live_test.sh voice CLIENT_ID
#       clone the voice from the client's uploaded recordings (needs the
#       voice-cloning consent on the ElevenLabs card)
#   bash scripts/creative_tools_live_test.sh video CLIENT_ID recording FILE_ID
#       avatar video with an uploaded recording as the audio track
#   bash scripts/creative_tools_live_test.sh video CLIENT_ID clone "script text"
#       avatar video with the script read in the cloned voice
# Prints only the HTTP status and the result fields - never the admin key or a vendor key.
set -u

usage() {
  echo "Usage: bash scripts/creative_tools_live_test.sh voice CLIENT_ID"
  echo "       bash scripts/creative_tools_live_test.sh video CLIENT_ID recording FILE_ID"
  echo "       bash scripts/creative_tools_live_test.sh video CLIENT_ID clone \"script text\""
  exit 1
}

[ $# -ge 2 ] || usage
MODE="$1"
CLIENT_ID="$2"
BASE="https://app.uallak.com"

if [ "$MODE" = "voice" ]; then
  URL="$BASE/api/avatar/create-voice"
  BODY=$(python3 -c 'import json, sys; print(json.dumps({"client_id": int(sys.argv[1])}))' "$CLIENT_ID") \
    || { echo "CLIENT_ID must be a number - nothing sent."; exit 1; }
elif [ "$MODE" = "video" ] && [ $# -eq 4 ]; then
  URL="$BASE/api/avatar/generate-video"
  BODY=$(python3 -c '
import json, sys
cid, kind, value = int(sys.argv[1]), sys.argv[2], sys.argv[3]
if kind == "recording":
    body = {"client_id": cid, "voice_kind": "recording", "voice_file_id": value}
elif kind == "clone":
    body = {"client_id": cid, "voice_kind": "clone", "script_text": value}
else:
    sys.exit(1)
print(json.dumps(body, ensure_ascii=False))
' "$CLIENT_ID" "$3" "$4") || usage
else
  usage
fi

PROJECT="super-system-500410"
SERVICE="super-system"
REGION="me-west1"
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
  echo "No ADMIN_KEY - nothing sent."
  exit 1
fi

OUT=$(mktemp)
echo "Sending... (a video render can take up to ~15 minutes)"
STATUS=$(curl -s -o "$OUT" -w "%{http_code}" -X POST "$URL" --max-time 1000 \
  -H "X-Admin-Key: $AK" -H "Content-Type: application/json" -d "$BODY")
unset AK

echo "HTTP $STATUS"
python3 - "$OUT" <<'PY'
import json, sys
try:
    body = json.load(open(sys.argv[1]))
except Exception:
    print("No JSON in the response (see the HTTP status above).")
    sys.exit()
data = body.get("data") or {}
if data:
    for key in ("voice_id", "provider", "voice_kind", "duration_seconds", "link", "minutes_used", "minutes_cap"):
        if data.get(key) not in (None, ""):
            print(f"{key}: {data[key]}")
else:
    print(f"refused: {body.get('detail')}")
PY
rm -f "$OUT"
