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
"""
import html
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

_db_instance = None


def _db():
    global _db_instance
    if _db_instance is None:
        from supabase import create_client
        _db_instance = create_client(os.environ["SUPABASE_URL"],
                                     os.environ["SUPABASE_SERVICE_KEY"])
    return _db_instance


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clip(value, limit: int) -> str:
    return (str(value or "").strip())[:limit]


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


def _unique_slug(client_id: int, desired: str) -> str:
    existing = {row["slug"] for row in
                (_db().table("landing_pages").select("slug")
                 .eq("client_id", client_id).execute().data or [])}
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
    }


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


def update_page(client_id: int, page_id: int, fields: dict) -> dict:
    """Only these four are writable; anything else in `fields` is ignored, so a
    bad payload can never flip client_id or rewrite a slug into a collision."""
    changes = {}
    if "title" in fields:
        changes["title"] = _clip(fields["title"], MAX_TITLE_CHARS)
    if "goal" in fields:
        changes["goal"] = _clip(fields["goal"], MAX_GOAL_CHARS)
    if "content" in fields:
        changes["content"] = normalize_content(fields["content"])
    if "status" in fields:
        if fields["status"] not in STATUSES:
            return {"success": False, "code": "ERR_LANDING_BAD_STATUS",
                    "errors": [f"status must be one of {STATUSES}"]}
        changes["status"] = fields["status"]
    if not changes:
        return {"success": False, "code": "ERR_LANDING_NO_FIELDS",
                "errors": ["nothing to update"]}

    changes["updated_at"] = _now()
    result = (_db().table("landing_pages").update(changes)
              .eq("id", page_id).eq("client_id", client_id).execute())
    if not result.data:
        return {"success": False, "code": "ERR_LANDING_NOT_FOUND",
                "errors": ["page not found"]}
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
footer{margin-top:40px;font-size:12px;color:var(--muted);text-align:center}
"""


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


def render_page(page: dict, capture_url: str, public_url: str,
                sent: bool = False, query_params=None, path: str = "",
                referrer: str = "") -> str:
    """The whole page, as one self-contained HTML document. No external CSS,
    no JS, no fonts — a landing page's only job is to load instantly and
    capture a lead, and every external request is a chance to be slow.

    The form is a plain `<form method="post">` to the existing capture
    endpoint, which is exactly what that endpoint was built to accept (it
    hand-parses urlencoded bodies so no JS is needed). `redirect` returns the
    visitor to this same page with ?sent=1, which satisfies the endpoint's
    same-host-https guard on both the shared and the custom domain."""
    content = normalize_content(page.get("content") or {})
    title = _e(page.get("title") or content["headline"] or "")

    benefits = "".join(f"<li>{_e(b)}</li>" for b in content["benefits"])
    benefits_html = f'<ul class="benefits">{benefits}</ul>' if benefits else ""

    # Built with a plain loop rather than a nested f-string comprehension:
    # same-quote nesting inside f-strings only parses on 3.12+ (PEP 701), and
    # this file must not carry a hidden interpreter-version requirement.
    section_parts = []
    for section in content["sections"]:
        block = '<section class="block">'
        if section["title"]:
            block += f'<h2>{_e(section["title"])}</h2>'
        if section["body"]:
            block += f'<p>{_e(section["body"])}</p>'
        section_parts.append(block + "</section>")
    sections_html = "".join(section_parts)

    attribution_fields = _attribution_hidden_fields(query_params or {}, path, referrer)

    sent_html = ('<div class="sent">תודה! קיבלנו את הפרטים ונחזור אליכם בהקדם.</div>'
                 if sent else "")
    note_html = f'<p class="note">{_e(content["form_note"])}</p>' if content["form_note"] else ""

    return f"""<!doctype html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<meta name="description" content="{_e(content['subheadline'])}">
<meta name="robots" content="index,follow">
<style>{PAGE_CSS}</style>
</head>
<body>
<main class="wrap">
{sent_html}
<h1>{_e(content['headline']) or title}</h1>
<p class="sub">{_e(content['subheadline'])}</p>
{benefits_html}
{sections_html}
<form action="{_e(capture_url)}" method="post">
  <label for="lp-name">שם</label>
  <input id="lp-name" name="name" type="text" autocomplete="name" required>
  <label for="lp-phone">טלפון</label>
  <input id="lp-phone" name="phone" type="tel" autocomplete="tel">
  <label for="lp-email">אימייל</label>
  <input id="lp-email" name="email" type="email" autocomplete="email">
  <label for="lp-message">הודעה (לא חובה)</label>
  <textarea id="lp-message" name="message"></textarea>
  <input type="hidden" name="source" value="website_form">
  <input type="hidden" name="source_detail" value="{_e(source_detail_for(page.get('slug', '')))}">
  <input type="hidden" name="redirect" value="{_e(public_url)}?sent=1">
  {attribution_fields}
  <button type="submit">{_e(content['cta_text'])}</button>
  {note_html}
</form>
</main>
<footer>נבנה על ידי uallak</footer>
</body>
</html>"""
