#!/usr/bin/env bash
# One-shot: fork upstream, push the PoC harness branch, run the pipeline,
# download the evidence bundle.
#
# Prereqs:  gh CLI installed and authenticated (`gh auth login`), git, network.
# Usage:    ./setup_fork.sh [workdir]
set -euo pipefail

HARNESS="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="${1:-$HOME/flowcrypt-poc-run}"
UPSTREAM="FlowCrypt/flowcrypt-ios"
BRANCH="poc/imap-log"

command -v gh >/dev/null || { echo "gh CLI missing: sudo apt-get install -y gh  (or brew install gh)"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "not logged in - run: gh auth login"; exit 1; }

ME="$(gh api user -q .login)"
echo "==> authenticated as $ME"

mkdir -p "$WORK"
cd "$WORK"
if [ ! -d flowcrypt-ios/.git ]; then
  echo "==> forking $UPSTREAM (public fork, no upstream write access)"
  gh repo fork "$UPSTREAM" --clone=true --remote=true
else
  echo "==> reusing $WORK/flowcrypt-ios"
fi
cd flowcrypt-ios

git fetch origin >/dev/null 2>&1 || true
git checkout -B "$BRANCH"

echo "==> installing harness files (workflow + poc/, no app source touched)"
mkdir -p .github/workflows
cp "$HARNESS/.github/workflows/poc.yml" .github/workflows/poc.yml
rm -rf poc
cp -R "$HARNESS/poc" poc

git add .github/workflows/poc.yml poc
if git diff --cached --quiet; then
  echo "==> nothing new to commit"
else
  git -c user.name="poc" -c user.email="poc@localhost" commit -m "PoC: IMAP credential logging on upgraded installs"
fi
git push -u origin "$BRANCH"

echo "==> dispatching workflow"
sleep 5
gh workflow run flowcrypt-imap-log-poc --ref "$BRANCH"
sleep 15
RUN_ID="$(gh run list --workflow=flowcrypt-imap-log-poc --branch "$BRANCH" --limit 1 --json databaseId -q '.[0].databaseId')"
echo "==> run id: $RUN_ID"
gh run watch "$RUN_ID" || true

ART="$WORK/artifact"
rm -rf "$ART"
gh run download "$RUN_ID" -D "$ART" || { echo "download failed - open the run page: gh run view $RUN_ID --web"; exit 1; }
echo
echo "==> artifacts in $ART"
find "$ART" -maxdepth 2 -type f | head -40
echo
echo "==> quick verdict"
grep -i "LOGIN" "$ART"/*/console-new-after-upgrade.log 2>/dev/null | head -3 || echo "!! no LOGIN in the upgraded-build log - inspect console-*.log before reporting"
