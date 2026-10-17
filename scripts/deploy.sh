#!/usr/bin/env bash
# Deploy SafeHer: API on Cloud Run (scales to zero), web app on Firebase Hosting, user data in Firestore.
# Every step that changes the Google Cloud project asks first. Safe to re-run: existing resources are kept.
#
#   scripts/deploy.sh            all steps (setup + deploy)
#   scripts/deploy.sh --app      redeploy code/data only (Cloud Run + Hosting), no setup questions
#
# Needs: gcloud signed in (gcloud auth login), firebase CLI signed in (firebase login), data/safeher.duckdb
# built, and the Firebase project added to this Cloud project. See docs/DEPLOY.md.
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT="${SAFEHER_PROJECT:-safeher-511808}"
REGION="asia-south1"                    # Mumbai: closest to Delhi and Bengaluru users
SERVICE="safeher-api"
SA_NAME="safeher-api"
SA="$SA_NAME@$PROJECT.iam.gserviceaccount.com"
SECRET="safeher-maps-key"
GCLOUD="$(command -v gcloud || echo "$HOME/google-cloud-sdk/bin/gcloud")"
FIREBASE="$(command -v firebase || echo firebase)"
APP_ONLY=false; [[ "${1:-}" == "--app" ]] && APP_ONLY=true

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ask()  { $APP_ONLY && return 1; read -r -p "$* [y/N] " a; [[ "$a" == [yY]* ]]; }
gc()   { "$GCLOUD" --project="$PROJECT" --quiet "$@"; }

# --- Checks -------------------------------------------------------------------------------------
[[ -x "$GCLOUD" ]] || { echo "gcloud not found (install the Google Cloud SDK)"; exit 1; }
"$GCLOUD" auth list --filter=status:ACTIVE --format="value(account)" | grep -q . \
  || { echo "gcloud is not signed in: run  $GCLOUD auth login"; exit 1; }
[[ -f data/safeher.duckdb ]] || { echo "data/safeher.duckdb missing: run  cd backend && .venv/bin/python -m safeher.data build"; exit 1; }
command -v "$FIREBASE" >/dev/null || { echo "firebase CLI not found: curl -sL https://firebase.tools | bash"; exit 1; }
say "Project $PROJECT, region $REGION, signed in as $("$GCLOUD" auth list --filter=status:ACTIVE --format='value(account)')"

if ! $APP_ONLY; then
  # --- 1. APIs ----------------------------------------------------------------------------------
  if ask "1/6 Turn on Cloud Run, Cloud Build, Artifact Registry, Firestore, Secret Manager, Vertex AI?"; then
    gc services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
      firestore.googleapis.com secretmanager.googleapis.com aiplatform.googleapis.com
  fi

  # --- 2. Firestore (reports and live trips) ----------------------------------------------------
  if gc firestore databases describe --database="(default)" >/dev/null 2>&1; then
    echo "Firestore database exists."
  elif ask "2/6 Create the Firestore database (Native mode, $REGION)?"; then
    gc firestore databases create --location="$REGION" --type=firestore-native
  fi
  if ask "    Let Firestore delete expired live trips by itself (TTL on trips.expire_at)?"; then
    gc firestore fields ttls update expire_at --collection-group=trips --enable-ttl --async
  fi

  # --- 3. Service account: the server's own identity for ADC -----------------------------------
  if ! gc iam service-accounts describe "$SA" >/dev/null 2>&1; then
    if ask "3/6 Create service account $SA (the server signs in to Vertex AI and Firestore as it)?"; then
      gc iam service-accounts create "$SA_NAME" --display-name="SafeHer API (Cloud Run)"
    fi
  fi
  if ask "    Give it Vertex AI User and Cloud Datastore User (Firestore)?"; then
    for role in roles/aiplatform.user roles/datastore.user; do
      gc projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$SA" --role="$role" --condition=None >/dev/null
      echo "  granted $role"
    done
  fi

  # --- 4. Google Maps key -> Secret Manager (never printed) ------------------------------------
  KEY="$(grep -E '^GOOGLE_MAPS_API_KEY=' backend/.env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'"'"' \r' || true)"
  if [[ -n "$KEY" ]] && ask "4/6 Store the Google Maps key from backend/.env in Secret Manager as '$SECRET'?"; then
    gc secrets describe "$SECRET" >/dev/null 2>&1 || gc secrets create "$SECRET" --replication-policy=automatic
    printf '%s' "$KEY" | gc secrets versions add "$SECRET" --data-file=- >/dev/null
    gc secrets add-iam-policy-binding "$SECRET" --member="serviceAccount:$SA" --role=roles/secretmanager.secretAccessor >/dev/null
    echo "  stored (value not shown)"
  fi
  unset KEY
fi

# --- 5. Cloud Run: build the container (code + data/safeher.duckdb) and deploy ------------------
SECRETS_FLAG=()
gc secrets describe "$SECRET" >/dev/null 2>&1 && SECRETS_FLAG=(--set-secrets="GOOGLE_MAPS_API_KEY=$SECRET:latest")
if $APP_ONLY || ask "5/6 Build and deploy the API to Cloud Run (min 0 instances = no cost when idle, max 2)?"; then
  gc run deploy "$SERVICE" --source . --region="$REGION" \
    --service-account="$SA" --allow-unauthenticated \
    --cpu=1 --memory=1Gi --cpu-throttling --cpu-boost \
    --min-instances=0 --max-instances=2 --concurrency=20 --timeout=120 \
    --set-env-vars="SAFEHER_USERDB=firestore,GOOGLE_CLOUD_PROJECT=$PROJECT,GOOGLE_CLOUD_LOCATION=global,CHATBOT_PROVIDER=gemini,SAFEHER_CORRIDOR_CACHE=8" \
    ${SECRETS_FLAG[@]+"${SECRETS_FLAG[@]}"}
  # Keep only the 2 newest images, so storage doesn't grow with every deploy over the months.
  cat > /tmp/safeher-cleanup.json <<'JSON'
[{"name": "keep-two-newest", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 2}},
 {"name": "delete-older", "action": {"type": "Delete"}, "condition": {"tagState": "ANY"}}]
JSON
  gc artifacts repositories set-cleanup-policies cloud-run-source-deploy --location="$REGION" \
    --policy=/tmp/safeher-cleanup.json --no-dry-run >/dev/null && echo "Image cleanup: keep the 2 newest."
fi

# --- 6. Firebase Hosting: the web app, with /api/** routed to Cloud Run ------------------------
if $APP_ONLY || ask "6/6 Deploy the web app to Firebase Hosting?"; then
  scripts/build_hosting.sh
  "$FIREBASE" deploy --only hosting --project "$PROJECT"
fi

say "Done. Web app: https://$PROJECT.web.app  (API wakes on the first request, sleeps when idle)"
