# Deploying SafeHer (Firebase Hosting + Cloud Run + Firestore)

Built to run for months on a Google Cloud free trial: **nothing runs, and nothing is billed, while nobody is using the app.**

```
Browser ──► Firebase Hosting (static web app, free) ──/api/**──► Cloud Run "safeher-api" (asia-south1)
                                                                   ├─ data/safeher.duckdb baked into the image (read-only)
                                                                   ├─ Firestore: community reports + live trips
                                                                   └─ Vertex AI (Gemini 2.5) via the service account (ADC)
```

| Piece | Setting | Why |
|---|---|---|
| Cloud Run | min **0** instances, max **2**, 1 vCPU, 1 GiB, CPU only during requests, startup boost | Scales to zero when idle. A cold start takes about 5–10 s, and the app shows "Starting up…". One process peaks around 600 MB. |
| Reference data | `data/safeher.duckdb` (145 MB) inside the container | No database server to pay for. To update it, rebuild the data locally and redeploy. |
| User data | Firestore (Native, asia-south1), with a TTL on `trips.expire_at` | Cloud Run's disk is wiped when it scales down. Free tier: 1 GiB stored, 50k reads and 20k writes per day. |
| Gemini / Maps grounding | Service account `safeher-api@…` with *Vertex AI User* | ADC on Cloud Run: no key stored. Teammates running locally can use the shared `VERTEX_API_KEY` instead (see below). |
| Google Maps key | Secret Manager `safeher-maps-key`, read only by the service account | Not in the image or any file. |
| Images | Artifact Registry keeps the 2 newest | Storage stays at about 1–2 GB, roughly $0.10/month. |
| Crawlers | `robots.txt` disallows `/api/` and `/live` | Bots don't wake the server. |

**What costs money:** Gemini calls (explanations, HerSide, the scope check) and Google Maps (tiles, Places, Routes, grounding), only when someone uses those features. Everything else stays within free tiers for light testing. A free trial isn't charged to a card unless you upgrade; the budget alert below is a safety net.

## One-time setup (you)

1. **Add Firebase to the Cloud project.** Go to [console.firebase.google.com](https://console.firebase.google.com), choose *Create a project*, then *Add Firebase to Google Cloud project*, and pick `safeher-511808`. Use the same Google account that owns the Cloud project, and skip Analytics. Choose the **Blaze** plan, which uses the trial billing account. Blaze is required for routing `/api/**` to Cloud Run.
2. **Budget alert.** In Cloud console, go to *Billing*, then *Budgets & alerts*, and create one (e.g. ₹1,000) with email alerts at 50%, 90% and 100%.
3. **Sign in the command-line tools** on your Mac:
   ```bash
   ~/google-cloud-sdk/bin/gcloud auth login
   curl -sL https://firebase.tools | bash
   firebase login
   ```
4. **Build the data** if you haven't: `cd backend && .venv/bin/python -m safeher.data build`.
5. **Deploy:** run `scripts/deploy.sh`. It asks before each step:
   1. turn on APIs
   2. create Firestore and its TTL
   3. create the service account and grant its roles
   4. store the Maps key (read from `backend/.env`, never printed)
   5. build and deploy Cloud Run
   6. deploy Hosting

   It prints the app's address: `https://safeher-511808.web.app`.
6. **Optional:** restrict the Maps key. In Cloud console, go to *APIs & Services*, then *Credentials*, and restrict it to the Maps APIs SafeHer uses: Map Tiles, Places (New), Routes, Street View Static and Geocoding. Don't add a website restriction: the server calls Maps itself, without a browser referrer.

## Sharing Gemini with teammates (Vertex AI API key)

AI Studio Gemini keys can no longer use Gemini 2.5, so the team shares a **Vertex AI** API key instead, which is billed to this project:
1. In Cloud console, open **Vertex AI → API keys** (or [Express mode](https://console.cloud.google.com/expressmode)) for `safeher-511808`, and create a key linked to the trial billing account.
2. Restrict it to the **Vertex AI API** (*APIs & Services → Credentials → the key → API restrictions*).
3. Share it privately, never in Git or a chat log. Each person puts `VERTEX_API_KEY=…` in their own `backend/.env`.
4. Rotate it (create a new key and delete the old one) when the team changes or the hackathon ends.

The deployed server keeps using its service account (ADC) and doesn't need the key.

**Recommended, with no key passed around (and ~10× faster than the key):** give a teammate's Google account *Vertex AI User* and *Service Usage Consumer* on the project:
```bash
gcloud projects add-iam-policy-binding safeher-511808 --member=user:NAME@gmail.com --role=roles/aiplatform.user --condition=None
gcloud projects add-iam-policy-binding safeher-511808 --member=user:NAME@gmail.com --role=roles/serviceusage.serviceUsageConsumer --condition=None
```
They run `gcloud auth application-default login` once and set `GOOGLE_CLOUD_PROJECT=safeher-511808` in `backend/.env`, leaving `VERTEX_API_KEY` empty. Usage is still billed to this project. To remove one person, delete their two role bindings. Shreya and Anushka were given access this way on 17 Oct.

## Updating

- **Code change:** merge or push to `main`; GitHub Actions deploys it (below). By hand: `scripts/deploy.sh --app` rebuilds the container and redeploys Hosting with no setup questions.
- **Data change:** `scripts/setup_ci.sh --data`, then run the workflow (or `scripts/deploy.sh --app` locally).
- **Pause everything:** nothing is needed, since idle costs nothing. To take it offline, delete the Cloud Run service (`gcloud run services delete safeher-api --region asia-south1`) and disable Hosting (`firebase hosting:disable`).

## Automatic deploys (GitHub Actions)

`.github/workflows/deploy.yml` runs on every push to `main` (doc-only changes are skipped) and can also be started by hand: *Actions → Deploy → Run workflow*. It:
1. signs in to Google with no stored key, using Workload Identity Federation. Google trusts only runs on `main` of `yadavithirwani/safeher`, which then act as the service account `safeher-deployer`
2. downloads `data/safeher.duckdb` from the private bucket `gs://safeher-511808-data`, because the database isn't in Git
3. runs the offline tests (about 400, ~4 min; no Gemini or Maps calls), and stops if any fail
4. runs `scripts/deploy.sh --app` (Cloud Run + Hosting)

**One-time setup:** run `scripts/setup_ci.sh` from your Mac. It asks before each step: the data bucket and upload, the deployer account and its roles, the identity pool limited to this repo, and the permission to act as the deployer. No GitHub settings or secrets are needed.

**After rebuilding the data:** run `scripts/setup_ci.sh --data` to upload it, then *Run workflow*, or push to main.

**Cost:** GitHub runners are free for public repos (2,000 minutes a month for private ones; a run takes about 10 minutes). The bucket costs under $0.01/month, plus about 2¢ of transfer per deploy.

## Local development is unchanged

Without `SAFEHER_USERDB=firestore`, reports and live trips stay in `backend/var/safeher.sqlite3`. Tests run the Firestore path against an in-memory stand-in (`tests/test_userdb_firestore.py`).
