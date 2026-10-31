#!/usr/bin/env bash
# Prints the two GitHub repository VARIABLES the deploy workflow needs
# (docs/OPERATOR.md, "Variables"). Neither value is a secret. Read-only: run
# in Cloud Shell after scripts/setup_operator.sh, then paste each into
# GitHub -> Settings -> Secrets and variables -> Actions -> Variables.
set -eu

PROJECT=super-system-500410
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')

echo "Name:  GCP_WIF_PROVIDER"
echo "Value: projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/github-actions"
echo
echo "Name:  GCP_DEPLOY_SA"
echo "Value: github-deployer@$PROJECT.iam.gserviceaccount.com"
