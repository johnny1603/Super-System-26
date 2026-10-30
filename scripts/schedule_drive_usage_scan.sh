#!/usr/bin/env bash
# Creates (or updates) the DAILY Cloud Scheduler job for the Shared-Drive
# storage scan: GET /api/drive/usage-scan with the X-Admin-Key header, every
# day at 03:30 Asia/Jerusalem. Run in Cloud Shell from the repo root, AFTER
# deploying the version that has the route.
# The admin key is read from the Cloud Run service's env and never printed.
set -u

PROJECT="super-system-500410"
SERVICE="super-system"
REGION="me-west1"
JOB="drive-usage-scan"
URL="https://app.uallak.com/api/drive/usage-scan"
SCHEDULE="30 3 * * *"
TZ_NAME="Asia/Jerusalem"

# Put the job where the project's other scheduler jobs already live
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

if gcloud scheduler jobs describe "$JOB" --location="$LOCATION" --project="$PROJECT" >/dev/null 2>&1; then
  ACTION="update"
else
  ACTION="create"
fi

gcloud scheduler jobs "$ACTION" http "$JOB" \
  --project="$PROJECT" --location="$LOCATION" \
  --schedule="$SCHEDULE" --time-zone="$TZ_NAME" \
  --uri="$URL" --http-method=GET \
  --update-headers="X-Admin-Key=$AK" \
  --attempt-deadline=600s \
  --description="Daily Shared Drive storage scan (DRIVE_SHARED_ALERT_GB alert on status change)" \
  --format='value(name)' >/dev/null
RESULT=$?
unset AK

if [ "$RESULT" -ne 0 ]; then
  echo "gcloud scheduler jobs $ACTION failed (exit $RESULT) - see the error above."
  exit "$RESULT"
fi
echo "Job $JOB ${ACTION}d in $LOCATION: daily 03:30 $TZ_NAME -> $URL"
echo "Run it once now to check:  gcloud scheduler jobs run $JOB --location=$LOCATION --project=$PROJECT"
