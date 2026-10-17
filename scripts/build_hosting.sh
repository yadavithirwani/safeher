#!/usr/bin/env bash
# Assemble Firebase Hosting's folder from app/web, in the same layout FastAPI serves locally:
#   /            -> index.html
#   /static/...  -> every file in app/web
#   /live        -> /static/live.html (a rewrite in firebase.json)
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf hosting/public
mkdir -p hosting/public/static
cp app/web/* hosting/public/static/
cp app/web/index.html hosting/public/index.html
cat > hosting/public/robots.txt <<'TXT'
# The API costs money to wake up; crawlers have no reason to call it.
User-agent: *
Disallow: /api/
Disallow: /live
TXT
echo "hosting/public: $(find hosting/public -type f | wc -l | tr -d ' ') files"
