#!/usr/bin/env bash
# ONE entry point for uallak's Cloud Scheduler jobs. Creates each job, or
# updates it if it already exists (idempotent: run it again any time).
# The job table between "# BEGIN JOBS" and "# END JOBS" below is the single
# source of truth; scripts/scheduler_audit.py and tests/test_schedule_jobs.py
# read the same table.
#
# Every route is guarded by X-Admin-Key and registered in core/api_server.py
# with the HTTP method the table states (the audit and the tests check that).
# The key is read here from Secret Manager (the secret the Cloud Run service
# mounts as ADMIN_KEY; a plain env value is the fallback) and is NEVER printed:
# not in output, not in errors, not in --dry-run (masked as ****). --audit never
# reads the key at all, nor any job's headers.
#
# Usage (Cloud Shell, repo root):
#   bash scripts/schedule_jobs.sh             create/update all jobs, then show them
#   bash scripts/schedule_jobs.sh --dry-run   print the commands, key masked, change nothing
#   bash scripts/schedule_jobs.sh --audit     list EVERY job in the project/location: schedule,
#                                             path, method, last attempt, managed or not, and
#                                             flag a method that the route does not register
set -u

PROJECT="super-system-500410"
REGION="me-west1"                       # Cloud Run service region
LOCATION="${SCHEDULER_LOCATION:-me-west1}"
SERVICE="super-system"
BASE_URL="https://app.uallak.com"
TZ_NAME="Asia/Jerusalem"
MASK="****"
HERE="$(cd "$(dirname "$0")" && pwd)"

DRY_RUN=0
AUDIT=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --audit) AUDIT=1 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "Unknown argument: $arg (use --dry-run, --audit or --help)"; exit 2 ;;
  esac
done

# ─── the job table ───────────────────────────────────────────────────────────
# job NAME METHOD SCHEDULE PATH INITIAL DESCRIPTION
#   INITIAL: active | paused (paused = a job CREATED by this run starts paused;
#   an existing job keeps whatever state the owner chose)
NAMES=(); METHODS=(); SCHEDULES=(); PATHS=(); INITIALS=(); DESCRIPTIONS=()
job() {
  NAMES+=("$1"); METHODS+=("$2"); SCHEDULES+=("$3"); PATHS+=("$4"); INITIALS+=("$5"); DESCRIPTIONS+=("$6")
}
# retire NAME WHY — a job we used to run: paused if it exists (never deleted,
# so it can be looked at or resumed by hand), never created
RETIRED=()
retire() { RETIRED+=("$1"); }

# BEGIN JOBS
job chat-rollover GET "0 8 * * *" "/api/chat/rollover" active "Daily chat rollover at 08:00: summary + fresh window, nothing deleted"
job chat-rollover-catchup GET "10-59/15 8-22 * * *" "/api/chat/rollover" active "Chat rollover catch-up for windows deferred while the client was active"
job connection-reminders GET "0 10 * * *" "/api/connections/reminders" active "One gentle reminder per client and connection, when a connection was not completed"
job landing-hosting-costs GET "0 6 2 * *" "/api/landing/hosting-costs" paused "Monthly landing-page hosting cost estimate (needs the rates on /admin/landing/)"
job crm-retry-syncs GET "0 */6 * * *" "/api/crm/retry-syncs" active "Retry client leads whose sync to the client's own CRM failed"
job lead-volume-scan GET "0 6 * * *" "/api/leads/volume-scan" active "Daily lead volume and tier per client, plus uallak's own Supabase footprint"
job engagement-weekly GET "0 9 * * 0" "/api/engagement/weekly" active "Weekly client suggestions (Israeli calendar, trends, performance), Sunday 09:00"
job engagement-morning GET "30 8 * * 0-5" "/api/engagement/morning" active "Morning briefing 08:30 Sun-Fri, after the 08:00 chat rollover (no Shabbat or chag)"
retire engagement-daily "folded into the morning briefing (engagement-morning)"
job action-reaper GET "*/5 * * * *" "/api/actions/reap" active "Ends approved actions stuck running past their deadline: failed with a reason, alert closed, client told in chat"
job drive-usage-scan GET "30 3 * * *" "/api/drive/usage-scan" active "Daily Shared Drive storage scan (DRIVE_SHARED_ALERT_GB alert on status change)"
job credits-check GET "7 * * * *" "/api/credits/check" active "Hourly Claude credits estimate: one owner notice per threshold crossing (warn only)"
job tiktok-uploads GET "* * * * *" "/api/tiktok/uploads/run" active "Moves durable Drive to TikTok-inbox uploads forward, chunk by chunk, inside the request (no work when none is due)"
# END JOBS

AK=""

