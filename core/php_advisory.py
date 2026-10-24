"""PHP2Shell — the ONE place the advisory's facts live (core/php_exposure reads
them; nothing else hardcodes a PHP version).

CVE-2026-109618 + CVE-2026-109619: remote code execution and memory
disclosure (data from OTHER requests — which on a site we manage includes the
Basic-auth header carrying our Application Password) in the PHP runtime
itself. Fixed upstream in the releases below; PHP 8.1 and older get no
official release (only unreleased source backports, and distro/vendor builds
that may carry them). Updating WordPress, a plugin or a theme does NOT fix it
— only the host updating PHP does. See docs/INCIDENT-PHP2SHELL.md.

Facts as published by the PHP project and summarised by CERT advisories;
recorded 2026-10-24. Change them here only.
"""

NAME = "PHP2Shell"
CVES = ("CVE-2026-109618", "CVE-2026-109619")
PUBLIC_SINCE = "2026-10-21"                 # fixed releases + advisories public by this date
EXPLOITATION_REPORTED_SINCE = "2026-10-21"
RECORDED_ON = "2026-10-24"

# (major, minor) -> first FIXED patch release on that branch
FIXED_RELEASES = {
    (8, 5): 12,
    (8, 4): 27,
    (8, 3): 36,
    (8, 2): 35,
}

# Branches below this have no official fixed release at all
OLDEST_FIXED_BRANCH = (8, 2)


def fixed_versions_text() -> str:
    """'8.5.12 / 8.4.27 / 8.3.36 / 8.2.35' — for alerts and the panel."""
    return " / ".join(f"{major}.{minor}.{patch}"
                      for (major, minor), patch in sorted(FIXED_RELEASES.items(), reverse=True))
