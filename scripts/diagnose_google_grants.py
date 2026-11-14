#!/usr/bin/env python3
"""READ-ONLY diagnosis of one client's Google grants (Google Ads, YouTube,
Tag Manager), run in Cloud Shell from the repo root:

    python3 scripts/diagnose_google_grants.py 27
    python3 scripts/diagnose_google_grants.py 27 --live-reads

Prints, copy-paste friendly:
  1. EVERY stored Google client_accounts row (id, platform, account, status,
     created/updated) — several rows for one platform are flagged: readers
     take the NEWEST id, while upsert_account updates whichever row the DB
     returns first (no ORDER BY), so a reconnect can land in a row nobody reads.
  2. The connect / disconnect timeline from client_activity (gtm_api_connected
     with its scopes, YouTube account_connected, account_disconnected, …) and
     the hidden alert-dedup markers (chat_fallback issue_key), newest first.
  3. Per product, a REAL refresh + Google tokeninfo on the grant the product
     actually uses (GET /api/admin/google/scopes — the newest row per platform):
     alive / dead, and its scopes vs the six.
  4. A harmless Tag Manager read with that grant (GET /api/website/
     conversion-tracking: versions.live of the stored container).
  5. The admin alerts that mention this client (newest 100 alerts scanned).
  --live-reads adds two more real reads. They CAN leave a trace, which is why
  they are opt-in: YouTube channels.list mine=true (+ uploads) via
  /api/youtube-content/engagement raises an admin alert if it fails, and the
  Google Ads listAccessibleCustomers via the Control Room picker records the
  grant's health (app_settings gads_token:<id>) and alerts once if it is dead.

Keys: ADMIN_KEY and ADMIN_PASSWORD are read from the Cloud Run env (or typed,
hidden). Never prints a key, a password, a token or the last characters of
one. Changes nothing (without --live-reads).
"""
import getpass
import http.cookiejar
import json
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

PROJECT = "super-system-500410"
SERVICE = "super-system"
REGION = "me-west1"
BASE = "https://app.uallak.com"
GOOGLE_PLATFORMS = ("google_ads", "youtube", "youtube_pending", "google_tagmanager")
TIMELINE_ACTIONS = ("gtm_api_connected", "gtm_container_selected", "account_connected", "account_disconnected",
                    "account_created_by_admin", "google_ads_connected", "google_ads_account_selected",
                    "google_ads_account_pending", "channel_guide_sent")
LINE = "-" * 78
_SCRUB = re.compile(r"(ya29\.[\w.-]+|1//[\w.-]+|access_token=[^&\s]+|refresh_token=[^&\s]+)")


def scrub(text) -> str:
    return _SCRUB.sub("[redacted]", str(text if text is not None else ""))


def cloud_run_env(name: str) -> str:
    try:
        raw = subprocess.run(["gcloud", "run", "services", "describe", SERVICE, "--region", REGION,
                              f"--project={PROJECT}", "--format=json"],
                             capture_output=True, text=True, timeout=60).stdout
        env = json.loads(raw)["spec"]["template"]["spec"]["containers"][0].get("env", [])
        return next((e.get("value", "") for e in env if e.get("name") == name), "") or ""
    except Exception:
        return ""


def secret(name: str) -> str:
    value = cloud_run_env(name)
    if not value:
        value = getpass.getpass(f"{name} not in the Cloud Run env. Paste it (hidden), then Enter: ")
    return value.strip()


class Api:
    def __init__(self, admin_key: str):
        self._key = admin_key
        self._jar = http.cookiejar.CookieJar()
        self._open = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._jar)).open

    def call(self, method: str, path: str, params: dict = None, body: dict = None, key: bool = True):
        url = BASE + path + (("?" + urllib.parse.urlencode(params)) if params else "")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if key:
            req.add_header("X-Admin-Key", self._key)
        try:
            with self._open(req, timeout=180) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode("utf-8") or "{}")
            except Exception:
                return e.code, {}
        except Exception as e:
            return 0, {"error": type(e).__name__}

    def login(self, password: str) -> bool:
        status, _ = self.call("POST", "/api/admin/login", body={"password": password}, key=False)
        return status == 200


