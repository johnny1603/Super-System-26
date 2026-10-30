"""Google verification ROUND 2 switches (docs/GOOGLE-ROUND2.md). All OFF by
default: while off, the round-2 scopes are never requested, their OAuth
routes answer 404, their cards and chat actions are never offered, and no
round-2 API is called. Round 1's scope lists (sign-in, Ads, YouTube, Tag
Manager) do not depend on these at all.

Read from the environment at call time (no restart-time caching), stdlib
only so any module and any test can ask cheaply.
"""
import os

_TRUE = ("1", "true", "yes", "on")


def gmail_send_enabled() -> bool:
    return os.environ.get("GMAIL_SEND_ENABLED", "").strip().lower() in _TRUE


def search_console_enabled() -> bool:
    return os.environ.get("SEARCH_CONSOLE_ENABLED", "").strip().lower() in _TRUE


def analytics_enabled() -> bool:
    """analytics.readonly — GA4 site traffic (core/analytics_service)."""
    return os.environ.get("ANALYTICS_ENABLED", "").strip().lower() in _TRUE


def drive_file_enabled() -> bool:
    """drive.file — the client's files in their OWN Google Drive
    (core/drive_file_service, core/drive_storage)."""
    return os.environ.get("DRIVE_FILE_ENABLED", "").strip().lower() in _TRUE