# ─── masking ─────────────────────────────────────────────────────────────────
# Every line that might carry the key goes through here before it is shown.
sanitize() {
  local text="$1"
  if [ -n "$AK" ]; then
    text="${text//"$AK"/$MASK}"
  fi
  # Belt and braces: any header assignment, whatever the value
  printf '%s' "$text" | sed -E "s/(X-Admin-Key[=:][[:space:]]*)[^[:space:],'\"}]+/\1${MASK}/Ig"
}

# The command as text, the key masked in each argument BEFORE quoting (so no
# escaping can split it), then single-quoted where needed
show_cmd() {
  local out="" a q="'"
  for a in "$@"; do
    if [ -n "$AK" ]; then
      a="${a//"$AK"/$MASK}"
    fi
    if [[ "$a" =~ ^[A-Za-z0-9_./:=@%+*-]+$ ]]; then
      out+=" $a"
    else
      out+=" ${q}${a//${q}/${q}\\${q}${q}}${q}"
    fi
  done
  sanitize "${out# }"
}

# ─── --audit: read-only, every job in the project/location ───────────────────
run_audit() {
  if ! command -v gcloud >/dev/null 2>&1; then
    echo "gcloud is not installed here - run this in Cloud Shell."
    exit 1
  fi
  echo "Cloud Scheduler jobs in project $PROJECT, location $LOCATION (read-only):"
  echo
  # Only these fields are requested - never httpTarget.headers
  local listing rc
  listing=$(gcloud scheduler jobs list --location="$LOCATION" --project="$PROJECT" \
    --format='value(name.basename(),schedule,httpTarget.uri,httpTarget.httpMethod,state,status.code,lastAttemptTime)' 2>/dev/null)
  rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "Could not list the jobs (exit $rc) - check: gcloud auth list, and access to $PROJECT."
    exit 1
  fi
  printf '%s\n' "$listing" | python3 "$HERE/scheduler_audit.py"
  exit $?
}

if [ "$AUDIT" -eq 1 ]; then
  run_audit
fi

# ─── the admin key, from Secret Manager ──────────────────────────────────────
read_admin_key() {
  local ref kind name version
  ref=$(gcloud run services describe "$SERVICE" --region "$REGION" --project="$PROJECT" \
          --format=json 2>/dev/null | python3 -c '
import json, sys
try:
    env = json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0].get("env", [])
except Exception:
    env = []
entry = next((e for e in env if e.get("name") == "ADMIN_KEY"), None)
if not entry:
    print("none")
elif "valueFrom" in entry:
    r = entry["valueFrom"].get("secretKeyRef", {})
    print("secret", r.get("name", ""), r.get("key", "latest") or "latest")
else:
    print("plain")
' 2>/dev/null)
  read -r kind name version <<< "$ref"
  case "$kind" in
    secret)
      AK=$(gcloud secrets versions access "$version" --secret="$name" --project="$PROJECT" 2>/dev/null)
      [ -n "$AK" ] && echo "Admin key: read from Secret Manager (secret $name, version $version)." ;;
    plain)
      # Not in Secret Manager yet: the value lives in the service env itself
      AK=$(gcloud run services describe "$SERVICE" --region "$REGION" --project="$PROJECT" \
             --format=json 2>/dev/null | python3 -c '
import json, sys
env = json.load(sys.stdin)["spec"]["template"]["spec"]["containers"][0].get("env", [])
print(next((e.get("value", "") for e in env if e.get("name") == "ADMIN_KEY"), ""))
' 2>/dev/null)
      [ -n "$AK" ] && echo "Admin key: ADMIN_KEY is a plain env var on $SERVICE (not in Secret Manager); using it." ;;
  esac
  if [ -z "$AK" ]; then
    echo "Could not read ADMIN_KEY for $SERVICE (Secret Manager or service env) - nothing created."
    exit 1
  fi
}

# ─── one job ─────────────────────────────────────────────────────────────────
FAILED=()
CREATED=()

job_exists() {
  gcloud scheduler jobs describe "$1" --location="$LOCATION" --project="$PROJECT" >/dev/null 2>&1
}

# upsert NAME METHOD SCHEDULE PATH DESCRIPTION
upsert() {
  local job="$1" method="$2" schedule="$3" path="$4" description="$5" action header_flag err rc
  local -a cmd
  if command -v gcloud >/dev/null 2>&1 && job_exists "$job"; then
    action="update"; header_flag="--update-headers"   # update accepts only --update-headers
  else
    action="create"; header_flag="--headers"          # create accepts only --headers
  fi
  cmd=(gcloud scheduler jobs "$action" http "$job"
       --project="$PROJECT" --location="$LOCATION"
       --schedule="$schedule" --time-zone="$TZ_NAME"
       --uri="${BASE_URL}${path}" --http-method="$method"
       "${header_flag}=X-Admin-Key=${AK}"
       --attempt-deadline=900s
       --description="$description"
       --quiet)
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry-run] $(show_cmd "${cmd[@]}")"
    [ "$action" = "create" ] && CREATED+=("$job")
    return 0
  fi
  err=$("${cmd[@]}" 2>&1 >/dev/null)
  rc=$?
  if [ "$rc" -ne 0 ]; then
    # Only the job name and gcloud's own ERROR line(s), sanitized; never the arguments
    err=$(printf '%s\n' "$err" | grep -E '^ERROR' | head -n 3)
    echo "FAILED: $job ($action, exit $rc): $(sanitize "${err:-no error text}" | cut -c1-400)"
    FAILED+=("$job")
    return "$rc"
  fi
  echo "OK: $job ${action}d"
  [ "$action" = "create" ] && CREATED+=("$job")
  return 0
}

