#!/usr/bin/env bash
# One-time GCP setup so GitHub Actions can deploy to Cloud Run WITHOUT a
# service-account key (Workload Identity Federation).
#
# Run locally, once, as a project owner:
#   bash scripts/setup_github_deploy.sh
#
# Idempotent — safe to re-run. Creates/ensures:
#   • service account  github-deploy@PROJECT.iam.gserviceaccount.com
#   • IAM roles on it needed for Cloud Build + Cloud Run deploy
#   • WIF pool "github" + OIDC provider "github" restricted to this repo
#   • binding letting the repo's workflows impersonate the service account
#
# The workflow (.github/workflows/deploy.yml) references these by name, so no
# GitHub secrets or variables are required.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
REPO_SLUG="${REPO_SLUG:-$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || echo tyjallen44/Rank2)}"
SA_NAME="github-deploy"
POOL="github"
PROVIDER="github"

[[ -n "$PROJECT_ID" ]] || { echo "ERROR: no GCP project. gcloud config set project ..."; exit 1; }
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo ""
echo "  Project : $PROJECT_ID ($PROJECT_NUMBER)"
echo "  Repo    : $REPO_SLUG"
echo "  SA      : $SA_EMAIL"
echo ""

echo "==> Enabling APIs..."
gcloud services enable iamcredentials.googleapis.com sts.googleapis.com \
  cloudbuild.googleapis.com run.googleapis.com artifactregistry.googleapis.com \
  --project="$PROJECT_ID" --quiet

echo "==> Service account..."
if gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" >/dev/null 2>&1; then
  echo "    (already exists)"
else
  gcloud iam service-accounts create "$SA_NAME" \
    --display-name="GitHub Actions deploy" --project="$PROJECT_ID" --quiet
  # IAM is eventually consistent — wait until the new SA is visible before binding roles.
  for i in $(seq 1 30); do
    gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" >/dev/null 2>&1 && break
    sleep 2
  done
fi

echo "==> Project roles for the deploy SA..."
for ROLE in \
  roles/run.admin \
  roles/iam.serviceAccountUser \
  roles/cloudbuild.builds.editor \
  roles/artifactregistry.writer \
  roles/storage.admin \
  roles/serviceusage.serviceUsageConsumer
do
  for i in $(seq 1 5); do
    if gcloud projects add-iam-policy-binding "$PROJECT_ID" \
         --member="serviceAccount:${SA_EMAIL}" --role="$ROLE" \
         --condition=None --quiet >/dev/null 2>&1; then
      echo "    $ROLE"; break
    fi
    [[ $i -eq 5 ]] && { echo "ERROR: could not bind $ROLE"; exit 1; }
    sleep 5
  done
done

echo "==> Workload Identity pool..."
gcloud iam workload-identity-pools create "$POOL" \
  --location=global --display-name="GitHub Actions" --project="$PROJECT_ID" --quiet 2>/dev/null \
  || echo "    (already exists)"

echo "==> OIDC provider (restricted to $REPO_SLUG)..."
gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" \
  --location=global --workload-identity-pool="$POOL" --project="$PROJECT_ID" \
  --display-name="GitHub" \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
  --attribute-condition="assertion.repository == '${REPO_SLUG}'" \
  --quiet 2>/dev/null \
  || echo "    (already exists)"

echo "==> Allow the repo's workflows to impersonate the SA..."
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
  --project="$PROJECT_ID" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${REPO_SLUG}" \
  --quiet >/dev/null

echo ""
echo "Done. Workflow values (already hard-coded in .github/workflows/deploy.yml):"
echo "  workload_identity_provider: projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"
echo "  service_account:            ${SA_EMAIL}"
echo ""
echo "Trigger a deploy now with:  gh workflow run 'Deploy to Cloud Run' && gh run watch"
