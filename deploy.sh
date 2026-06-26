#!/usr/bin/env bash
# Deploy Rank2 to Google Cloud Run
# Prerequisites:
#   1. gcloud CLI installed  →  brew install --cask google-cloud-sdk
#   2. Logged in             →  gcloud auth login
#   3. Docker running        →  open Docker Desktop
#
# First-time run:  bash deploy.sh setup
# Redeploy only:   bash deploy.sh
set -e

# ── Config ────────────────────────────────────────────────────────────────────
REGION="us-central1"
SERVICE="rank2"
# ─────────────────────────────────────────────────────────────────────────────

PROJECT_ID=$(gcloud config get-value project 2>/dev/null)
if [[ -z "$PROJECT_ID" ]]; then
  echo "ERROR: No GCP project set. Run: gcloud config set project YOUR_PROJECT_ID"
  exit 1
fi

BUCKET="${PROJECT_ID}-rank2-data"
REPO="rank2"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/app"

echo ""
echo "  Project : $PROJECT_ID"
echo "  Region  : $REGION"
echo "  Image   : $IMAGE"
echo "  Bucket  : gs://$BUCKET"
echo ""

# ── One-time setup ────────────────────────────────────────────────────────────
if [[ "$1" == "setup" ]]; then
  echo "==> Enabling APIs..."
  gcloud services enable \
    run.googleapis.com \
    artifactregistry.googleapis.com \
    storage.googleapis.com \
    cloudbuild.googleapis.com \
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
  echo "  gcloud secrets create ACCESS_PASSWORD        --data-file=- <<< 'BigBanana1!'"
  echo ""
  echo "Then re-run without 'setup' to build and deploy:"
  echo "  bash deploy.sh"
  exit 0
fi

# ── Build & push ──────────────────────────────────────────────────────────────
echo "==> Writing build version..."
git rev-parse --short HEAD > VERSION 2>/dev/null || echo "dev" > VERSION

echo "==> Building and pushing image via Cloud Build..."
gcloud builds submit --tag "$IMAGE" --project "$PROJECT_ID" .

# ── Deploy ────────────────────────────────────────────────────────────────────
echo "==> Deploying to Cloud Run..."
gcloud run deploy "$SERVICE" \
  --image="$IMAGE" \
  --region="$REGION" \
  --platform=managed \
  --allow-unauthenticated \
  --memory=2Gi \
  --cpu=1 \
  --timeout=3600 \
  --min-instances=0 \
  --max-instances=2 \
  --set-env-vars="REPORTS_DIR=/data/reports,DB_PATH=/data/rank2.duckdb" \
  --set-secrets="ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest,GOOGLE_PLACES_API_KEY=GOOGLE_PLACES_API_KEY:latest,ACCESS_PASSWORD=ACCESS_PASSWORD:latest" \
  --add-volume="name=rank2-data,type=cloud-storage,bucket=${BUCKET}" \
  --add-volume-mount="volume=rank2-data,mount-path=/data"

echo ""
echo "Deploy complete!"
gcloud run services describe "$SERVICE" --region="$REGION" --format="value(status.url)"