# pause_if_new NAME — a job created by this run starts paused; an existing
# job keeps whatever state the owner chose
pause_if_new() {
  local job="$1" c err
  for c in ${CREATED[@]+"${CREATED[@]}"}; do
    [ "$c" = "$job" ] || continue
    if [ "$DRY_RUN" -eq 1 ]; then
      echo "[dry-run] gcloud scheduler jobs pause $job --location=$LOCATION --project=$PROJECT"
      return 0
    fi
    if ! err=$(gcloud scheduler jobs pause "$job" --location="$LOCATION" --project="$PROJECT" --quiet 2>&1 >/dev/null); then
      echo "FAILED: $job (pause): $(sanitize "$(printf '%s\n' "$err" | grep -E '^ERROR' | head -n 2)" | cut -c1-400)"
      FAILED+=("$job")
      return 1
    fi
    echo "OK: $job created paused (resume it when it should start running)"
  done
}

# ─── run ─────────────────────────────────────────────────────────────────────
if [ "$DRY_RUN" -eq 1 ]; then
  # Nothing is read or changed; DRY_RUN_KEY only exists so the masking can be tested
  AK="${DRY_RUN_KEY:-dry-run-placeholder-key}"
  echo "Dry run: nothing will be created or changed. Location $LOCATION, project $PROJECT."
else
  if ! command -v gcloud >/dev/null 2>&1; then
    echo "gcloud is not installed here - run this in Cloud Shell."
    exit 1
  fi
  read_admin_key
fi

for i in "${!NAMES[@]}"; do
  upsert "${NAMES[$i]}" "${METHODS[$i]}" "${SCHEDULES[$i]}" "${PATHS[$i]}" "${DESCRIPTIONS[$i]}"
  if [ "${INITIALS[$i]}" = "paused" ]; then
    pause_if_new "${NAMES[$i]}"
  fi
done

# Retired jobs: paused where they still exist
for job in ${RETIRED[@]+"${RETIRED[@]}"}; do
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry-run] gcloud scheduler jobs pause $job --location=$LOCATION --project=$PROJECT (retired, only if it exists)"
    continue
  fi
  job_exists "$job" || continue
  if err=$(gcloud scheduler jobs pause "$job" --location="$LOCATION" --project="$PROJECT" --quiet 2>&1 >/dev/null); then
    echo "OK: $job paused (retired)"
  elif printf '%s' "$err" | grep -qi "already paused\|FAILED_PRECONDITION"; then
    echo "OK: $job already paused (retired)"
  else
    echo "FAILED: $job (pause retired): $(sanitize "$(printf '%s\n' "$err" | grep -E '^ERROR' | head -n 2)" | cut -c1-400)"
    FAILED+=("$job")
  fi
done

unset AK

echo
echo "Managed by this script (${#NAMES[@]}): ${NAMES[*]}"
[ "${#RETIRED[@]}" -gt 0 ] && echo "Retired (kept paused): ${RETIRED[*]}"

if [ "$DRY_RUN" -eq 0 ]; then
  echo
  echo "Jobs in $LOCATION:"
  printf '%-24s %-6s %-22s %-16s %-9s %s\n' NAME METHOD SCHEDULE TIME_ZONE STATE NEXT_RUN
  for job in "${NAMES[@]}"; do
    # Only these fields are read: the job's headers are never requested
    line=$(gcloud scheduler jobs describe "$job" --location="$LOCATION" --project="$PROJECT" \
             --format='value(httpTarget.httpMethod,schedule,timeZone,state,scheduleTime)' 2>/dev/null)
    if [ -z "$line" ]; then
      printf '%-24s %s\n' "$job" "(not found)"
      continue
    fi
    IFS=$'\t' read -r method schedule tz state next <<< "$line"
    printf '%-24s %-6s %-22s %-16s %-9s %s\n' "$job" "$method" "$schedule" "$tz" "$state" "${next:--}"
  done
  echo
  echo "Full audit (every job in the project, method vs route): bash scripts/schedule_jobs.sh --audit"
fi

if [ "${#FAILED[@]}" -gt 0 ]; then
  echo
  echo "Failed: ${FAILED[*]}"
  exit 1
fi
