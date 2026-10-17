#!/usr/bin/env bash
# One-time setup so GitHub Actions can deploy SafeHer (.github/workflows/deploy.yml) without any stored key:
# GitHub proves "this run is on main of yadavithirwani/safeher" to Google (Workload Identity Federation),
# and Google lets that run act as the safeher-deployer service account. Also creates the bucket that holds
# data/safeher.duckdb, which is too big for Git. Asks before each change; safe to re-run.
#
#   scripts/setup_ci.sh          all steps
#   scripts/setup_ci.sh --data   only upload data/safeher.duckdb (after rebuilding the data)
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT="${SAFEHER_PROJECT:-safeher-511808}"
REGION="asia-south1"
REPO="yadavithirwani/safeher"
BUCKET="gs://$PROJECT-data"
POOL="github"; PROVIDER="github"
DEPLOYER="safeher-deployer@$PROJECT.iam.gserviceaccount.com"
RUNTIME_SA="safeher-api@$PROJECT.iam.gserviceaccount.com"
BUILD_SA="safeher-build@$PROJECT.iam.gserviceaccount.com"
GCLOUD="$(command -v gcloud || echo "$HOME/google-cloud-sdk/bin/gcloud")"

ask() { read -r -p "$* [y/N] " a; [[ "$a" == [yY]* ]]; }
gc()  { "$GCLOUD" --project="$PROJECT" --quiet "$@"; }

upload_data() {
  [[ -f data/safeher.duckdb ]] || { echo "data/safeher.duckdb missing: build it first"; exit 1; }
  gc storage cp data/safeher.duckdb "$BUCKET/safeher.duckdb"
  echo "Uploaded. The next GitHub deploy uses it (Actions → Deploy → Run workflow to deploy now)."
}
if [[ "${1:-}" == "--data" ]]; then upload_data; exit 0; fi

NUMBER="$(gc projects describe "$PROJECT" --format='value(projectNumber)')"

# --- 1. Data bucket ------------------------------------------------------------------------------
if ! gc storage buckets describe "$BUCKET" >/dev/null 2>&1; then
  if ask "1/4 Create bucket $BUCKET ($REGION, private) for data/safeher.duckdb (~145 MB, under \$0.01/month)?"; then
    gc storage buckets create "$BUCKET" --location="$REGION" --uniform-bucket-level-access --public-access-prevention
  fi
fi
if ask "    Upload data/safeher.duckdb to it now?"; then upload_data; fi

# --- 2. Deployer service account ----------------------------------------------------------------
if ! gc iam service-accounts describe "$DEPLOYER" >/dev/null 2>&1; then
  if ask "2/4 Create service account $DEPLOYER (what GitHub Actions deploys as)?"; then
    gc iam service-accounts create safeher-deployer --display-name="SafeHer GitHub deploys"
  fi
fi
if ask "    Give it the roles a deploy needs (Cloud Run, Cloud Build, source upload, Hosting, read the data bucket)?"; then
  for role in roles/run.admin roles/cloudbuild.builds.editor roles/storage.objectAdmin \
              roles/artifactregistry.reader roles/firebasehosting.admin roles/firebase.viewer \
              roles/serviceusage.serviceUsageConsumer roles/secretmanager.viewer roles/logging.viewer; do
    for try in 1 2 3 4 5 6; do  # a new account takes a few seconds to be visible
      gc projects add-iam-policy-binding "$PROJECT" --member="serviceAccount:$DEPLOYER" --role="$role" \
        --condition=None >/dev/null 2>/tmp/safeher-iam.err && { echo "  granted $role"; break; }
      grep -q "does not exist" /tmp/safeher-iam.err && [[ $try -lt 6 ]] || { cat /tmp/safeher-iam.err; exit 1; }
      sleep 10
    done
  done
  # Deploying a service that runs as safeher-api, built by safeher-build, needs "act as" on both.
  for sa in "$RUNTIME_SA" "$BUILD_SA"; do
    gc iam service-accounts add-iam-policy-binding "$sa" --member="serviceAccount:$DEPLOYER" \
      --role=roles/iam.serviceAccountUser >/dev/null && echo "  may act as ${sa%%@*}"
  done
fi

# --- 3. Workload Identity Federation: trust GitHub's tokens for this repo's main branch only -------
if ! gc iam workload-identity-pools describe "$POOL" --location=global >/dev/null 2>&1; then
  if ask "3/4 Create workload identity pool '$POOL' and a GitHub provider limited to $REPO, branch main?"; then
    gc iam workload-identity-pools create "$POOL" --location=global --display-name="GitHub Actions"
  fi
fi
if ! gc iam workload-identity-pools providers describe "$PROVIDER" --workload-identity-pool="$POOL" \
       --location=global >/dev/null 2>&1; then
  gc iam workload-identity-pools providers create-oidc "$PROVIDER" --workload-identity-pool="$POOL" \
    --location=global --display-name="GitHub" --issuer-uri="https://token.actions.githubusercontent.com" \
    --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition="assertion.repository=='$REPO' && assertion.ref=='refs/heads/main'"
fi

# --- 4. Let those GitHub runs act as the deployer -----------------------------------------------
if ask "4/4 Allow GitHub Actions runs of $REPO (main) to deploy as $DEPLOYER?"; then
  gc iam service-accounts add-iam-policy-binding "$DEPLOYER" --role=roles/iam.workloadIdentityUser \
    --member="principalSet://iam.googleapis.com/projects/$NUMBER/locations/global/workloadIdentityPools/$POOL/attribute.repository/$REPO" \
    >/dev/null && echo "  done"
fi

echo
echo "Ready. Values used by .github/workflows/deploy.yml (not secrets):"
echo "  workload_identity_provider: projects/$NUMBER/locations/global/workloadIdentityPools/$POOL/providers/$PROVIDER"
echo "  service_account:            $DEPLOYER"