def section(title: str):
    print(LINE)
    print(title)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    live_reads = "--live-reads" in sys.argv
    if not args or not args[0].isdigit():
        print("Usage: python3 scripts/diagnose_google_grants.py CLIENT_ID [--live-reads]")
        sys.exit(1)
    cid = int(args[0])
    api = Api(secret("ADMIN_KEY"))
    print(f"client {cid} - reading (read-only{'' if not live_reads else ' + live reads'})...")

    # 1. stored rows
    section("1. STORED Google connections (client_accounts) - every row, newest id first")
    status, body = api.call("GET", f"/api/clients/{cid}/accounts")
    rows = sorted([r for r in (body.get("data") or []) if r.get("platform") in GOOGLE_PLATFORMS],
                  key=lambda r: -int(r.get("id") or 0)) if status == 200 else []
    if status != 200:
        print(f"   failed: HTTP {status}")
    for r in rows:
        print(f"   id={r.get('id')}  {r.get('platform'):<18} status={r.get('status'):<8} "
              f"account={scrub(r.get('account_id'))}  created={str(r.get('created_at') or '-')[:19]}  "
              f"updated={str(r.get('updated_at') or '-')[:19]}  has_secret={r.get('has_secret')}")
    for platform in GOOGLE_PLATFORMS:
        same = [r for r in rows if r.get("platform") == platform]
        if len(same) > 1:
            print(f"   !! {len(same)} rows for {platform}: readers use id={same[0].get('id')} (newest); "
                  f"a reconnect (upsert_account) may update another one")
    if not rows and status == 200:
        print("   (no Google rows)")

    # 2. timeline
    section("2. Connect / disconnect timeline + alert-dedup markers (client_activity, newest first)")
    status, body = api.call("GET", f"/api/clients/{cid}/activity", params={"limit": 500})
    if status != 200:
        print(f"   failed: HTTP {status}")
    for a in body.get("data") or []:
        action, details = a.get("action_type") or "", a.get("details") or {}
        marker = a.get("agent_name") == "chat_fallback" and details.get("issue_key")
        if not (action in TIMELINE_ACTIONS or marker or "google" in action or "gtm" in action
                or "youtube" in action or "disconnect" in action):
            continue
        keep = {k: details.get(k) for k in ("platform", "issue_key", "source_agent", "container_path", "public_id",
                                            "channel_id", "customer_id", "revoked", "via") if details.get(k) is not None}
        if isinstance(details.get("scopes"), list):
            keep["scopes"] = [str(s).rsplit("/", 1)[-1] for s in details["scopes"]]
        print(f"   {str(a.get('created_at') or '')[:19]}  {a.get('agent_name')}/{action}  {scrub(json.dumps(keep, ensure_ascii=False))}")

    # 3 + 5 need the admin session
    logged_in = api.login(secret("ADMIN_PASSWORD"))
    section("3. Each product's grant (newest row): real refresh + Google tokeninfo")
    if not logged_in:
        print("   admin login failed - skipped (sections 3 and 5 need ADMIN_PASSWORD)")
    else:
        status, body = api.call("GET", "/api/admin/google/scopes", params={"client_id": cid, "live": "true"}, key=False)
        d = body.get("data") or {}
        if status != 200:
            print(f"   failed: HTTP {status}")
        print(f"   the six scopes the code asks for: {', '.join(d.get('code_scopes') or [])}")
        for c in d.get("connections") or []:
            alive = "DEAD (invalid_grant / revoked / expired)" if c.get("error") == "grant_dead" else \
                (f"check failed: {c.get('error')}" if c.get("error") else
                 ("ALIVE" if c.get("source") == "google" else "not checked live"))
            print(f"   {c.get('platform'):<18} status={c.get('status')}  account={scrub(c.get('account'))}  -> {alive}")
            if c.get("scopes") is not None:
                print(f"      scopes ({c.get('source')}): {', '.join(c['scopes'])}")
                print(f"      missing for this feature: {c.get('missing_for_feature') or 'none'}   "
                      f"missing of the six: {c.get('missing_of_six') or 'none'}")

    # 4. harmless GTM read
    section("4. Tag Manager harmless read with the stored grant (versions.live)")
    status, body = api.call("GET", "/api/website/conversion-tracking", params={"client_id": cid})
    d = body.get("data") or {}
    if status == 200:
        print(f"   OK: gtm_api_connected={d.get('gtm_api_connected')}  configured_live={d.get('configured_live')}  "
              f"live_version={scrub(d.get('live_version_name', ''))}  live_types={d.get('live_types')}")
    else:
        print(f"   FAILED: HTTP {status}  {scrub(body.get('detail') or body)[:300]}")

    # 5. alerts
    section("5. Admin alerts that mention this client (newest 100 scanned)")
    if logged_in:
        status, body = api.call("GET", "/api/admin/alerts", key=False)
        rx = re.compile(rf"client[ _-]?{cid}\b|client_id[=:\s]+{cid}\b", re.IGNORECASE)
        hits = 0
        for al in body.get("data") or []:
            text = " ".join(str(al.get(k) or "") for k in ("message", "details", "title"))
            if rx.search(text):
                hits += 1
                print(f"   {str(al.get('created_at') or '')[:19]}  [{al.get('status')}] {al.get('source') or al.get('agent_name')}: "
                      f"{scrub(text)[:220]}")
        if not hits:
            print("   (none in the newest 100)")

    # 6. optional live reads
    if live_reads:
        section("6a. YouTube channels.list mine=true (+ uploads) - may add an admin alert if it fails")
        status, body = api.call("GET", "/api/youtube-content/engagement", params={"client_id": cid})
        d = body.get("data") or {}
        print(f"   HTTP {status}  connected={d.get('connected')}  error={scrub(d.get('error') or body.get('detail') or '')[:200]}"
              f"  videos_in_window={d.get('videos')}")
        section("6b. Google Ads listAccessibleCustomers - records grant health, alerts once if dead")
        if logged_in:
            status, body = api.call("GET", f"/api/admin/control-room/{cid}/google-ads/accounts",
                                    params={"refresh": "true"}, key=False)
            d = body.get("data") or {}
            print(f"   HTTP {status}  success={d.get('success')}  code={d.get('code', '')}  "
                  f"accounts={len(d.get('accounts') or [])}  current={d.get('current_display', '')}")
        else:
            print("   skipped (admin login failed)")
    print(LINE)


if __name__ == "__main__":
    main()
