#!/usr/bin/env bash
# Remove committed binaries from the whole git history.
#
# The two APKs under APKs/ were 67.7 MB of a 106 MB repository and nothing ever
# read them: the mirror workflows publish APKs to GitHub Releases, which have a
# 2 GiB per-file limit and do not count against repository size.
#
# Deleting the files from the working tree does NOT reduce the repository size,
# because the blobs stay reachable from history. This rewrites history instead.
#
#   ./scripts/purge_binaries.sh            # dry run: report what would change
#   ./scripts/purge_binaries.sh --apply    # rewrite and force-push
#
# Requirements: git-filter-repo  (pip install git-filter-repo)
#
# WARNING: rewriting history changes every commit SHA. Forks diverge and open
# pull requests need rebasing. The data branch is force-pushed by its workflow
# anyway, so only `main` matters. Run this once, during a quiet period.

set -euo pipefail

APPLY="${1:-}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [ ! -d .git ]; then
  echo "error: $REPO_ROOT is not a git clone. Clone it first:" >&2
  echo "  git clone https://github.com/RookieEnough/Orion-Data.git" >&2
  exit 1
fi

if ! command -v git-filter-repo >/dev/null 2>&1; then
  echo "error: git-filter-repo not found. Install it with:" >&2
  echo "  pip install git-filter-repo" >&2
  exit 1
fi

echo "== current repository size =="
git count-objects -vH | sed -n 's/^/  /p'

echo
echo "== largest blobs currently reachable from history =="
git rev-list --objects --all \
  | git cat-file --batch-check='%(objecttype) %(objectname) %(objectsize) %(rest)' \
  | awk '$1=="blob"{print $3, $4}' \
  | sort -rn \
  | head -10 \
  | awk '{printf "  %10.2f MB  %s\n", $1/1048576, $2}'

echo
echo "== blobs to purge (patterns) =="
for pattern in 'APKs/*' '*.apk' '*.aab' '*.apks' '*.xapk'; do
  echo "  $pattern"
done

if [ "$APPLY" != "--apply" ]; then
  echo
  echo "dry run. Re-run with --apply to rewrite history."
  exit 0
fi

echo
echo "== rewriting history =="
git filter-repo \
  --force \
  --invert-paths \
  --path 'APKs/' \
  --path-glob '*.apk' \
  --path-glob '*.aab' \
  --path-glob '*.apks' \
  --path-glob '*.xapk'

echo
echo "== size after rewrite (local) =="
git count-objects -vH | sed -n 's/^/  /p'

cat <<'EOF'

History rewritten locally. To publish:

  git remote add origin https://github.com/RookieEnough/Orion-Data.git   # if filter-repo dropped it
  git push --force origin main
  git push --force origin data

GitHub reclaims the space when it next garbage-collects the repository. This
project already has direct evidence that it does: the repository is ~106 MB
after roughly a year of force-pushing 48 MB blobs four times a day, which would
be ~70 GB if unreachable objects were retained.

After the push, check the size the API reports:

  curl -s https://api.github.com/repos/RookieEnough/Orion-Data | grep '"size"'
EOF
