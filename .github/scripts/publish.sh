#!/usr/bin/env bash
# Commit the chaos state and rendered files to main, rebasing over other profile jobs.
# Exit codes: 0 published or nothing to publish, 1 push failed after retries.
set -euo pipefail

files=(chaos/state.json brand/hero.svg brand/production.svg README.md)

git add "${files[@]}"
if git diff --cached --quiet; then
  echo "Nothing to publish."
  exit 0
fi

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
