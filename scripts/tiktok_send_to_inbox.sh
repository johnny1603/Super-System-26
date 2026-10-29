#!/usr/bin/env bash
# Send one approved video from a client's Drive media folder to that client's
# TikTok inbox as a DRAFT (Upload-to-Inbox, scope video.upload). Used for the
# TikTok app-review demo (docs/TIKTOK-REVIEW.md, step 5). Run in Cloud Shell:
#   bash scripts/tiktok_send_to_inbox.sh CLIENT_ID DRIVE_FILE_ID
# Calls POST /api/tiktok-content/publish. Prints only the HTTP status and the
# result fields - never the admin key or a token.
set -u

if [ $# -ne 2 ]; then
  echo "Usage: bash scripts/tiktok_send_to_inbox.sh CLIENT_ID DRIVE_FILE_ID"
  exit 1
fi
CLIENT_ID="$1"
DRIVE_FILE_ID="$2"

PROJECT="super-system-500410"
SERVICE="super-system"
REGION="me-west1"
URL="https://app.uallak.com/api/tiktok-content/publish"

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

BODY=$(python3 -c 'import json, sys; print(json.dumps({"client_id": int(sys.argv[1]), "drive_file_id": sys.argv[2]}))' \
  "$CLIENT_ID" "$DRIVE_FILE_ID") || { echo "CLIENT_ID must be a number - nothing sent."; unset AK; exit 1; }

OUT=$(mktemp)
echo "Sending... (download from Drive + upload + waiting for TikTok can take up to ~2 minutes)"
STATUS=$(curl -s -o "$OUT" -w "%{http_code}" -X POST "$URL" \
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
    print(f"status: {data.get('status')}  publish_id: {data.get('publish_id')}")
    print("Open the TikTok app on the connected account -> inbox / drafts notification.")
else:
    print(f"error: {body.get('detail')}")
PY
rm -f "$OUT"
