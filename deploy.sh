#!/usr/bin/env bash
# Deploy Rank2 to Google Cloud Run
# Usage:
#   bash deploy.sh            Ship to production: dispatches the GitHub Actions
#                             deploy of whatever is on origin/main and watches it.
#                             (Pushing to main does NOT deploy — this does.)
#   bash deploy.sh --local    Build + deploy directly from this machine via
#                             Cloud Build (needs gcloud login; no Docker).
#   bash deploy.sh setup      One-time GCP project setup (APIs, registry, bucket).
#   bash scripts/deploy_status.sh   Show what's live vs. what's pending.
#
# This script is the single source of truth for the Cloud Run flags: the
# GitHub Actions workflow (.github/workflows/deploy.yml) runs `bash deploy.sh`
# with CI=true after authenticating via Workload Identity Federation. Do not
# duplicate the flags anywhere else — edit them here.
#
# Env overrides:
#   PROJECT_ID   GCP project (defaults to the active gcloud config project)
set -euo pipefail

# ── Ship via GitHub Actions (default when run from a laptop) ─────────────────
if [[ -z "${CI:-}" && "${1:-}" != "--local" && "${1:-}" != "setup" ]]; then
  command -v gh >/dev/null || { echo "ERROR: gh CLI not installed (brew install gh; gh auth login)"; exit 1; }
  git fetch -q origin main
  if [[ -n "$(git status --porcelain)" ]]; then
    echo "WARNING: you have uncommitted changes — they will NOT be deployed (only origin/main ships)."
  fi
  UNPUSHED=$(git log --oneline origin/main..main | wc -l | tr -d ' ')
  if [[ "$UNPUSHED" != "0" ]]; then
    echo "ERROR: $UNPUSHED local commit(s) on main are not pushed. Run: git push origin main"
    exit 1
  fi
  SHIP_SHA=$(git rev-parse --short origin/main)
  echo ""
  echo "  Shipping origin/main @ $SHIP_SHA to production via GitHub Actions..."
  echo ""
  gh workflow run "Deploy to Cloud Run" --ref main
  sleep 5
  RUN_ID=$(gh run list --workflow=deploy.yml --event=workflow_dispatch --limit 1 --json databaseId -q '.[0].databaseId')
  gh run watch "$RUN_ID" --exit-status
  echo ""
  bash "$(dirname "$0")/scripts/deploy_status.sh"
  exit 0
fi

# ── Config ────────────────────────────────────────────────────────────────────
REGION="us-central1"
SERVICE="rank2"

# ── SSO / Email config (edit these before deploying) ─────────────────────────
GOOGLE_CLIENT_ID="883710187036-ruiomm1h40c6947uo8qn06su95fci1uq.apps.googleusercontent.com"
RESEND_FROM_DOMAIN="careclimb.com"
APP_URL="https://careclimb.com"
# ─────────────────────────────────────────────────────────────────────────────

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || true)}"
if [[ -z "$PROJECT_ID" ]]; then
  echo "ERROR: No GCP project set. Run: gcloud config set project YOUR_PROJECT_ID (or export PROJECT_ID)"
  exit 1
fi

BUCKET="${PROJECT_ID}-rank2-data"
REPO="rank2"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/app"
GIT_SHA="$(git rev-parse --short HEAD 2>/dev/null || echo dev)"
IMAGE_TAG="${IMAGE}:${GIT_SHA}"

echo ""
echo "  Project : $PROJECT_ID"
echo "  Region  : $REGION"
echo "  Image   : $IMAGE_TAG"
echo "  Bucket  : gs://$BUCKET"
echo ""

