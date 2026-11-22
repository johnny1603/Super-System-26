#!/usr/bin/env bash
# READ-ONLY: does Higgsfield accept this client's stored key, sent the way the
# generator sends it now (Authorization: Key ...)? Prints the key's SHAPE only
# (lengths, whether it has the "id:secret" form) — never any part of it — and
# the result of one free authenticated read (no credits). Run in Cloud Shell:
#   bash scripts/higgsfield_check.sh CLIENT_ID
# Calls GET /api/admin/higgsfield/check. Never prints the admin key or the key.
set -u

if [ $# -ne 1 ]; then
  echo "Usage: bash scripts/higgsfield_check.sh CLIENT_ID"
  exit 1
fi
CLIENT_ID="$1"
case "$CLIENT_ID" in
  *[!0-9]*|"") echo "CLIENT_ID must be a number."; exit 1 ;;
esac

PROJECT="super-system-500410"
SERVICE="super-system"
REGION="me-west1"
URL="https://app.uallak.com/api/admin/higgsfield/check?client_id=${CLIENT_ID}"

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
d = body.get("data")
if not d:
    print(f"error: {body.get('detail')}")
    sys.exit()
print(f"tile status: {d.get('tile_status')}")
if not d.get("stored"):
    print("No Higgsfield key stored for this client.")
    sys.exit()
s = d.get("shape") or {}
print(f"stored key: length {s.get('length')}, after cleaning {s.get('clean_length')}"
      f"{' (cleaning changed it)' if s.get('changed_by_cleaning') else ''}")
both = d.get("both_parts", s.get("has_id_and_secret"))
lengths = s.get("part_lengths") or []
print(f"  Key ID present: {bool(lengths and lengths[0])}   Key Secret present: {bool(both)}"
      f"   part lengths: {lengths}")
print(f"sent as: Authorization: {d.get('auth_scheme')} <id:secret>   to {d.get('api_base')}")
p = d.get("probe")
print(f"Higgsfield says: {p}")
if p == "ok":
    print("  -> accepted. Generation should work now.")
elif p == "not_sent_single_part":
    print("  -> an OLD single-part value (no Key ID + Key Secret pair). Nothing was sent to Higgsfield.")
    print("     The card shows 'needs reconnect': the client enters the two values in the two boxes.")
elif p == "rejected":
    print("  -> refused although both parts are stored: the key was revoked/deleted in Higgsfield,")
    print("     or belongs to an account without API access. The client enters a new pair in the card.")
else:
    print("  -> Higgsfield did not answer clearly (network/5xx). Run again in a minute.")
PY
rm -f "$OUT"
