#!/usr/bin/env bash
# One-time: move ADMIN_KEY on the super-system service from a plain env var
# into Secret Manager, and reference it from the service. Run in Cloud Shell
# from the repo folder:
#
#   bash scripts/move_admin_key_to_secret.sh
#
# - The VALUE does not change: the Cloud Scheduler jobs keep sending the same
#   X-Admin-Key, nothing else needs updating.
# - The key is never printed, logged or written to a file: it goes from the
#   service's config straight into `gcloud secrets ... --data-file=-` via a pipe.
# - The switch is ONE `gcloud run services update` (remove the plain var + add
#   the secret reference together), so no revision ever runs without ADMIN_KEY.
#   If that command fails, nothing on the service changed.
# - Safe to run again: if ADMIN_KEY is already a secret reference it says so
#   and stops.
#
# After this, the usual deploy command keeps the secret: `gcloud run deploy
# --source .` without --set-env-vars / --set-secrets / --clear-* keeps every
# env var and secret reference the service already has.
set -euo pipefail

PROJECT=super-system-500410
REGION=me-west1
SERVICE=super-system
SECRET=ADMIN_KEY

gcloud config set project "$PROJECT" >/dev/null

describe() {
  gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" --format=json
}

# kind of ADMIN_KEY on the service: plain | secret | none (never the value)
kind() {
  describe | python3 -c '
import json, sys
env = json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0].get("env", [])
e = next((x for x in env if x.get("name") == "ADMIN_KEY"), None)
print("none" if not e else "secret" if "valueFrom" in e else "plain")'
}

echo "== ADMIN_KEY on $SERVICE"
K=$(kind)
case "$K" in
  secret) echo "Already a Secret Manager reference - nothing to do."; exit 0 ;;
  none)   echo "ADMIN_KEY is not set on $SERVICE - nothing to move. Stopping."; exit 1 ;;
esac
echo "Plain env var - moving it."

RUNTIME_SA=$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" \
  --format='value(spec.template.spec.serviceAccountName)')
if [ -z "$RUNTIME_SA" ]; then
  RUNTIME_SA="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')-compute@developer.gserviceaccount.com"
fi

echo "== 1. Secret Manager"
gcloud services enable secretmanager.googleapis.com --project "$PROJECT" >/dev/null
if ! gcloud secrets describe "$SECRET" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud secrets create "$SECRET" --project "$PROJECT" --replication-policy automatic >/dev/null
  echo "Secret $SECRET created."
fi
# The value: service config -> pipe -> new secret version (never on screen or disk)
describe | python3 -c '
import json, sys
env = json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0].get("env", [])
v = next((x.get("value", "") for x in env if x.get("name") == "ADMIN_KEY"), "")
if not v:
    sys.exit(3)
sys.stdout.write(v)' | gcloud secrets versions add "$SECRET" --project "$PROJECT" --data-file=- >/dev/null
VERSION=$(gcloud secrets versions list "$SECRET" --project "$PROJECT" --filter='state=ENABLED' \
  --sort-by='~createTime' --limit=1 --format='value(name.basename())')
echo "Stored as $SECRET version $VERSION."

echo "== 2. Let the service read it ($RUNTIME_SA)"
gcloud secrets add-iam-policy-binding "$SECRET" --project "$PROJECT" --quiet \
  --member "serviceAccount:$RUNTIME_SA" --role roles/secretmanager.secretAccessor >/dev/null

echo "== 3. Switch the service to the secret (one update, one new revision)"
gcloud run services update "$SERVICE" --region "$REGION" --project "$PROJECT" --quiet \
  --remove-env-vars ADMIN_KEY \
  --update-secrets "ADMIN_KEY=$SECRET:$VERSION" >/dev/null

echo "== 4. Check"
K=$(kind)
if [ "$K" = "secret" ]; then
  echo "OK: ADMIN_KEY on $SERVICE now comes from Secret Manager ($SECRET:$VERSION); no plain value left."
  echo "The value is unchanged, so the Cloud Scheduler jobs keep working as they are."
  echo "Your usual deploy command keeps this reference - nothing to add to it."
else
  echo "Unexpected: ADMIN_KEY is '$K' on $SERVICE after the update. Check the service in the console."
  exit 1
fi
