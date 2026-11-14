"""Homepage v2 — a presentation layer behind a switch (docs/HOMEPAGE-V2.md).

The public homepage `/` is reviewed by Google (OAuth verification) and by
TikTok. So the new look ships DARK:

- Admin setting `homepage_v2_enabled`, default OFF. Off = the homepage is
  served by the very same static-file handler as before, from the very same
  file: byte-for-byte what the reviewers see today, nothing injected, no
  extra request. A deploy changes nothing on that page.
- On = the same HTML with two tags added (a stylesheet in <head>, a deferred
  script before </body>): dashboard/assets/home-v2.css + home-v2.js. They add
  motion and polish to elements that already exist. The URL, the title, the
  app name, the description of what the app does and of how Google data is
  used, and the links to /privacy/ and /terms/ are the same text in the same
  place — tests/test_homepage_v2.py compares the two documents.
- Preview: `/?home=v2` shows the new look for that request only, whatever the
  setting says — so the owner can look at it on production before anyone else
  can. A reviewer never adds that parameter.

The switch is read WITHOUT ever making the homepage wait: `enabled()` answers
from memory at once (OFF until proven otherwise) and refreshes in a background
thread at most once a minute. A slow or unreachable database therefore costs
the public page nothing — it just stays on the current look.
"""
import os
import threading
import time

SERVICE_NAME = "homepage_v2"
SETTING = "homepage_v2_enabled"
PREVIEW_PARAM, PREVIEW_VALUE = "home", "v2"
REFRESH_SECONDS = 60
HEAD_TAG = '<link rel="stylesheet" href="/assets/home-v2.css">'
BODY_TAG = '<script src="/assets/home-v2.js" defer></script>'

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE_PATH = os.path.join(BASE_DIR, "dashboard", "landing", "index.html")

_state = {"on": False, "at": 0.0, "refreshing": False}
_lock = threading.Lock()
_page = {"mtime": None, "html": ""}


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "on", "yes")


def _read_setting() -> bool:
    from core import admin_service
    return _truthy(admin_service.get_setting(SETTING, False, ttl=0))


def _refresh():
    try:
        value = _read_setting()
    except Exception as e:
        print(f"[{SERVICE_NAME}] setting not read ({type(e).__name__}) — keeping the current look")
        value = _state["on"]
    with _lock:
        _state.update({"on": bool(value), "at": time.monotonic(), "refreshing": False})


def enabled() -> bool:
    """The switch, from memory. Never blocks: a stale answer starts ONE
    background refresh and the current answer is returned as it is."""
    with _lock:
        stale = time.monotonic() - _state["at"] > REFRESH_SECONDS or not _state["at"]
        start = stale and not _state["refreshing"]
        if start:
            _state["refreshing"] = True
        value = _state["on"]
    if start:
        threading.Thread(target=_refresh, name="homepage-v2-flag", daemon=True).start()
    return value


def wants_preview(query_value) -> bool:
    return str(query_value or "") == PREVIEW_VALUE


def inject(html: str) -> str:
    """The same document with the two v2 tags. Nothing else changes; if the
    anchors are missing the document is returned untouched."""
    if "</head>" not in html or "</body>" not in html or HEAD_TAG in html:
        return html
    head_at = html.index("</head>")
    html = html[:head_at] + HEAD_TAG + "\n" + html[head_at:]
    body_at = html.rindex("</body>")
    return html[:body_at] + BODY_TAG + "\n" + html[body_at:]


def page() -> str:
    """The v2 document, rebuilt only when the file on disk changes."""
    mtime = os.path.getmtime(PAGE_PATH)
    if _page["mtime"] != mtime:
        with open(PAGE_PATH, encoding="utf-8") as f:
            _page["html"], _page["mtime"] = inject(f.read()), mtime
    return _page["html"]
