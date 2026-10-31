#!/usr/bin/env bash
# One-time setup for the operator agent (docs/OPERATOR.md). Run in Cloud Shell
# from the repo folder:
#
#   bash scripts/setup_operator.sh
#
# Safe to run again (every step is "create if missing"). It:
#   1. lets the RUNNING SERVICE read its own revisions + logs and move traffic
#      between revisions (Tier 1 reads, Tier 2 rollback) — nothing else;
#   2. creates the deployer service account the GitHub workflow deploys as,
#      trusted ONLY from .github/workflows/deploy.yml on master of this repo
#      (Workload Identity Federation — no key file exists anywhere);
#   3. optionally stores the operator's GitHub token in Secret Manager and
#      sets the Tier 3 env vars on the service (asks first; the token is read
#      hidden and never printed).
# It never reads, prints or changes any other secret, and never touches
# SECRETS_ENCRYPTION_KEY.
set -euo pipefail

PROJECT=super-system-500410
REGION=me-west1
SERVICE=super-system
REPO=johnny1603/Super-System-26
DEPLOYER=github-deployer
POOL=github
PROVIDER=github-actions
ROLE_ID=operatorRollback

gcloud config set project "$PROJECT" >/dev/null
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
DEPLOYER_EMAIL="$DEPLOYER@$PROJECT.iam.gserviceaccount.com"
RUNTIME_SA=$(gcloud run services describe "$SERVICE" --region "$REGION" \
  --format='value(spec.template.spec.serviceAccountName)')
RUNTIME_SA=${RUNTIME_SA:-"$PROJECT_NUMBER-compute@developer.gserviceaccount.com"}
BUILD_SA="$PROJECT_NUMBER-compute@developer.gserviceaccount.com"

echo "== APIs"
gcloud services enable iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com \
  run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  logging.googleapis.com secretmanager.googleapis.com >/dev/null

echo "== 1. The running service: read revisions + logs, move traffic (runtime SA: $RUNTIME_SA)"
PERMS=run.services.get,run.services.list,run.services.update,run.revisions.get,run.revisions.list,run.operations.get
if gcloud iam roles describe "$ROLE_ID" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud iam roles update "$ROLE_ID" --project "$PROJECT" --permissions "$PERMS" --quiet >/dev/null
else
  gcloud iam roles create "$ROLE_ID" --project "$PROJECT" --title "Operator: revisions + rollback" \
    --description "uallak operator agent: read revisions, move traffic (docs/OPERATOR.md)" \
    --permissions "$PERMS" >/dev/null
fi
gcloud projects add-iam-policy-binding "$PROJECT" --condition=None --quiet \
  --member "serviceAccount:$RUNTIME_SA" --role "projects/$PROJECT/roles/$ROLE_ID" >/dev/null
gcloud projects add-iam-policy-binding "$PROJECT" --condition=None --quiet \
  --member "serviceAccount:$RUNTIME_SA" --role roles/logging.viewer >/dev/null
# Updating the service's traffic requires acting as the account it runs as
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" --quiet \
  --member "serviceAccount:$RUNTIME_SA" --role roles/iam.serviceAccountUser >/dev/null

echo "== 2. The deployer service account: $DEPLOYER_EMAIL"
gcloud iam service-accounts describe "$DEPLOYER_EMAIL" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$DEPLOYER" --display-name "GitHub Actions deployer (operator)"
for ROLE in roles/run.developer roles/cloudbuild.builds.editor roles/artifactregistry.writer \
            roles/storage.objectAdmin roles/serviceusage.serviceUsageConsumer roles/logging.viewer; do
  gcloud projects add-iam-policy-binding "$PROJECT" --condition=None --quiet \
    --member "serviceAccount:$DEPLOYER_EMAIL" --role "$ROLE" >/dev/null
done
for SA in "$RUNTIME_SA" "$BUILD_SA"; do
  gcloud iam service-accounts add-iam-policy-binding "$SA" --quiet \
    --member "serviceAccount:$DEPLOYER_EMAIL" --role roles/iam.serviceAccountUser >/dev/null
done

gcloud iam workload-identity-pools describe "$POOL" --location global >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools create "$POOL" --location global --display-name "GitHub Actions"
CONDITION="assertion.repository=='$REPO' && assertion.ref=='refs/heads/master' && assertion.workflow_ref.startsWith('$REPO/.github/workflows/deploy.yml@')"
if gcloud iam workload-identity-pools providers describe "$PROVIDER" --location global \
     --workload-identity-pool "$POOL" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers update-oidc "$PROVIDER" --location global \
    --workload-identity-pool "$POOL" --attribute-condition "$CONDITION" >/dev/null
else
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" --location global \
    --workload-identity-pool "$POOL" --issuer-uri "https://token.actions.githubusercontent.com" \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref,attribute.workflow_ref=assertion.workflow_ref" \
    --attribute-condition "$CONDITION" >/dev/null
fi
gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER_EMAIL" --quiet \
  --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$POOL/attribute.repository/$REPO" >/dev/null

WIF="projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/$POOL/providers/$PROVIDER"

echo
read -r -p "== 3. Store the operator's GitHub token and turn on Tier 3 env vars now? [y/N] " ANSWER
if [[ "${ANSWER:-}" =~ ^[Yy]$ ]]; then
  read -r -s -p "Paste the fine-grained GitHub token (hidden): " TOKEN; echo
  read -r -p "Email for approval codes (your OWN mailbox, not the support one): " APPROVAL_EMAIL
  if gcloud secrets describe operator-github-token >/dev/null 2>&1; then
    printf %s "$TOKEN" | gcloud secrets versions add operator-github-token --data-file=- >/dev/null
  else
    printf %s "$TOKEN" | gcloud secrets create operator-github-token --replication-policy automatic \
      --data-file=- >/dev/null
  fi
  unset TOKEN
  gcloud secrets add-iam-policy-binding operator-github-token --quiet \
    --member "serviceAccount:$RUNTIME_SA" --role roles/secretmanager.secretAccessor >/dev/null
  gcloud run services update "$SERVICE" --region "$REGION" --quiet \
    --update-secrets "OPERATOR_GITHUB_TOKEN=operator-github-token:latest" \
    --update-env-vars "OPERATOR_APPROVAL_EMAIL=$APPROVAL_EMAIL,OPERATOR_CODE_ENABLED=1"
  echo "Stored. Tier 3 is ON for the next request."
fi

cat <<EOF

Done. Now in GitHub → the repo → Settings → Secrets and variables → Actions → Variables,
add these two REPOSITORY VARIABLES (not secrets — neither is secret):

  GCP_WIF_PROVIDER = $WIF
  GCP_DEPLOY_SA    = $DEPLOYER_EMAIL

Recommended: Settings → Environments → production → Required reviewers → add yourself.
EOF
