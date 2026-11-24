#!/usr/bin/env bash
# One-time: give the service uallak's OWN Higgsfield key, for the agents'
# characters (core/agent_characters). The agents' character generations, the
# reference-image uploads and the background removal run on THAT key — never
# on a client's.
#
#   bash scripts/set_higgsfield_agents_key.sh
#
# - Asks for the Key ID and the Key Secret with hidden input (nothing on
#   screen, nothing in shell history, nothing written to disk); they go
#   straight into Secret Manager through a pipe as one "id:secret" value.
# - The service reads it as HIGGSFIELD_AGENTS_KEY from Secret Manager (one
#   `gcloud run services update`, one new revision).
# - Run it again to rotate the key: a new secret version, the service moved to it.
# - Your usual deploy command keeps the reference — nothing to add to it.
set -euo pipefail

PROJECT=super-system-500410
REGION=me-west1
SERVICE=super-system
SECRET=HIGGSFIELD_AGENTS_KEY

gcloud config set project "$PROJECT" >/dev/null

read -r -s -p "Higgsfield Key ID (uallak's account): " KEY_ID; echo
read -r -s -p "Higgsfield Key Secret: " KEY_SECRET; echo
KEY_ID=$(printf '%s' "$KEY_ID" | tr -d '[:space:]')
KEY_SECRET=$(printf '%s' "$KEY_SECRET" | tr -d '[:space:]')
if [ -z "$KEY_ID" ] || [ -z "$KEY_SECRET" ]; then
  echo "Both parts are needed. Nothing changed."; exit 1
fi

echo "== 1. Check the key with Higgsfield (the documented upload-URL call: free, uploads nothing)"
CODE=$(printf 'Authorization: Key %s:%s\nContent-Type: application/json\n' "$KEY_ID" "$KEY_SECRET" | \
  curl -s -o /dev/null -w '%{http_code}' -H @- -X POST --data '{"content_type":"image/png"}' \
  "https://api.higgsfield.ai/files/generate-upload-url" || true)
case "$CODE" in
  401|403) echo "Higgsfield refused this key ($CODE). Nothing changed."; exit 1 ;;
  200|201) echo "Key accepted." ;;
  *)       echo "Higgsfield answered $CODE. Storing anyway." ;;
esac

RUNTIME_SA=$(gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" \
  --format='value(spec.template.spec.serviceAccountName)')
if [ -z "$RUNTIME_SA" ]; then
  RUNTIME_SA="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')-compute@developer.gserviceaccount.com"
fi

echo "== 2. Secret Manager"
gcloud services enable secretmanager.googleapis.com --project "$PROJECT" >/dev/null
if ! gcloud secrets describe "$SECRET" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud secrets create "$SECRET" --project "$PROJECT" --replication-policy automatic >/dev/null
  echo "Secret $SECRET created."
fi
printf '%s:%s' "$KEY_ID" "$KEY_SECRET" | gcloud secrets versions add "$SECRET" --project "$PROJECT" --data-file=- >/dev/null
unset KEY_ID KEY_SECRET
VERSION=$(gcloud secrets versions list "$SECRET" --project "$PROJECT" --filter='state=ENABLED' \
  --sort-by='~createTime' --limit=1 --format='value(name.basename())')
echo "Stored as $SECRET version $VERSION."

echo "== 3. Let the service read it ($RUNTIME_SA)"
gcloud secrets add-iam-policy-binding "$SECRET" --project "$PROJECT" --quiet \
  --member "serviceAccount:$RUNTIME_SA" --role roles/secretmanager.secretAccessor >/dev/null

echo "== 4. Point the service at it (one new revision)"
gcloud run services update "$SERVICE" --region "$REGION" --project "$PROJECT" --quiet \
  --update-secrets "$SECRET=$SECRET:$VERSION" >/dev/null

echo "OK: $SERVICE reads $SECRET from Secret Manager ($SECRET:$VERSION)."
echo "Next: open /admin/characters/ and set each agent's reference image."
