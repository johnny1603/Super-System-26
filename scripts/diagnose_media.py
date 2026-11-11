#!/usr/bin/env python3
"""READ-ONLY media diagnosis for one client, run in Cloud Shell:

    python3 scripts/diagnose_media.py 27 "test 3"

Prints, for the client:
  1. the stored folder id (the media_folder_created record) and the folder
     the system resolves and uses now (drive_mirror.shared_client_root);
  2. the RAW tree of EVERY folder named "client-27 …" the service account
     can see — which one is connected, file counts, each file with its
     sub-folder, size and date (up to 300 files per folder);
  3. what the find tools return for the name: inside the client's folder
     (what the agents' find_media_file and the Media tab search see) and
     everywhere the service account can see (attach existing file).

It calls GET /api/admin/drive/media-diagnose (core/drive_merge.media_diagnosis)
on the live service with the ADMIN_KEY read from the Cloud Run env (or typed,
hidden). Never prints the key, a token or a file body. Changes nothing.
"""
import getpass
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

PROJECT = "super-system-500410"
SERVICE = "super-system"
REGION = "me-west1"
BASE = "https://app.uallak.com"


def admin_key() -> str:
    try:
        raw = subprocess.run(["gcloud", "run", "services", "describe", SERVICE, "--region", REGION,
                              f"--project={PROJECT}", "--format=json"],
                             capture_output=True, text=True, timeout=60).stdout
        env = json.loads(raw)["spec"]["template"]["spec"]["containers"][0].get("env", [])
        key = next((e.get("value", "") for e in env if e.get("name") == "ADMIN_KEY"), "")
    except Exception:
        key = ""
    if not key:
        key = getpass.getpass("ADMIN_KEY not in the Cloud Run env. Paste it (hidden), then Enter: ")
    return key.strip()


def mb(n) -> str:
    return f"{n / 1048576:.1f} MB" if isinstance(n, (int, float)) and n else "-"


def main():
    if len(sys.argv) < 2 or not sys.argv[1].isdigit():
        print('Usage: python3 scripts/diagnose_media.py CLIENT_ID ["file name to find"]')
        sys.exit(1)
    client_id, find = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "test 3")
    key = admin_key()
    if not key:
        print("No ADMIN_KEY - nothing read.")
        sys.exit(1)
    url = f"{BASE}/api/admin/drive/media-diagnose?" + urllib.parse.urlencode({"client_id": client_id, "find": find})
    request = urllib.request.Request(url, headers={"X-Admin-Key": key})
    del key
    print(f"Reading client {client_id} from Drive (can take a minute)...")
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:400]}")
        sys.exit(1)
    d = body.get("data") or {}
    line = "-" * 78
    print(line)
    print(f"client {d.get('client_id')}   storage: {d.get('location')}   service account: {d.get('service_account')}")
    print(f"media root (DRIVE_MEDIA_FOLDER_ID): {d.get('media_root')}")
    print(f"STORED folder id (first media_folder_created record): {d.get('stored_folder_id') or '(none)'}")
    print(f"RESOLVED folder id (what the system reads and writes now): {d.get('resolved_folder_id') or '(none)'}")
    if d.get("error"):
        print(f"error: {d['error']}")
    if d.get("folders_error"):
        print(f"folder search failed: {d['folders_error']}")
    folders = d.get("folders_named_like_client") or []
    print(line)
    print(f"{len(folders)} folder(s) named like the client:")
    for f in folders:
        print(line)
        flag = "CONNECTED" if f.get("connected") else "not connected"
        where = "under the media root" if f.get("under_media_root") else "OUTSIDE the media root"
        print(f"[{flag}] {f.get('name')}  id={f.get('id')}  ({where}, created {f.get('created')})")
        print(f"   parents={f.get('parents')}  shared drive={f.get('shared_drive') or '-'}")
        if f.get("error"):
            print(f"   could not list: {f['error']}")
            continue
        print(f"   files={f.get('files')}  subfolders={f.get('folders')}  size={mb(f.get('bytes'))}"
              f"{'  (stopped at 300 files)' if f.get('truncated') else ''}")
        for item in f.get("tree") or []:
            print(f"   - {item.get('folder') or '/'} :: {item.get('name')}  [{item.get('mime_type')}]  "
                  f"{mb(item.get('size'))}  {str(item.get('modified_time') or '')[:10]}  id={item.get('id')}")
    print(line)
    print(f'find "{find}" INSIDE the client\'s connected folder (agents + Media tab):')
    if d.get("find_in_client_folder_error"):
        print(f"   failed: {d['find_in_client_folder_error']}")
    for m in d.get("find_in_client_folder") or []:
        print(f"   - {m.get('name')}  in {m.get('folder') or '/'}  {mb(m.get('size'))}  "
              f"{str(m.get('modified_time') or '')[:10]}  id={m.get('id')}")
    if not d.get("find_in_client_folder") and not d.get("find_in_client_folder_error"):
        print("   (nothing)")
    print(f'find "{find}" EVERYWHERE the service account can see (attach existing file):')
    anywhere = d.get("find_anywhere") or {}
    if d.get("find_anywhere_error"):
        print(f"   failed: {d['find_anywhere_error']}")
    for c in anywhere.get("candidates") or []:
        print(f"   - {c.get('name')}  in {c.get('folder') or '?'}  {mb(c.get('size'))}  "
              f"{str(c.get('modified_time') or '')[:10]}  "
              f"{'(already in the client folder)' if c.get('in_client_folder') else ''}  id={c.get('id')}")
    if not anywhere.get("candidates") and not d.get("find_anywhere_error"):
        print("   (nothing)")
    print(line)


if __name__ == "__main__":
    main()
