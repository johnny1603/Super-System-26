"""Landing pages — lightweight, code-built, conversion-focused. Data + render.

## What this is NOT

`website_agent` builds the client's MAIN site: WordPress, InstaWP-hosted, a
full business presence with navigation and depth. A landing page is the
opposite shape — one offer, one form, no navigation, fast — and it is built in
code here. The two share no table, no renderer and no hosting path. Same
discipline as `leads` vs `client_leads`: similar words, opposite jobs.

## Hosting: ONE shared route, never a project per page

Every client's pages are served by THIS app at
`/lp/{client_slug}/{page_slug}`. There is no per-page or per-client hosting
project, which is what makes this scale past ~30 clients — the Cloudflare
Pages project-count ceiling that shaped this decision is avoided by not using
Pages at all. Custom client domains are layered ON TOP of the same single
route (see `core/landing_domains.py`), never by duplicating the serving path.

## Structured content only — a SECURITY boundary, not a style choice

`content` holds strings (headline, sections, CTA), and `render_page()` escapes
every one of them into a fixed template. Raw HTML is never stored and never
rendered. These pages are served from **app.uallak.com — the same origin as
the client dashboard and its session cookie** — so stored raw HTML would be
stored XSS against every logged-in client and admin. A "custom HTML" feature
would have to serve from a different origin; do not relax this here.

## Lead capture reuses the existing endpoint, unchanged

The form posts to `POST /api/leads/capture/{token}` with the SAME
`create_lead_capture_token` the WordPress auto-wiring uses — no second capture
path, no second token scheme. Per-page attribution rides on `source_detail`
(`lp:{slug}`), which `client_leads` already stores and filters on.

## Versions (2026-11-01, docs/LANDING_PAGES.md)

Every edit — generated copy, a rewrite, a client edit, a revert — is a NEW
row in landing_page_versions; nothing is overwritten. `published_version`
says which one is LIVE, so editing a live page never changes what visitors
see until the client approves the new version (the publish card). Revert
copies an old version into a new one and, on a live page, puts it live at
once (the client's tap is the approval). Before
migrations/2026-11-01-landing-own.sql runs, all of this degrades to the old
single-content behaviour — `_versions_ok` turns off on the first
missing-table error.

## Sanitized twice

Every string is stripped of tags, control characters and script URLs on the
WAY IN (normalize_content / normalize_contact), and escaped on the way OUT
(render). Either alone would hold; both is the rule for text that an LLM or a
client wrote and a stranger's browser renders.
"""
import html
import json
import os
import re
from datetime import datetime, timezone

SERVICE_NAME = "landing_pages"

# Every client gets this many, included in the base package regardless of tier
# (a business decision, mirrored in the dashboard copy). The 4th is NOT priced
# here or anywhere in code — see agents/landing_page_agent.request_extra_page.
MAX_PAGES_PER_CLIENT = 3

STATUSES = ("draft", "published")

MAX_SLUG_CHARS = 60
MAX_TITLE_CHARS = 120
MAX_GOAL_CHARS = 300

# Content field caps. Long landing pages convert worse and cost more to
# generate; these are product limits, enforced on write.
MAX_HEADLINE_CHARS = 90
MAX_SUB_CHARS = 200
MAX_BENEFITS = 6
MAX_BENEFIT_CHARS = 140
MAX_SECTIONS = 4
MAX_SECTION_TITLE_CHARS = 90
MAX_SECTION_BODY_CHARS = 600
MAX_CTA_CHARS = 40

# Invisible limits (docs/LANDING_PAGES.md §6): pages live at once, and the size
# of one rendered page. The client never sees a counter; the admin is alerted
# when a client reaches the live-page limit (landing_page_agent).
MAX_ACTIVE_PAGES = MAX_PAGES_PER_CLIENT
MAX_PAGE_BYTES = 64_000
MAX_URL_CHARS = 300

_db_instance = None
_versions_ok = {"value": True}


def _db():
    global _db_instance
    if _db_instance is None:
        from supabase import create_client
        _db_instance = create_client(os.environ["SUPABASE_URL"],
                                     os.environ["SUPABASE_SERVICE_KEY"])
    return _db_instance


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# A tag starts with a letter, / ! or ? — so "מחיר < 100" survives, "<script" does not
_TAG = re.compile(r"<[a-zA-Z/!?][^>]*>?")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f‎‏‪-‮⁦-⁩]")
_SCRIPT_URL = re.compile(r"(?i)\b(javascript|vbscript|data)\s*:")


def sanitize_text(value) -> str:
    """Plain text only: no tags, no control/bidi-override characters, no
    script URLs. Applied to everything an LLM or a client wrote, BEFORE it is
    stored — render() escapes again on the way out."""
    text = html.unescape(str(value or ""))
    text = _TAG.sub("", text)
    text = _CONTROL.sub("", text)
    text = _SCRIPT_URL.sub("", text)
    return text


def _clip(value, limit: int) -> str:
    return sanitize_text(value).strip()[:limit]


