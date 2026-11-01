#!/usr/bin/env bash
# ONE entry point for uallak's Cloud Scheduler jobs. Creates each job, or
# updates it if it already exists (idempotent: run it again any time).
#
#   chat-rollover           08:00 Asia/Jerusalem daily      GET /api/chat/rollover
#   chat-rollover-catchup   every 15 min 08:10-22:55        GET /api/chat/rollover
#                           (windows deferred while the client was active;
#                           the route is idempotent, one summary per chat per day)
#   connection-reminders    10:00 Asia/Jerusalem daily      GET /api/connections/reminders
#                           (at most ONE reminder per client + connection, ever)
#   landing-hosting-costs   06:00 on the 2nd of each month  GET /api/landing/hosting-costs
#                           created PAUSED — resume it after setting the rates
#                           on /admin/landing/:
#                           gcloud scheduler jobs resume landing-hosting-costs --location=me-west1 --project=super-system-500410
#
# The routes are GET (core/api_server.py) and are guarded by X-Admin-Key.
# The key is read here from Secret Manager (the secret the Cloud Run service
# mounts as ADMIN_KEY; a plain env value is the fallback) and is NEVER printed:
# not in output, not in errors, not in --dry-run (masked as ****).
#
# Usage (Cloud Shell, repo root):
#   bash scripts/schedule_jobs.sh             create/update all jobs, then show them
#   bash scripts/schedule_jobs.sh --dry-run   print the commands, key masked, change nothing
set -u

PROJECT="super-system-500410"
REGION="me-west1"                       # Cloud Run service region
LOCATION="${SCHEDULER_LOCATION:-me-west1}"
SERVICE="super-system"
BASE_URL="https://app.uallak.com"
TZ_NAME="Asia/Jerusalem"
MASK="****"

DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    *) echo "Unknown argument: $arg (use --dry-run or --help)"; exit 2 ;;
  esac
done

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

# upsert NAME SCHEDULE PATH DESCRIPTION
upsert() {
  local job="$1" schedule="$2" path="$3" description="$4" action header_flag err rc
  local -a cmd
  if command -v gcloud >/dev/null 2>&1 && job_exists "$job"; then
    action="update"; header_flag="--update-headers"   # update accepts only --update-headers
  else
    action="create"; header_flag="--headers"          # create accepts only --headers
  fi
  cmd=(gcloud scheduler jobs "$action" http "$job"
       --project="$PROJECT" --location="$LOCATION"
       --schedule="$schedule" --time-zone="$TZ_NAME"
       --uri="${BASE_URL}${path}" --http-method=GET
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
    echo "OK: $job paused (resume after setting the rates on /admin/landing/)"
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

upsert chat-rollover "0 8 * * *" "/api/chat/rollover" \
  "Daily chat rollover at 08:00: summary + fresh window, nothing deleted"
upsert chat-rollover-catchup "10-59/15 8-22 * * *" "/api/chat/rollover" \
  "Chat rollover catch-up for windows deferred while the client was active"
upsert connection-reminders "0 10 * * *" "/api/connections/reminders" \
  "One gentle reminder per client and connection, when a connection was not completed"
upsert landing-hosting-costs "0 6 2 * *" "/api/landing/hosting-costs" \
  "Monthly landing-page hosting cost estimate (needs the rates on /admin/landing/)"
pause_if_new landing-hosting-costs

unset AK

if [ "$DRY_RUN" -eq 0 ]; then
  echo
  echo "Jobs in $LOCATION:"
  printf '%-24s %-22s %-16s %-9s %s\n' NAME SCHEDULE TIME_ZONE STATE NEXT_RUN
  for job in chat-rollover chat-rollover-catchup connection-reminders landing-hosting-costs; do
    # Only these fields are read: the job's headers are never requested
    line=$(gcloud scheduler jobs describe "$job" --location="$LOCATION" --project="$PROJECT" \
             --format='value(schedule,timeZone,state,scheduleTime)' 2>/dev/null)
    if [ -z "$line" ]; then
      printf '%-24s %s\n' "$job" "(not found)"
      continue
    fi
    IFS=$'\t' read -r schedule tz state next <<< "$line"
    printf '%-24s %-22s %-16s %-9s %s\n' "$job" "$schedule" "$tz" "$state" "${next:--}"
  done
fi

if [ "${#FAILED[@]}" -gt 0 ]; then
  echo
  echo "Failed: ${FAILED[*]}"
  exit 1
fi
