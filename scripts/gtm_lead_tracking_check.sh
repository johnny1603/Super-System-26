#!/usr/bin/env bash
# Live check of the Tag Manager lead-tracking flow against the REAL GTM API
# (docs/GOOGLE-SCOPES.md, "Before recording"). Run in Cloud Shell:
#   bash scripts/gtm_lead_tracking_check.sh CLIENT_ID              # read only
#   bash scripts/gtm_lead_tracking_check.sh CLIENT_ID G-XXXXXXX apply
# Read only: GET /api/website/conversion-tracking (versions.live).
# apply: POST /api/website/configure-conversion - creates a workspace, the
# form-submission trigger and the GA4 generate_lead tag, a version, and
# PUBLISHES it - then reads the live version again to prove it is there.
# Prints only the HTTP status and result fields - never the admin key or a token.
set -u

if [ $# -lt 1 ] || [ $# -gt 3 ]; then
  echo "Usage: bash scripts/gtm_lead_tracking_check.sh CLIENT_ID [G-XXXXXXX apply]"
  exit 1
fi
CLIENT_ID="$1"
GA4_ID="${2:-}"
MODE="${3:-read}"
case "$CLIENT_ID" in ''|*[!0-9]*) echo "CLIENT_ID must be a number - nothing sent."; exit 1 ;; esac

PROJECT="super-system-500410"
SERVICE="super-system"
REGION="me-west1"
BASE="https://app.uallak.com/api/website"

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

show_status() {
  local status
  status=$(curl -s -o "$OUT" -w "%{http_code}" "$BASE/conversion-tracking?client_id=$CLIENT_ID" \
    -H "X-Admin-Key: $AK")
  echo "[$1] GET conversion-tracking -> HTTP $status"
  python3 - "$OUT" <<'PY'
import json, sys
try:
    body = json.load(open(sys.argv[1]))
except Exception:
    print("  no JSON in the response"); sys.exit()
d = body.get("data") or {}
if not d:
    print(f"  error: {body.get('detail')}"); sys.exit()
print(f"  gtm_api_connected: {d.get('gtm_api_connected')}  configured_live: {d.get('configured_live')}")
print(f"  live version: {d.get('live_version_name') or '-'}")
for t in d.get("lead_event_tags") or []:
    print(f"  lead tag: {t.get('tag_name')}  event: {t.get('event_name')}  "
          f"measurement: {t.get('measurement_id')}  triggers: {t.get('trigger_types')}")
for key in ("issue", "note"):
    if d.get(key):
        print(f"  {key}: {d[key]}")
PY
}

show_status before

if [ "$MODE" = "apply" ]; then
  case "$GA4_ID" in G-*) ;; *) echo "apply needs a GA4 id (G-...) - nothing changed."; rm -f "$OUT"; unset AK; exit 1 ;; esac
  read -rp "This PUBLISHES a trigger + tag in client $CLIENT_ID's real container. Type yes to go on: " OK
  if [ "$OK" != "yes" ]; then
    echo "Not applied - nothing changed."
  else
    BODY=$(python3 -c 'import json, sys; print(json.dumps({"client_id": int(sys.argv[1]), "ga4_measurement_id": sys.argv[2]}))' \
      "$CLIENT_ID" "$GA4_ID")
    STATUS=$(curl -s -o "$OUT" -w "%{http_code}" -X POST "$BASE/configure-conversion" \
      -H "X-Admin-Key: $AK" -H "Content-Type: application/json" -d "$BODY")
    echo "POST configure-conversion -> HTTP $STATUS"
    python3 - "$OUT" <<'PY'
import json, sys
try:
    body = json.load(open(sys.argv[1]))
except Exception:
    print("  no JSON in the response"); sys.exit()
d = body.get("data") or {}
if d:
    for key in ("event_name", "ga4_measurement_id", "tag_name", "trigger_name", "live_version", "live_version_id"):
        print(f"  {key}: {d.get(key)}")
else:
    print(f"  error: {body.get('detail')}")
PY
    show_status after
  fi
fi

unset AK
rm -f "$OUT"
