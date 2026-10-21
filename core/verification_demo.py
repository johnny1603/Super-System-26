"""Google verification demo: one real write each, on an INTERNAL test client.

For recording the OAuth verification video (docs/GOOGLE-SCOPES.md shot list):
a PAUSED test campaign in the client's Google Ads account, or a PRIVATE test
video on their YouTube channel. Each runs the REAL code path once —
google_ads_agent.create_search_campaign / youtube_content_agent.publish — so
the change can be shown inside Google Ads / YouTube Studio.

Two callers, one implementation: the Control Room buttons
(api_server /api/admin/control-room/{id}/demo/*) and the action layer
(core/agent_actions: demo_google_ads_campaign / demo_youtube_upload). Both are
refused here, not in the callers, for any client that is not billing_exempt
(internal), so no paying client's account is ever touched.
"""
import os
from datetime import datetime, timezone

from core.video_limits import VIDEO_UPLOAD_MAX_MB, fits

DEMO_PREFIX = "uallak verification test"


class DemoRefused(Exception):
    """A refusal with the HTTP status the endpoint answers with."""

    def __init__(self, status: int, detail):
        super().__init__(detail if isinstance(detail, str) else "; ".join(map(str, detail)))
        self.status = status
        self.detail = detail


def require_internal_test_client(client_id: int) -> dict:
    from agents.client_agent import get_client
    client = get_client(client_id)
    if not client:
        raise DemoRefused(404, "Client not found")
    if client.get("billing_exempt") is not True:
        raise DemoRefused(403, "Verification demo actions run only on an internal "
                               "(billing-exempt) test client")
    return client


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')


def create_test_campaign(client_id: int) -> dict:
    """Same function as POST /api/google-ads/create-campaign — created PAUSED,
    a 5 ILS/day budget that never spends unless someone enables it in Google
    Ads. One blocking googleAds:mutate."""
    require_internal_test_client(client_id)
    from agents.client_agent import get_accounts
    from agents.google_ads_agent import connection_info, create_search_campaign
    site = next((a.get("account_id") for a in get_accounts(client_id)
                 if a.get("platform") == "wordpress" and a.get("status") == "active"
                 and str(a.get("account_id") or "").startswith(("http://", "https://"))), "")
    name = f"{DEMO_PREFIX} {_stamp()}"
    result = create_search_campaign(client_id, {
        "name": name,
        "daily_budget_ils": 5,
        "final_url": site or os.environ.get("PUBLIC_APP_URL", "https://app.uallak.com"),
        "keywords": ["uallak verification test"],
        "headlines": ["uallak test campaign", "Created for verification", "Paused - not running"],
        "descriptions": ["Test campaign created by uallak for Google OAuth verification.",
                         "Created paused. It spends nothing unless enabled in Google Ads."],
    })
    if not result.get("success"):
        raise DemoRefused(400, result.get("errors", ["unknown error"]))
    info = connection_info(client_id) or {}
    return {"campaign_id": result.get("campaign_id", ""), "campaign_name": name,
            "status": result.get("status", "PAUSED"),
            "customer_id": info.get("display_id", ""), "account_name": info.get("name", "")}


def _demo_video_file(client_id: int) -> dict:
    """The smallest video in the client's own Drive media folder (root or one
    level down), within core/video_limits — the same source and limit a real
    publish uses. {} when there is none."""
    from core import drive_mirror, drive_service
    root = drive_mirror.client_root_id(client_id)
    if not root:
        return {}
    folders, candidates = [root], []
    for item in drive_service.list_children(root, page_size=100).get("files", []):
        if item.get("mimeType") == "application/vnd.google-apps.folder":
            folders.append(item["id"])
    for folder in folders:
        for f in drive_service.list_files(folder):
            if (f.get("mimeType") or "").startswith("video/") and fits(f.get("size")):
                candidates.append({**f, "size": int(f["size"])})
    return min(candidates, key=lambda f: f["size"]) if candidates else {}


def upload_test_video(client_id: int) -> dict:
    """Same function as POST /api/youtube-content/publish — the video lands
    PRIVATE, exactly as a real publish does. Drive download + resumable
    YouTube upload."""
    require_internal_test_client(client_id)
    try:
        video = _demo_video_file(client_id)
    except Exception as e:
        raise DemoRefused(502, f"Drive media folder could not be read: {e}")
    if not video:
        raise DemoRefused(400, f"No video up to {VIDEO_UPLOAD_MAX_MB} MB in this client's Drive media "
                               "folder - upload a short clip there first (Control Room → מדיה ב-Drive)")
    from agents.youtube_content_agent import publish
    title = f"{DEMO_PREFIX} {_stamp()}"
    result = publish(client_id, {"drive_file_id": video["id"], "title": title,
                                 "description": "Test upload by uallak for Google OAuth verification."})
    if not result.get("success"):
        raise DemoRefused(400, result.get("errors", ["unknown error"]))
    video_id = result.get("video_id", "")
    return {"video_id": video_id, "title": title, "privacy_status": "private",
            "source_file": video.get("name", ""),
            "studio_url": f"https://studio.youtube.com/video/{video_id}/edit" if video_id else ""}
