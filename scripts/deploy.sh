#!/usr/bin/env bash
# Deploy the ERP: pull latest, run the test suite, restart only if it's green.
#
# Why this exists (Roadmap E5): deploys used to be a bare `git pull` +
# `systemctl restart erp`, so a regression could ship straight to production
# with no gate. This script is the smallest change that fixes that: if the
# test suite fails, the running app is left untouched.
#
# Usage (on the VPS, from the repo root):
#   ./scripts/deploy.sh
#
# Exit code: 0 on a successful deploy, non-zero if pull/test/restart failed
# (and in the test-failure case, the app was NOT restarted).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') deploy: $*"; }

log "pulling latest..."
git pull

log "running test suite..."
if ! .venv/bin/python -m pytest -q; then
    log "ERROR: tests failed — aborting deploy. The running app was NOT restarted."
    exit 1
fi

log "tests passed — restarting erp.service..."
sudo systemctl restart erp

log "deploy complete."
