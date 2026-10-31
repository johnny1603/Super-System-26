#!/usr/bin/env bash
# Creates (or updates) the Cloud Scheduler jobs for the daily chat rollover
# (core/chat_rollover): GET /api/chat/rollover with the X-Admin-Key header.
#   chat-rollover          08:00 Asia/Jerusalem every day (Cloud Scheduler
#                          follows the zone's DST itself)
#   chat-rollover-catchup  every 15 minutes 08:10-22:55, for windows deferred
#                          because the client was active in the last 30 minutes
# Both hit the same idempotent route (one summary per chat per day), so an
# extra or repeated run changes nothing. Run in Cloud Shell from the repo root,
# AFTER deploying the version that has the route and running
# migrations/2026-11-01-chat-rollover.sql.
# The admin key is read from the Cloud Run service's env and never printed.
set -u

PROJECT="super-system-500410"
SERVICE="super-system"
REGION="me-west1"
URL="https://app.uallak.com/api/chat/rollover"
TZ_NAME="Asia/Jerusalem"

LOCATION="${SCHEDULER_LOCATION:-}"
if [ -z "$LOCATION" ]; then
  for candidate in me-west1 europe-west1 us-central1; do
    if [ -n "$(gcloud scheduler jobs list --location="$candidate" --project="$PROJECT" \
                 --format='value(name)' 2>/dev/null | head -n1)" ]; then
      LOCATION="$candidate"; break
    fi
  done
fi
LOCATION="${LOCATION:-me-west1}"

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
  echo "ADMIN_KEY is not in the Cloud Run env of $SERVICE - nothing created."
  exit 1
fi

upsert() {
  local job="$1" schedule="$2" description="$3" action
  if gcloud scheduler jobs describe "$job" --location="$LOCATION" --project="$PROJECT" >/dev/null 2>&1; then
    action="update"
  else
    action="create"
  fi
  gcloud scheduler jobs "$action" http "$job" \
    --project="$PROJECT" --location="$LOCATION" \
    --schedule="$schedule" --time-zone="$TZ_NAME" \
    --uri="$URL" --http-method=GET \
    --update-headers="X-Admin-Key=$AK" \
    --attempt-deadline=900s \
    --description="$description" \
    --format='value(name)' >/dev/null
  local result=$?
  if [ "$result" -ne 0 ]; then
    echo "gcloud scheduler jobs $action $job failed (exit $result) - see the error above."
    return "$result"
  fi
  echo "Job $job ${action}d in $LOCATION: $schedule $TZ_NAME -> $URL"
}

upsert chat-rollover "0 8 * * *" "Daily chat rollover at 08:00: summary + fresh window, nothing deleted"
FIRST=$?
upsert chat-rollover-catchup "10-59/15 8-22 * * *" "Chat rollover catch-up for windows deferred while the client was active"
SECOND=$?
unset AK

if [ "$FIRST" -ne 0 ] || [ "$SECOND" -ne 0 ]; then
  exit 1
fi
echo "Run it once now to check:  gcloud scheduler jobs run chat-rollover --location=$LOCATION --project=$PROJECT"