# ─── Slugs ────────────────────────────────────────────────────────────────────

def slugify(raw: str, fallback: str = "page") -> str:
    """URL-safe ASCII slug. Hebrew titles are the norm here and transliterating
    them properly is a problem this does not need to solve — a Hebrew title
    simply yields the fallback plus a suffix, and the client never types or
    reads these slugs (they see the full URL, and the page title carries the
    meaning)."""
    text = (raw or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return (text or fallback)[:MAX_SLUG_CHARS]


def client_slug(client_id: int, business_name: str = "") -> str:
    """The client's own URL segment. Always ends in the client_id, so it is
    unique without a lookup and stays stable if the business is renamed."""
    base = slugify(business_name, fallback="")
    return f"{base}-{client_id}".strip("-") if base else f"c{client_id}"


# Paths the client's legal pages use under the same /lp/{client}/ prefix — a
# page can never take one of these slugs.
RESERVED_SLUGS = {"privacy", "terms"}


def _unique_slug(client_id: int, desired: str) -> str:
    existing = {row["slug"] for row in
                (_db().table("landing_pages").select("slug")
                 .eq("client_id", client_id).execute().data or [])} | RESERVED_SLUGS
    if desired not in existing:
        return desired
    for suffix in range(2, 50):
        candidate = f"{desired}-{suffix}"[:MAX_SLUG_CHARS]
        if candidate not in existing:
            return candidate
    return f"{desired}-{int(datetime.now(timezone.utc).timestamp())}"[:MAX_SLUG_CHARS]


# ─── Content normalization (the write-side gate) ──────────────────────────────

def normalize_content(raw: dict) -> dict:
    """Coerce whatever a caller (or an LLM) produced into the exact shape the
    template renders, with every field length-capped. Unknown keys are DROPPED
    — the renderer only ever reads these, so an extra key could never surface,
    and silently keeping it would suggest otherwise."""
    from core import landing_images
    raw = raw if isinstance(raw, dict) else {}
    benefits = [_clip(b, MAX_BENEFIT_CHARS) for b in (raw.get("benefits") or [])]
    sections = []
    for section in (raw.get("sections") or [])[:MAX_SECTIONS]:
        if not isinstance(section, dict):
            continue
        title = _clip(section.get("title"), MAX_SECTION_TITLE_CHARS)
        body = _clip(section.get("body"), MAX_SECTION_BODY_CHARS)
        if title or body:
            sections.append({"title": title, "body": body})
    return {
        "headline": _clip(raw.get("headline"), MAX_HEADLINE_CHARS),
        "subheadline": _clip(raw.get("subheadline"), MAX_SUB_CHARS),
        "benefits": [b for b in benefits if b][:MAX_BENEFITS],
        "sections": sections,
        "cta_text": _clip(raw.get("cta_text"), MAX_CTA_CHARS) or "שליחה",
        "form_note": _clip(raw.get("form_note"), MAX_SUB_CHARS),
        # References to OUR stored copies (core/landing_images) — never a URL
        "images": landing_images.normalize_refs(raw.get("images")),
    }


def _digits(value, lo: int, hi: int) -> str:
    raw = sanitize_text(value).strip()
    digits = re.sub(r"[^\d]", "", raw)
    if not lo <= len(digits) <= hi:
        return ""
    return ("+" if raw.startswith("+") else "") + digits


def safe_url(value) -> str:
    """https only, no credentials, no spaces, length-capped — else ""."""
    from urllib.parse import urlparse
    url = sanitize_text(value).strip()
    if not url or len(url) > MAX_URL_CHARS or any(c.isspace() for c in url):
        return ""
    parts = urlparse(url)
    if parts.scheme != "https" or not parts.hostname or "@" in parts.netloc or "." not in parts.hostname:
        return ""
    return url


def normalize_contact(raw: dict) -> dict:
    """The page's action links: phone (click-to-call), WhatsApp number,
    store URL (a store client's buy button). Anything invalid is dropped."""
    raw = raw if isinstance(raw, dict) else {}
    out = {"phone": _digits(raw.get("phone"), 7, 15),
           "whatsapp": _digits(raw.get("whatsapp"), 9, 15).lstrip("+"),
           "store_url": safe_url(raw.get("store_url"))}
    return {k: v for k, v in out.items() if v}


# ─── CRUD ─────────────────────────────────────────────────────────────────────

def count_pages(client_id: int) -> int:
    result = (_db().table("landing_pages").select("id", count="exact")
              .eq("client_id", client_id).limit(1).execute())
    return result.count or 0


def list_pages(client_id: int) -> list:
    return (_db().table("landing_pages").select("*")
            .eq("client_id", client_id)
            .order("created_at", desc=True).execute().data or [])


def get_page(client_id: int, page_id: int) -> dict:
    rows = (_db().table("landing_pages").select("*")
            .eq("client_id", client_id).eq("id", page_id).limit(1).execute().data or [])
    return rows[0] if rows else {}


def get_by_slug(client_id: int, slug: str) -> dict:
    rows = (_db().table("landing_pages").select("*")
            .eq("client_id", client_id).eq("slug", slug).limit(1).execute().data or [])
    return rows[0] if rows else {}


def create_page(client_id: int, title: str, goal: str = "", content: dict = None,
                slug: str = "", copy_source: str = "") -> dict:
    """Create ONE page. The MAX_PAGES_PER_CLIENT ceiling is enforced HERE — the
    server side — so the dashboard's own check is a courtesy, not the control.
    Returns {"success": bool, "code": str, "page": dict}."""
    if count_pages(client_id) >= MAX_PAGES_PER_CLIENT:
        return {"success": False, "code": "ERR_LANDING_PAGE_LIMIT",
                "errors": [f"client already has {MAX_PAGES_PER_CLIENT} landing pages "
                           f"(the number included in every package)"]}
    title = _clip(title, MAX_TITLE_CHARS)
    if not title:
        return {"success": False, "code": "ERR_LANDING_TITLE_REQUIRED",
                "errors": ["title is required"]}

    row = {
        "client_id": client_id,
        "slug": _unique_slug(client_id, slugify(slug or title)),
        "title": title,
        "goal": _clip(goal, MAX_GOAL_CHARS),
        "content": normalize_content(content or {}),
        "status": "draft",  # draft-first, same principle as website_agent
        "copy_source": _clip(copy_source, 40),
    }
    created = _db().table("landing_pages").insert(row).execute()
    return {"success": True, "code": "", "page": (created.data or [row])[0]}


def update_page(client_id: int, page_id: int, fields: dict, source: str = "client_edit") -> dict:
    """Only title / goal / content / contact are writable here; anything else
    in `fields` is ignored, so a bad payload can never flip client_id, rewrite
    a slug into a collision or publish a page (publishing is publish(), behind
    the client's approval card). A change to what visitors would see (title,
    content, contact) becomes a NEW version — the live one stays live."""
    changes = {}
    if "title" in fields:
        changes["title"] = _clip(fields["title"], MAX_TITLE_CHARS)
    if "goal" in fields:
        changes["goal"] = _clip(fields["goal"], MAX_GOAL_CHARS)
    if "content" in fields:
        changes["content"] = normalize_content(fields["content"])
    if "contact" in fields:
        changes["contact"] = normalize_contact(fields["contact"])
    if not changes:
        return {"success": False, "code": "ERR_LANDING_NO_FIELDS",
                "errors": ["nothing to update"]}
    page = get_page(client_id, page_id)
    if not page:
        return {"success": False, "code": "ERR_LANDING_NOT_FOUND", "errors": ["page not found"]}
    if "content" in changes:
        if not isinstance(fields["content"], dict) or "images" not in fields["content"]:
            # A text edit (the dashboard editor, a rewrite) keeps the photos
            changes["content"]["images"] = normalize_content(page.get("content") or {})["images"]
        refs = changes["content"]["images"]
        if refs:
            from core import landing_images
            known = landing_images.sizes(client_id, [r["id"] for r in refs])
            if set(known) != {r["id"] for r in refs}:
                # An id that is not THIS client's never reaches a version
                return {"success": False, "code": "ERR_LANDING_IMAGE_NOT_FOUND", "errors": ["image"]}
            if sum(known.values()) > landing_images.MAX_PAGE_IMAGE_BYTES:
                return {"success": False, "code": "ERR_LANDING_IMAGES_PAGE_TOO_LARGE",
                        "errors": [f"{sum(known.values())} bytes"]}
    visible = {k: changes[k] for k in ("title", "content", "contact") if k in changes}
    if visible:
        merged = {"title": page.get("title", ""), "content": page.get("content") or {},
                  "contact": page.get("contact") or {}, **visible}
        size = page_size({**page, **merged})
        if size > MAX_PAGE_BYTES:
            return {"success": False, "code": "ERR_LANDING_TOO_LARGE", "errors": [f"{size} bytes"]}
        version = _add_version(client_id, page_id, merged, source, size)
        if version:
            changes["current_version"] = version
    changes["updated_at"] = _now()
    try:
        result = (_db().table("landing_pages").update(changes)
                  .eq("id", page_id).eq("client_id", client_id).execute())
    except Exception as e:
        # Before the migration: the new columns don't exist yet — keep the old behaviour
        if not any(col in str(e) for col in ("current_version", "contact")):
            raise
        changes = {k: v for k, v in changes.items() if k not in ("current_version", "contact")}
        result = (_db().table("landing_pages").update(changes)
                  .eq("id", page_id).eq("client_id", client_id).execute())
    if not result.data:
        return {"success": False, "code": "ERR_LANDING_NOT_FOUND",
                "errors": ["page not found"]}
    return {"success": True, "code": "", "page": result.data[0]}


# ─── Versions ────────────────────────────────────────────────────────────────

def _missing_table(error: Exception) -> bool:
    text = str(error)
    return "landing_page_versions" in text or "42P01" in text or "PGRST205" in text \
        or "current_version" in text or "published_version" in text


def _add_version(client_id: int, page_id: int, visible: dict, source: str, size: int = 0) -> int:
    """Store one version; returns its number (0 when versions aren't set up yet)."""
    if not _versions_ok["value"]:
        return 0
    try:
        rows = (_db().table("landing_page_versions").select("version").eq("page_id", page_id)
                .eq("client_id", client_id).order("version", desc=True).limit(1).execute().data or [])
        number = (rows[0]["version"] if rows else 0) + 1
        _db().table("landing_page_versions").insert({
            "page_id": page_id, "client_id": client_id, "version": number,
            "title": visible.get("title", ""), "content": visible.get("content") or {},
            "contact": visible.get("contact") or {}, "source": _clip(source, 20), "size_bytes": int(size or 0),
        }).execute()
        return number
    except Exception as e:
        if _missing_table(e):
            _versions_ok["value"] = False
            print(f"[{SERVICE_NAME}] versions off until migrations/2026-11-01-landing-own.sql runs")
            return 0
        raise


def list_versions(client_id: int, page_id: int) -> list:
    if not _versions_ok["value"]:
        return []
    try:
        return (_db().table("landing_page_versions").select("version, source, created_at, title, size_bytes")
                .eq("client_id", client_id).eq("page_id", page_id)
                .order("version", desc=True).limit(50).execute().data or [])
    except Exception as e:
        if _missing_table(e):
            _versions_ok["value"] = False
            return []
        raise


def get_version(client_id: int, page_id: int, version: int) -> dict:
    """One version, scoped by client AND page — another client's id never resolves."""
    if not _versions_ok["value"] or not version:
        return {}
    try:
        rows = (_db().table("landing_page_versions").select("*").eq("client_id", client_id)
                .eq("page_id", page_id).eq("version", int(version)).limit(1).execute().data or [])
    except Exception as e:
        if _missing_table(e):
            _versions_ok["value"] = False
            return {}
        raise
    return rows[0] if rows else {}


def visible_of(client_id: int, page: dict, version: int = None) -> dict:
    """What a visitor sees for `version` (default: the LIVE one): title,
    content, contact. Falls back to the page row itself — before the
    migration, and for a page whose live version predates versioning."""
    number = version if version is not None else page.get("published_version")
    row = get_version(client_id, page.get("id"), number) if number else {}
    src = row or page
    return {"title": src.get("title") or page.get("title", ""),
            "content": normalize_content(src.get("content") or {}),
            "contact": normalize_contact(src.get("contact") or {}),
            "version": row.get("version") if row else page.get("current_version") or 0}


def count_active(client_id: int) -> int:
    result = (_db().table("landing_pages").select("id", count="exact")
              .eq("client_id", client_id).eq("status", "published").limit(1).execute())
    return result.count or 0


def publish(client_id: int, page_id: int, version: int = None) -> dict:
    """Put `version` (default: the newest) live. Only ever called from the
    client's approval of the publish card, a revert tap, or the admin.
    Refuses past the invisible live-page limit and the size limit."""
    page = get_page(client_id, page_id)
    if not page:
        return {"success": False, "code": "ERR_LANDING_NOT_FOUND"}
    number = int(version or page.get("current_version") or 0)
    if number and _versions_ok["value"] and not get_version(client_id, page_id, number):
        return {"success": False, "code": "ERR_LANDING_NO_VERSION"}
    if page.get("status") != "published" and count_active(client_id) >= MAX_ACTIVE_PAGES:
        return {"success": False, "code": "ERR_LANDING_ACTIVE_LIMIT"}
    visible = visible_of(client_id, page, number or None)
    if page_size({**page, **visible}) > MAX_PAGE_BYTES:
        return {"success": False, "code": "ERR_LANDING_TOO_LARGE"}
    changes = {"status": "published", "updated_at": _now(), "unpublished_reason": ""}
    if number:
        changes.update(published_version=number, published_at=_now())
    try:
        result = (_db().table("landing_pages").update(changes)
                  .eq("id", page_id).eq("client_id", client_id).execute())
    except Exception as e:
        if not _missing_table(e) and "unpublished_reason" not in str(e) and "published_at" not in str(e):
            raise
        result = (_db().table("landing_pages").update({"status": "published", "updated_at": _now()})
                  .eq("id", page_id).eq("client_id", client_id).execute())
    if not result.data:
        return {"success": False, "code": "ERR_LANDING_NOT_FOUND"}
    return {"success": True, "code": "", "page": result.data[0], "version": number}


def revert(client_id: int, page_id: int, version: int) -> dict:
    """One tap: version N comes back as a NEW version; a live page goes live
    with it at once."""
    page = get_page(client_id, page_id)
    old = get_version(client_id, page_id, version) if page else {}
    if not old:
        return {"success": False, "code": "ERR_LANDING_NO_VERSION"}
    # The old version's photos exactly (none, for a version from before photos)
    old_content = {**(old.get("content") or {}), "images": (old.get("content") or {}).get("images") or []}
    updated = update_page(client_id, page_id, {"title": old.get("title"), "content": old_content,
                                               "contact": old.get("contact")}, source=f"revert:{version}")
    if not updated.get("success"):
        return updated
    new_number = updated["page"].get("current_version")
    if page.get("status") == "published":
        return {**publish(client_id, page_id, new_number), "reverted_to": version}
    return {"success": True, "code": "", "page": updated["page"], "reverted_to": version, "version": new_number}


def unpublish(client_id: int, page_id: int, reason: str = "") -> dict:
    """Instant: the next request for the page answers 404."""
    changes = {"status": "draft", "updated_at": _now(), "unpublished_reason": _clip(reason, 120)}
    try:
        result = (_db().table("landing_pages").update(changes)
                  .eq("id", page_id).eq("client_id", client_id).execute())
    except Exception as e:
        if "unpublished_reason" not in str(e):
            raise
        changes.pop("unpublished_reason")
        result = (_db().table("landing_pages").update(changes)
                  .eq("id", page_id).eq("client_id", client_id).execute())
    if not result.data:
        return {"success": False, "code": "ERR_LANDING_NOT_FOUND"}
    return {"success": True, "code": "", "page": result.data[0]}


def delete_page(client_id: int, page_id: int) -> bool:
    result = (_db().table("landing_pages").delete()
              .eq("id", page_id).eq("client_id", client_id).execute())
    return bool(result.data)


# ─── Lead attribution ─────────────────────────────────────────────────────────

SOURCE_DETAIL_PREFIX = "lp:"


def source_detail_for(slug: str) -> str:
    """What the page's form stamps on the lead so the dashboard can say WHICH
    page produced it. Rides on client_leads.source_detail, which already
    exists, is already stored and is already searchable — no schema change."""
    return f"{SOURCE_DETAIL_PREFIX}{slug}"


def lead_counts_by_page(client_id: int) -> dict:
    """{slug: lead_count} from the rows client_leads already stores. Counted in
    Python over one scoped read rather than a per-page aggregate query: at SMB
    volume one round trip beats three."""
    rows = (_db().table("client_leads").select("source_detail")
            .eq("client_id", client_id).limit(5000).execute().data or [])
    counts = {}
    for row in rows:
        detail = row.get("source_detail") or ""
        if detail.startswith(SOURCE_DETAIL_PREFIX):
            slug = detail[len(SOURCE_DETAIL_PREFIX):]
            counts[slug] = counts.get(slug, 0) + 1
    return counts


# ─── Render ───────────────────────────────────────────────────────────────────

def _e(value) -> str:
    """Escape for HTML text/attribute context. Every dynamic value in the
    template goes through this — see the module docstring on why."""
    return html.escape(str(value or ""), quote=True)


PAGE_CSS = """
:root{--ink:#12171f;--muted:#5a6472;--line:#e3e7ec;--bg:#fff;--accent:#1f6feb;--accent-ink:#fff}
@media (prefers-color-scheme:dark){:root{--ink:#eef2f7;--muted:#9aa6b6;--line:#252b34;--bg:#0e1116}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:'Heebo','Assistant',system-ui,-apple-system,'Segoe UI',Arial,sans-serif;line-height:1.6}
.wrap{max-width:720px;margin:0 auto;padding:40px 20px 64px}
h1{font-size:clamp(26px,5vw,40px);line-height:1.2;margin:0 0 12px}
.sub{font-size:clamp(16px,2.4vw,19px);color:var(--muted);margin:0 0 28px}
ul.benefits{list-style:none;padding:0;margin:0 0 32px}
ul.benefits li{padding:9px 0 9px 0;border-bottom:1px solid var(--line);font-size:16px}
ul.benefits li::before{content:"✓";color:var(--accent);font-weight:700;margin-inline-end:10px}
section.block{margin:0 0 28px}
section.block h2{font-size:20px;margin:0 0 8px}
section.block p{margin:0;color:var(--muted)}
form{background:transparent;border:1px solid var(--line);border-radius:14px;padding:20px;margin-top:8px}
label{display:block;font-size:14px;font-weight:600;margin:0 0 6px}
input,textarea{width:100%;padding:12px 13px;margin:0 0 16px;border:1px solid var(--line);border-radius:9px;background:var(--bg);color:var(--ink);font:inherit}
textarea{min-height:96px;resize:vertical}
button{width:100%;padding:14px;border:0;border-radius:9px;background:var(--accent);color:var(--accent-ink);font:inherit;font-weight:700;font-size:17px;cursor:pointer}
.note{font-size:13px;color:var(--muted);margin:12px 0 0}
.sent{background:rgba(31,111,235,.1);border:1px solid var(--accent);border-radius:11px;padding:16px;margin:0 0 24px;font-weight:600}
.actions{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 28px}
.actions a{flex:1 1 140px;display:block;text-align:center;padding:13px 8px;white-space:nowrap;border-radius:9px;border:1px solid var(--accent);color:var(--accent);font-weight:700;text-decoration:none;min-height:48px}
.actions a.wa{border-color:#1f9d55;color:#1f9d55}
.hp{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}
.consent{font-size:12.5px;color:var(--muted);margin:12px 0 0}
.consent a,footer a{color:inherit}
footer{margin-top:40px;font-size:12px;color:var(--muted);text-align:center}
footer nav{margin-bottom:6px}
footer nav a{margin:0 8px}
img{max-width:100%;height:auto;display:block}
img.hero{width:100%;border-radius:14px;margin:0 0 22px;object-fit:cover;max-height:440px}
header.bg{border-radius:14px;padding:44px 22px;margin:0 0 26px;background-size:cover;background-position:center;color:#fff}
header.bg .sub{color:#f1f3f6}
section.block img{border-radius:11px;margin:0 0 12px;width:100%;object-fit:cover;max-height:360px}
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin:0 0 28px}
.gallery img{width:100%;aspect-ratio:1/1;object-fit:cover;border-radius:10px}
.legal h1{font-size:26px}
.legal p,.legal li{color:var(--ink)}
"""

# Tag Manager runs ONLY on the client's own domain, never on app.uallak.com:
# a third-party container on our origin could act as a logged-in client or
# admin (see the module docstring). Our own counters work everywhere.
GTM_ID = re.compile(r"^GTM-[A-Z0-9]{4,12}$")


def _gtm_head(container: str) -> str:
    if not GTM_ID.match(container or ""):
        return ""
    return ("<script>window.dataLayer=window.dataLayer||[];"
            "(function(w,d,s,l,i){w[l].push({'gtm.start':new Date().getTime(),event:'gtm.js'});"
            "var f=d.getElementsByTagName(s)[0],j=d.createElement(s);j.async=true;"
            f"j.src='https://www.googletagmanager.com/gtm.js?id='+i;f.parentNode.insertBefore(j,f);"
            f"}})(window,document,'script','dataLayer','{container}');</script>")


# The events pushed to the client's dataLayer (their Tag Manager forwards
# them to Analytics / Ads). Names follow GA4's recommended events.
TRACKING_EVENTS = {"view": "page_view", "lead": "generate_lead", "call": "click_to_call",
                   "whatsapp": "click_whatsapp", "store": "begin_checkout"}


def _gtm_events(slug: str) -> str:
    events = json.dumps(TRACKING_EVENTS)
    return ("<script>(function(){var E=" + events + ",p=" + json.dumps(slug) + ";"
            "function push(k){window.dataLayer.push({event:E[k],landing_page:p});}"
            "push('view');var f=document.querySelector('form.lead');"
            "if(f)f.addEventListener('submit',function(){push('lead');});"
            "document.querySelectorAll('a[data-track]').forEach(function(a){"
            "a.addEventListener('click',function(){push(a.getAttribute('data-track'));});});"
            "})();</script>")


# Attribution parameters carried from the visitor's URL into the capture POST.
# Mirrors core/lead_tracking's own field list — imported there rather than
# re-typed, so a new click-id platform is added in exactly one place.
def _attribution_hidden_fields(query_params, path: str, referrer: str) -> str:
    """Hidden inputs carrying utm_*/click ids straight off THIS request's query
    string.

    Rendered SERVER-SIDE on purpose. The obvious implementation is a snippet of
    JavaScript reading `location.search` — but this page's whole design rule is
    that it ships no JS at all (see render_page), and the route already holds
    the query string, so reading it here costs nothing and keeps the page a
    single static document. It also means attribution survives a visitor with
    JS disabled, which a script-based version silently would not.
    """
    from core import lead_tracking

    fields = []
    for name in list(lead_tracking.UTM_FIELDS) + list(lead_tracking.CLICK_ID_PLATFORMS):
        value = (query_params.get(name) or "").strip()[:500]
        if value:
            fields.append(f'<input type="hidden" name="{_e(name)}" value="{_e(value)}">')
    # Always sent: they let the classifier fall back to referrer/direct instead
    # of reporting "no data" for an untagged visit.
    fields.append(f'<input type="hidden" name="landing_path" value="{_e(path)}">')
    if referrer:
        fields.append(f'<input type="hidden" name="referrer" value="{_e(referrer)}">')
    return "\n  ".join(fields)


def _footer(business: str, links: dict) -> str:
    nav = ""
    if links.get("privacy") or links.get("terms"):
        nav = "<nav>"
        if links.get("privacy"):
            nav += f'<a href="{_e(links["privacy"])}">מדיניות פרטיות</a>'
        if links.get("terms"):
            nav += f'<a href="{_e(links["terms"])}">תנאי שימוש</a>'
        nav += "</nav>"
    owner = f"{_e(business)} · " if business else ""
    return f"<footer>{nav}{owner}נבנה ומתארח על ידי uallak</footer>"


def render_page(page: dict, capture_url: str, public_url: str,
                sent: bool = False, query_params=None, path: str = "",
                referrer: str = "", visible: dict = None, business_name: str = "",
                links: dict = None, gtm_container: str = "", form_stamp: str = "",
                preview: bool = False) -> str:
    """The whole page, as one self-contained HTML document. No external CSS,
    no fonts, and NO JavaScript unless the client's own Tag Manager is on
    (custom domain only) — a landing page's only job is to load instantly and
    capture a lead, and every external request is a chance to be slow.

    `visible`: the version to render (landing_pages.visible_of); default the
    page row itself. `links`: call / whatsapp / store (our counting
    redirects), privacy / terms. `form_stamp`: the signed render time the
    capture endpoint uses against bots (core/landing_tracking).

    The form is a plain `<form method="post">` to the existing capture
    endpoint, which is exactly what that endpoint was built to accept (it
    hand-parses urlencoded bodies so no JS is needed). `redirect` returns the
    visitor to this same page with ?sent=1, which satisfies the endpoint's
    same-host-https guard on both the shared and the custom domain."""
    visible = visible or {"title": page.get("title", ""), "content": page.get("content") or {},
                          "contact": page.get("contact") or {}}
    content = normalize_content(visible.get("content") or {})
    links = links or {}
    title = _e(visible.get("title") or content["headline"] or "")
    slug = re.sub(r"[^a-z0-9-]", "", str(page.get("slug") or ""))

    benefits = "".join(f"<li>{_e(b)}</li>" for b in content["benefits"])
    benefits_html = f'<ul class="benefits">{benefits}</ul>' if benefits else ""

    # Built with a plain loop rather than a nested f-string comprehension:
    # same-quote nesting inside f-strings only parses on 3.12+ (PEP 701), and
    # this file must not carry a hidden interpreter-version requirement.
    # Photos: OUR stored copies only (landing_images.image_url — ints and hex,
    # so safe in an attribute and in url()); never a Drive link. A section
    # image whose section no longer exists joins the gallery.
    from core import landing_images
    owner = int(page.get("client_id") or 0)
    refs = content["images"] if owner else []

    def img(ref, cls="", lazy=True):
        attrs = ' class="' + cls + '"' if cls else ""
        attrs += ' src="' + _e(landing_images.image_url(owner, ref)) + '" alt="' + _e(ref["alt"]) + '"'
        if ref.get("w") and ref.get("h"):
            attrs += ' width="' + str(int(ref["w"])) + '" height="' + str(int(ref["h"])) + '"'
        attrs += ' loading="lazy"' if lazy else ' fetchpriority="high"'
        return "<img" + attrs + ' decoding="async">'

    hero = next((r for r in refs if r["placement"] == "hero"), None)
    background = next((r for r in refs if r["placement"] == "background"), None)
    by_section = {r["section"]: r for r in refs if r["placement"] == "section"
                  and r["section"] < len(content["sections"])}
    gallery = [r for r in refs if r["placement"] == "gallery"
               or (r["placement"] == "section" and r["section"] >= len(content["sections"]))]

    section_parts = []
    for number, section in enumerate(content["sections"]):
        block = '<section class="block">'
        if number in by_section:
            block += img(by_section[number])
        if section["title"]:
            block += f'<h2>{_e(section["title"])}</h2>'
        if section["body"]:
            block += f'<p>{_e(section["body"])}</p>'
        section_parts.append(block + "</section>")
    sections_html = "".join(section_parts)
    gallery_html = (f'<div class="gallery">{"".join(img(r) for r in gallery)}</div>' if gallery else "")
    hero_html = img(hero, "hero", lazy=False) if hero else ""
    heading = f"<h1>{_e(content['headline']) or title}</h1>\n<p class=\"sub\">{_e(content['subheadline'])}</p>"
    if background:
        bg_url = _e(landing_images.image_url(owner, background))
        heading = (f'<header class="bg" role="img" aria-label="{_e(background["alt"])}" '
                   f'style="background-image:linear-gradient(rgba(0,0,0,.5),rgba(0,0,0,.5)),url(\'{bg_url}\')">'
                   f"{heading}</header>")

    actions = []
    if links.get("call"):
        actions.append(f'<a href="{_e(links["call"])}" data-track="call">📞 התקשרו עכשיו</a>')
    if links.get("whatsapp"):
        actions.append(f'<a class="wa" href="{_e(links["whatsapp"])}" data-track="whatsapp">💬 וואטסאפ</a>')
    if links.get("store"):
        actions.append(f'<a href="{_e(links["store"])}" data-track="store">🛒 לרכישה באתר</a>')
    actions_html = f'<div class="actions">{"".join(actions)}</div>' if actions else ""

    attribution_fields = _attribution_hidden_fields(query_params or {}, path, referrer)

    sent_html = ('<div class="sent">תודה! קיבלנו את הפרטים ונחזור אליכם בהקדם.</div>'
                 if sent else "")
    note_html = f'<p class="note">{_e(content["form_note"])}</p>' if content["form_note"] else ""
    who = _e(business_name) if business_name else "בעל העסק"
    privacy_link = (f' — <a href="{_e(links["privacy"])}">מדיניות הפרטיות</a>' if links.get("privacy") else "")
    consent_html = (f'<p class="consent">בשליחת הטופס אתם מאשרים ל{who} לחזור אליכם בנוגע לפנייה. '
                    f'הפרטים נשמרים רק לשם כך{privacy_link}.</p>')
    gtm = _gtm_head(gtm_container) if not preview else ""
    events = _gtm_events(slug) if gtm else ""
    robots = "noindex,nofollow" if preview else "index,follow"

    return f"""<!doctype html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta name="description" content="{_e(content['subheadline'])}">
<meta name="robots" content="{robots}">
{gtm}
<style>{PAGE_CSS}</style>
</head>
<body>
<main class="wrap">
{sent_html}
{hero_html}
{heading}
{actions_html}
{benefits_html}
{sections_html}
{gallery_html}
<form class="lead" action="{_e(capture_url)}" method="post">
  <label for="lp-name">שם</label>
  <input id="lp-name" name="name" type="text" autocomplete="name" required>
  <label for="lp-phone">טלפון</label>
  <input id="lp-phone" name="phone" type="tel" autocomplete="tel">
  <label for="lp-email">אימייל</label>
  <input id="lp-email" name="email" type="email" autocomplete="email">
  <label for="lp-message">הודעה (לא חובה)</label>
  <textarea id="lp-message" name="message"></textarea>
  <div class="hp" aria-hidden="true"><label for="lp-website">אתר</label><input id="lp-website" name="website" type="text" tabindex="-1" autocomplete="off"></div>
  <input type="hidden" name="lp_stamp" value="{_e(form_stamp)}">
  <input type="hidden" name="source" value="website_form">
  <input type="hidden" name="source_detail" value="{_e(source_detail_for(slug))}">
  <input type="hidden" name="redirect" value="{_e(public_url)}?sent=1">
  {attribution_fields}
  <button type="submit"{' disabled' if preview else ''}>{_e(content['cta_text'])}</button>
  {note_html}
  {consent_html}
</form>
</main>
{_footer(business_name, links)}
{events}
</body>
</html>"""


def page_size(page: dict) -> int:
    """Bytes of the rendered page — the size limit's measure."""
    visible = {"title": page.get("title", ""), "content": page.get("content") or {},
               "contact": page.get("contact") or {}}
    links = {"call": "https://x", "whatsapp": "https://x", "store": "https://x",
             "privacy": "https://x", "terms": "https://x"}
    return len(render_page(page, "https://x", "https://x", visible=visible, links=links).encode("utf-8"))


# ─── The legal pages every landing page links to ─────────────────────────────

def render_legal(kind: str, business_name: str, contact_email: str = "", contact_phone: str = "") -> str:
    """A fixed Hebrew privacy notice / terms page per client: the BUSINESS
    collects the enquiry, uallak hosts the page and stores the form on its
    behalf. Fixed text, every value escaped — never model-written."""
    who = _e(business_name or "בעל העסק")
    contact = " · ".join(_e(x) for x in (contact_email, contact_phone) if x)
    contact_line = f"<p>ליצירת קשר: {contact}</p>" if contact else ""
    if kind == "privacy":
        heading = "מדיניות פרטיות"
        body = (f"<p>הדף מופעל על ידי {who}. הפרטים שתשאירו בטופס (שם, טלפון, אימייל והודעה) "
                f"נשמרים כדי ש{who} יחזרו אליכם בנוגע לפנייה, ולא לשום מטרה אחרת.</p>"
                "<p>הדף והטופס מתארחים אצל uallak, שמאחסנת את הפרטים עבור העסק בלבד ואינה משתמשת בהם "
                "לצרכיה. אנחנו סופרים כניסות ולחיצות לדף בלי לזהות אתכם אישית.</p>"
                f"<p>אפשר לבקש לעיין בפרטים, לתקן אותם או למחוק אותם בכל פנייה ל{who}.</p>")
    else:
        heading = "תנאי שימוש"
        body = (f"<p>הדף מציג הצעה של {who}. התוכן נכתב עבור העסק ומיועד למידע בלבד; "
                f"התנאים המחייבים הם אלה שתסכמו ישירות עם {who}.</p>"
                "<p>שליחת הטופס אינה מחייבת אתכם לדבר — היא רק בקשה שיחזרו אליכם.</p>"
                "<p>הדף מתארח אצל uallak. אין להעתיק את תוכנו או לשבש את פעולתו.</p>")
    return f"""<!doctype html>
<html lang="he" dir="rtl">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{heading} — {who}</title><meta name="robots" content="noindex,follow"><style>{PAGE_CSS}</style></head>
<body><main class="wrap legal"><h1>{heading}</h1>{body}{contact_line}</main>
<footer>{who} · נבנה ומתארח על ידי uallak</footer></body></html>"""


