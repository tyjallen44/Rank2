#!/usr/bin/env bash
# What's live on production vs. what's pending on origin/main.
#   bash scripts/deploy_status.sh
set -euo pipefail
URL="${APP_URL:-https://rank2-883710187036.us-central1.run.app}"
cd "$(dirname "$0")/.."

LIVE=$(curl -sf --max-time 15 "$URL/api/version" \
       | python3 -c 'import sys,json; print(json.load(sys.stdin)["commit"])' 2>/dev/null || echo "")
[[ -n "$LIVE" ]] || { echo "ERROR: could not read $URL/api/version"; exit 1; }

git fetch -q origin main
HEAD_REMOTE=$(git rev-parse --short origin/main)

echo ""
echo "  Production : $LIVE   ($URL)"
echo "  origin/main: $HEAD_REMOTE"
echo ""

if ! git cat-file -e "${LIVE}^{commit}" 2>/dev/null; then
  echo "  ⚠ Live commit $LIVE is not in your local history (fetch/pull, or it was deployed from elsewhere)."
elif [[ "$(git rev-parse "$LIVE")" == "$(git rev-parse origin/main)" ]]; then
  echo "  ✓ Production is up to date with origin/main."
else
  N=$(git rev-list --count "$LIVE..origin/main")
  echo "  ▲ $N commit(s) on origin/main not yet deployed:"
  git log --oneline "$LIVE..origin/main" | sed 's/^/      /'
  echo ""
  echo "  Ship with:  bash deploy.sh"
fi

UNPUSHED=$(git log --oneline origin/main..main 2>/dev/null | wc -l | tr -d ' ')
[[ "$UNPUSHED" == "0" ]] || echo "  ⚠ $UNPUSHED local commit(s) on main not pushed yet (git push origin main)."
[[ -z "$(git status --porcelain)" ]] || echo "  ⚠ Uncommitted local changes present."
echo ""