# ── One-time setup ────────────────────────────────────────────────────────────
if [[ "${1:-}" == "setup" ]]; then
  echo "==> Enabling APIs..."
  gcloud services enable \
    run.googleapis.com \
    artifactregistry.googleapis.com \
    storage.googleapis.com \
    cloudbuild.googleapis.com \
    secretmanager.googleapis.com \
    --quiet

  echo "==> Creating Artifact Registry repository..."
  gcloud artifacts repositories create "$REPO" \
    --repository-format=docker \
    --location="$REGION" \
    --quiet 2>/dev/null || echo "    (already exists)"

  echo "==> Creating GCS bucket for persistent data..."
  gcloud storage buckets create "gs://${BUCKET}" \
    --location="$REGION" \
    --quiet 2>/dev/null || echo "    (already exists)"

  echo "==> Granting Cloud Run service account access to bucket..."
  PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
  SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
  gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
    --member="serviceAccount:${SA}" \
    --role="roles/storage.objectAdmin" \
    --quiet

  echo ""
  echo "Setup complete. Now add your secrets:"
  echo ""
  echo "  gcloud secrets create ANTHROPIC_API_KEY     --data-file=- <<< 'your-anthropic-key'"
  echo "  gcloud secrets create GOOGLE_PLACES_API_KEY --data-file=- <<< 'your-places-key'"
  echo "  gcloud secrets create ACCESS_PASSWORD        --data-file=- <<< 'your-access-password'"
  echo "  gcloud secrets create GOOGLE_CLIENT_SECRET   --data-file=- <<< 'your-google-client-secret'"
  echo "  gcloud secrets create RESEND_API_KEY         --data-file=- <<< 'your-resend-api-key'"
  echo ""
  echo "Then re-run without 'setup' to build and deploy:"
  echo "  bash deploy.sh"
  exit 0
fi

# ── Build & push ──────────────────────────────────────────────────────────────
echo "==> Writing build version ($GIT_SHA)..."
echo "$GIT_SHA" > VERSION

echo "==> Building and pushing image via Cloud Build..."
if [[ -n "${CI:-}" ]]; then
  # In CI the deploy SA can't stream build logs (that needs project Viewer), and
  # gcloud refuses to wait synchronously without it. Submit async and poll.
  BUILD_ID=$(gcloud builds submit --tag "$IMAGE_TAG" --project "$PROJECT_ID" \
               --quiet --async --format='value(id)' .)
  echo "    Build $BUILD_ID submitted — https://console.cloud.google.com/cloud-build/builds/${BUILD_ID}?project=${PROJECT_ID}"
  while :; do
    STATUS=$(gcloud builds describe "$BUILD_ID" --project "$PROJECT_ID" --format='value(status)')
    case "$STATUS" in
      SUCCESS) echo "    Build succeeded."; break ;;
      QUEUED|PENDING|WORKING) sleep 15 ;;
      *) echo "ERROR: Cloud Build finished with status $STATUS"; exit 1 ;;
    esac
  done
else
  gcloud builds submit --tag "$IMAGE_TAG" --project "$PROJECT_ID" --quiet .
fi

# ── Deploy ────────────────────────────────────────────────────────────────────
echo "==> Deploying to Cloud Run..."
gcloud run deploy "$SERVICE" \
  --image="$IMAGE_TAG" \
  --region="$REGION" \
  --project="$PROJECT_ID" \
  --quiet \
  --platform=managed \
  --allow-unauthenticated \
  --memory=2Gi \
  --cpu=1 \
  --timeout=3600 \
  --min-instances=1 \
  --max-instances=10 \
  --session-affinity \
  --no-cpu-throttling \
  --set-env-vars="REPORTS_DIR=/data/reports,APP_URL=${APP_URL},RESEND_FROM_DOMAIN=${RESEND_FROM_DOMAIN},GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}" \
  --set-secrets="ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,GOOGLE_PLACES_API_KEY=GOOGLE_PLACES_API_KEY:latest,ACCESS_PASSWORD=ACCESS_PASSWORD:latest,GOOGLE_CLIENT_SECRET=GOOGLE_CLIENT_SECRET:latest,RESEND_API_KEY=RESEND_API_KEY:latest,DATABASE_URL=rank2-db-url:latest" \
  --add-volume="name=rank2-data,type=cloud-storage,bucket=${BUCKET}" \
  --add-volume-mount="volume=rank2-data,mount-path=/data"
# NOTE: The /data volume now holds only generated reports (REPORTS_DIR); the
# database moved off GCS FUSE to Postgres (Neon) via the DATABASE_URL secret.
# Once this revision is verified, max-instances can be raised past 2 safely —
# Postgres supports many concurrent Cloud Run instances (DuckDB did not).

echo ""
echo "Deploy complete!"
gcloud run services describe "$SERVICE" --region="$REGION" --project="$PROJECT_ID" --format="value(status.url)"
