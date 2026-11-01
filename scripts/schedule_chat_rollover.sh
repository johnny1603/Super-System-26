#!/usr/bin/env bash
# Kept so the old command still works: every scheduler job (the chat rollover
# and its catch-up included) is created/updated by scripts/schedule_jobs.sh.
# Arguments pass through (e.g. --dry-run).
exec bash "$(dirname "$0")/schedule_jobs.sh" "$@"
