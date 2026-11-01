#!/usr/bin/env bash
# Kept so the old command still works: the daily Shared Drive usage scan
# (drive-usage-scan) is created/updated with every other scheduler job by
# scripts/schedule_jobs.sh, the one entry point. Arguments pass through.
exec bash "$(dirname "$0")/schedule_jobs.sh" "$@"
