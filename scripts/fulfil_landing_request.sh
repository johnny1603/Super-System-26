#!/usr/bin/env bash
# A team request that asked for a landing page before the chat could build one
# (request #6, client 27 - docs/CHAT_PARITY.md). Builds the DRAFT with the same
# builder as the dashboard button, in the chat thread the request came from, and
# marks the request handled by the system (kept, never deleted). Nothing goes
# live: the client publishes from the preview card with their own click.
# Run in Cloud Shell:
#   bash scripts/fulfil_landing_request.sh REQUEST_ID CLIENT_ID "PAGE TITLE" ["GOAL"]
# e.g. bash scripts/fulfil_landing_request.sh 6 27 "עיצוב אישי"
# First shows the request (dry run) and asks before building anything.
# Prints only the HTTP status and result fields - never the admin key.
set -u

[ $# -ge 3 ] || { echo "Usage: bash scripts/fulfil_landing_request.sh REQUEST_ID CLIENT_ID \"PAGE TITLE\" [\"GOAL\"]"; exit 1; }
REQUEST_ID="$1"
CLIENT_ID="$2"
TITLE="$3"
GOAL="${4:-}"
case "$REQUEST_ID$CLIENT_ID" in *[!0-9]*) echo "REQUEST_ID and CLIENT_ID must be numbers - nothing sent."; exit 1;; esac
BASE="https://app.uallak.com"
URL="$BASE/api/team-requests/$REQUEST_ID/fulfil-landing"

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
[ -n "$AK" ] || { echo "No ADMIN_KEY - nothing sent."; exit 1; }

body() {
  python3 -c 'import json, sys; print(json.dumps({"client_id": int(sys.argv[1]), "title": sys.argv[2],
    "goal": sys.argv[3], "dry_run": sys.argv[4] == "1"}, ensure_ascii=False))' "$CLIENT_ID" "$TITLE" "$GOAL" "$1"
}

send() {
  OUT=$(mktemp)
  STATUS=$(curl -s -o "$OUT" -w "%{http_code}" -X POST "$URL" --max-time 300 \
    -H "X-Admin-Key: $AK" -H "Content-Type: application/json; charset=utf-8" -d "$(body "$1")")
  echo "HTTP $STATUS"
  python3 - "$OUT" <<'PY'
import json, sys
try:
    body = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    print("No JSON in the response (see the HTTP status above).")
    sys.exit()
data = body.get("data") or {}
if data:
    for key in ("request_id", "topic", "request", "persona", "pages_used", "pages_included",
                "page_id", "status", "preview_card", "request_closed"):
        if data.get(key) not in (None, ""):
            print(f"{key}: {data[key]}")
else:
    print(f"refused: {body.get('detail')}")
PY
  rm -f "$OUT"
  [ "$STATUS" = "200" ]
}

echo "The request as stored (nothing changes yet):"
send 1 || { unset AK; exit 1; }
read -rp "Build the draft for it now? (y/N) " ANSWER
if [ "$ANSWER" = "y" ] || [ "$ANSWER" = "Y" ]; then
  echo "Building the draft... (the copy takes a minute or two)"
  send 0
else
  echo "Nothing built."
fi
unset AK
