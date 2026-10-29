"""Registered-domain matching — ONE implementation for every "is this the same
business's domain?" check (YouTube description links, outreach suppression,
e-mail discovery). Stdlib only.

eTLD+1 with the two-label public suffixes we actually meet (Israel first:
co.il, org.il, ac.il, gov.il, muni.il…). Not a full Public Suffix List — a
host under an unlisted multi-label suffix falls back to its last two labels,
which is the safe direction (it groups too much, never too little, for an
allowlist check that also requires exact page membership)."""
from urllib.parse import urlsplit

_SECOND_LEVEL = {"co", "org", "net", "ac", "gov", "muni", "k12", "com", "edu", "ltd", "plc", "idf"}


def registered_domain(host: str) -> str:
    labels = [l for l in (host or "").lower().strip(".").split(".") if l]
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def url_domain(url: str) -> str:
    return registered_domain(urlsplit(url or "").hostname or "")


def email_domain(email: str) -> str:
    return registered_domain((email or "").rsplit("@", 1)[-1]) if "@" in (email or "") else ""
