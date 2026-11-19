#!/usr/bin/env bash
# scripts/deploy-gcp.sh — build and deploy the web app to Cloud Run.
#
# Prerequisites (run setup:gcp once first):
#   - APIs enabled: run, cloudbuild, artifactregistry
#   - Artifact Registry repo: us-central1-docker.pkg.dev/runway-515115/runway-web
#   - Service accounts: runway-web-runtime, runway-deploy
#   - Frontend env vars in .env.development.local or exported in the shell
#
# Usage:
#   npm run deploy:gcp
#
set -euo pipefail

PROJECT=runway-515115
REGION=us-central1
SERVICE=runway-web
IMAGE_BASE="us-central1-docker.pkg.dev/${PROJECT}/runway-web/app"
RUNTIME_SA="runway-web-runtime@${PROJECT}.iam.gserviceaccount.com"

# ── Resolve commit SHA ────────────────────────────────────────────────────────
SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "local")
IMAGE="${IMAGE_BASE}:${SHA}"

# ── Load frontend build-time env vars ────────────────────────────────────────
# Source .env.development.local if present; explicitly exported vars override.
ENV_FILE="$(dirname "$0")/../.env.development.local"
if [[ -f "$ENV_FILE" ]]; then
  # Export only the VITE_ vars we need; do not leak other secrets into the env
  set -o allexport
  # shellcheck disable=SC1090
  source <(grep -E '^VITE_(MAPBOX_ACCESS_TOKEN|SUPABASE_URL|SUPABASE_ANON_KEY)=' "$ENV_FILE")
  set +o allexport
fi

: "${VITE_MAPBOX_ACCESS_TOKEN:?VITE_MAPBOX_ACCESS_TOKEN must be set}"
: "${VITE_SUPABASE_URL:?VITE_SUPABASE_URL must be set}"
: "${VITE_SUPABASE_ANON_KEY:?VITE_SUPABASE_ANON_KEY must be set}"

# ── Check gcloud is authenticated and has access ──────────────────────────────
echo "▶ Checking gcloud access to ${PROJECT}..."
gcloud projects describe "${PROJECT}" --format="value(projectId)" > /dev/null

# ── Build remotely with Cloud Build ──────────────────────────────────────────
echo "▶ Submitting build for ${IMAGE}..."
gcloud builds submit . \
  --project="${PROJECT}" \
  --config=cloudbuild.web.yaml \
  --substitutions=\
"SHORT_SHA=${SHA},\
_VITE_MAPBOX_ACCESS_TOKEN=${VITE_MAPBOX_ACCESS_TOKEN},\
_VITE_SUPABASE_URL=${VITE_SUPABASE_URL},\
_VITE_SUPABASE_ANON_KEY=${VITE_SUPABASE_ANON_KEY}"

# ── Resolve the immutable image digest ───────────────────────────────────────
echo "▶ Resolving image digest..."
DIGEST=$(gcloud artifacts docker images describe "${IMAGE}" \
  --project="${PROJECT}" \
  --format="value(image_summary.digest)" 2>/dev/null)
IMAGE_BY_DIGEST="${IMAGE_BASE}@${DIGEST}"
echo "  Image: ${IMAGE_BY_DIGEST}"

# ── Deploy candidate revision (0% traffic) ───────────────────────────────────
REVISION="${SERVICE}-${SHA}"
echo "▶ Deploying candidate revision ${REVISION}..."
gcloud run deploy "${SERVICE}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --image="${IMAGE_BY_DIGEST}" \
  --service-account="${RUNTIME_SA}" \
  --update-env-vars="WEATHER_CACHE_BUCKET=runway-weather,USE_FIXTURES=false" \
  --port=8080 \
  --cpu=1 \
  --memory=512Mi \
  --min-instances=0 \
  --max-instances=1 \
  --revision-suffix="${SHA}" \
  --no-traffic \
  --allow-unauthenticated

# ── Smoke-test the candidate ─────────────────────────────────────────────────
echo "▶ Smoke-testing candidate..."
CANDIDATE_URL=$(gcloud run revisions describe "${SERVICE}-${SHA}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --format="value(status.url)" 2>/dev/null || true)

# Fall back to tagging the revision URL via the service URL + tag
if [[ -z "$CANDIDATE_URL" ]]; then
  SERVICE_URL=$(gcloud run services describe "${SERVICE}" \
    --project="${PROJECT}" \
    --region="${REGION}" \
    --format="value(status.url)")
  CANDIDATE_URL="${SERVICE_URL}"
fi

HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  --max-time 30 "${CANDIDATE_URL}/health" || echo "000")

if [[ "$HTTP_STATUS" != "200" ]]; then
  echo "✗ Smoke test failed (HTTP ${HTTP_STATUS}). Production traffic unchanged."
  echo "  Rollback: gcloud run services update-traffic ${SERVICE} --project=${PROJECT} --region=${REGION} --to-latest"
  exit 1
fi
echo "  /health → ${HTTP_STATUS} ✓"

# ── Promote to 100% traffic ───────────────────────────────────────────────────
echo "▶ Promoting ${REVISION} to 100% traffic..."
gcloud run services update-traffic "${SERVICE}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --to-revisions="${SERVICE}-${SHA}=100"

# ── Print summary ─────────────────────────────────────────────────────────────
SERVICE_URL=$(gcloud run services describe "${SERVICE}" \
  --project="${PROJECT}" \
  --region="${REGION}" \
  --format="value(status.url)")

echo ""
echo "✓ Deployed successfully"
echo "  URL:      ${SERVICE_URL}"
echo "  Revision: ${SERVICE}-${SHA}"
echo "  Image:    ${IMAGE_BY_DIGEST}"
echo "  Rollback: gcloud run services update-traffic ${SERVICE} --project=${PROJECT} --region=${REGION} --to-revisions=PREVIOUS_REVISION=100"
