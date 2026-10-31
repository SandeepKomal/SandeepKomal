#!/usr/bin/env bash
# Commit the chaos state and rendered files to main, rebasing over other profile jobs.
# Usage: publish.sh [file...] (defaults to the chaos files); needs GH_TOKEN and MESSAGE.
# Exit codes: 0 published or nothing to publish, 1 push failed after retries.
set -euo pipefail

files=("$@")
if [ ${#files[@]} -eq 0 ]; then
  files=(chaos/state.json brand/hero.svg brand/hero-light.svg brand/production.svg brand/production-light.svg brand/events.svg brand/events-light.svg README.md)
fi

git add "${files[@]}"
if git diff --cached --quiet; then
  echo "Nothing to publish."
  exit 0
fi

# checkout keeps no credentials; the token is used for this push only
git remote set-url origin "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git commit -q -m "$MESSAGE"

for attempt in 1 2 3 4; do
  git fetch -q origin main
  git rebase -q origin/main
  if git push -q origin HEAD:main; then
    exit 0
  fi
  sleep $((attempt * 2))
done

echo "Push failed after 4 attempts." >&2
exit 1
