#!/usr/bin/env bash
# One-time Merchant Center cleanup (run in Cloud Shell from the repo root).
# Calls POST /api/admin/merchant-center/retire, which revokes every stored
# Merchant Center grant at Google and deletes the rows. Safe to re-run.
# Prints only the HTTP status and counts - never the admin key.
set -u

PROJECT="super-system-500410"
SERVICE="super-system"
REGION="me-west1"
URL="https://app.uallak.com/api/admin/merchant-center/retire"

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
STATUS=$(curl -s -o "$OUT" -w "%{http_code}" -X POST "$URL" -H "X-Admin-Key: $AK")
unset AK

echo "HTTP $STATUS"
python3 - "$OUT" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1]))["data"]
    rows = list(data["clients"].values())
    removed = sum(1 for r in rows if r.get("removed"))
    errors = sum(1 for r in rows if "error" in r)
    print(f"clients: {data['count']}  removed: {removed}  errors: {errors}")
except Exception:
    print("No counts in the response (see the HTTP status above).")
PY
rm -f "$OUT"
