"""The brand brief, per client, in the DATABASE, versioned (2026-11-24).
Schema: knowledge/brand-brief.schema.md (stays in the repo).

Before: a brief was a repo file (knowledge/brands/<id>.md + <id>.palette.json),
so a new client could not get one at runtime and every content request was
refused with "missing knowledge files". Now:

- STORAGE: app_settings `brand_brief:<client_id>` =
  {"versions": [{v, at, by, kind, status, text, palette, gaps, sources}],
   "draft_job": {"state": "queued"|"running"|"done"|"failed", ...}}
  `kind`: migrated | drafted | edited | approved. `status`: draft | approved.
  The newest version is the brief. Every save is a new version (≤ MAX_VERSIONS
  kept), so the manager's corrections persist and survive approval.
- MIGRATION: a client with no row but a repo file (client 27) is seeded from
  the file on first read, text and palette byte-for-byte unchanged
  (kind "migrated", status from the file's own "Status:" line). After that
  the file is never read for that client. If the database cannot be read the
  file is used read-only, so nothing about client 27 changes either way.
- AUTO-DRAFT: at onboarding (checkout / a client created in the admin) and
  whenever an agent needs a brief that does not exist, a draft is QUEUED; the
  every-minute scheduler tick (/api/tiktok/uploads/run) runs it inside its
  request (never a background task). The intake agent reads what is already
  there — the intake answers from the sales chat, the client's website, their
  public social pages, descriptions of their connected Drive assets — and
  writes the brief in the schema's format with a confidence mark on every
  field ([stated] / [observed] / [inferred]). The owner fills in nothing.
- USABLE AT ONCE: agents work from a draft (its own "Status: DRAFT" line
  tells them; [inferred] = direction only). No brief yet = agents still work,
  from the request and the craft library, and say what they lack.
- REVIEW: Control Room → client → brand brief: every field with its mark,
  edit any section or the palette, approve. Approval rewrites the Status line
  and clears the draft state; edits are stored as versions.
- PALETTE: only colours actually seen on the site (theme-color, CSS) may
  fill a role; a role that cannot be determined is a GAP, never invented.
  slide_spec.palette_roles falls back to NEUTRAL for missing roles / no
  palette, and the gaps show in the Control Room.

Cleared with the client (client_deletion, deletion_guard, admin_service).
"""
import copy
import json
import os
import re
import time
from datetime import datetime, timezone

SERVICE_NAME = "brand_brief"
PREFIX = "brand_brief:"
MAX_VERSIONS = 30
TEXT_MAX = 40000
CACHE_S = 20
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEGACY_DIR = os.path.join(ROOT, "knowledge", "brands")      # the one-time migration seed
SCHEMA_PATH = os.path.join(ROOT, "knowledge", "brand-brief.schema.md")

COLOR_ROLES = ("bg", "fg", "accent", "muted")
NEUTRAL = {"bg": "#FFFFFF", "fg": "#111111", "accent": "#333333", "muted": "#8A8A8A"}
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
MARK_RE = re.compile(r"\[(stated|observed|inferred)\b[^\]]*\]", re.IGNORECASE)
FIELD_RE = re.compile(r"^(\s*)-\s+\**([A-Za-z_][A-Za-z0-9_ /]*?)\**\s*:\s*(.*)$")
STATUS_LINE_RE = re.compile(r"^Status:.*$", re.MULTILINE)
DRAFT_STATUS_LINE = "Status: DRAFT — awaiting manager approval"
MISSING_NOTE = ("NO BRAND BRIEF YET for this client (it is being drafted automatically). Work from the request, "
                "the craft library and what the client said in this conversation. Use neutral, plain choices for "
                "tone and colour, make no factual claim about the business you were not told, and say briefly what "
                "you lacked (for example: tone of voice, audience, palette).")

_cache = {}           # client_id -> (monotonic, record)


class BriefError(ValueError):
    pass


def _db():
    from agents.client_agent import _db as db
    return db()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key(client_id: int) -> str:
    return f"{PREFIX}{int(client_id)}"


def _log(line: str):
    print(f"[{SERVICE_NAME}] {line}")


# ─── Storage ─────────────────────────────────────────────────────────────────

def _read_row(client_id: int):
    """The stored value, {} when there is no row, None when unreadable."""
    try:
        rows = (_db().table("app_settings").select("value").eq("key", _key(client_id)).limit(1)
                .execute().data) or []
    except Exception as e:
        _log(f"client {client_id}: unreadable ({type(e).__name__})")
        return None
    value = rows[0].get("value") if rows else {}
    return value if isinstance(value, dict) else {}


def _write_row(client_id: int, value: dict):
    _db().table("app_settings").upsert({"key": _key(client_id), "value": value, "updated_at": _now()},
                                       on_conflict="key").execute()
    _cache.pop(int(client_id), None)


def page_state(client_id: int) -> dict:
    """What the client's branding page remembers on this same row (for
    example that its one friendly note was dismissed). {} when unreadable."""
    value = _read_row(client_id)
    return dict((value or {}).get("page") or {})


def set_page_state(client_id: int, **fields) -> bool:
    value = _read_row(client_id)
    if value is None:
        raise BriefError("database_unavailable")
    value["page"] = {**(value.get("page") or {}), **fields}
    _write_row(client_id, value)
    return True


def _legacy(client_id: int) -> dict:
    """The repo files of a client briefed before 2026-11-24 (client 27)."""
    path = os.path.join(LEGACY_DIR, f"{int(client_id)}.md")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return {}
    palette = None
    try:
        with open(os.path.join(LEGACY_DIR, f"{int(client_id)}.palette.json"), encoding="utf-8") as f:
            palette = json.load(f)
    except (OSError, ValueError):
        palette = None
    m = STATUS_LINE_RE.search(text)
    status = "approved" if m and "approved" in m.group(0).lower() else "draft"
    return {"v": 1, "at": _now(), "by": "migration", "kind": "migrated", "status": status, "text": text,
            "palette": palette, "gaps": [], "fields": structured(text),
            "sources": {"migrated_from": f"knowledge/brands/{int(client_id)}.md"}}


def record(client_id: int, fresh: bool = False) -> dict:
    """{"versions": [...], "draft_job": {...}, "source": "database" |
    "repo_file" | "none"}. Seeds a legacy client on first read."""
    client_id = int(client_id)
    hit = _cache.get(client_id)
    if hit and not fresh and time.monotonic() - hit[0] < CACHE_S:
        return copy.deepcopy(hit[1])
    value = _read_row(client_id)
    if value is None:                                        # database down: the repo file, read-only
        seed = _legacy(client_id)
        out = {"versions": [seed] if seed else [], "draft_job": {}, "source": "repo_file" if seed else "none"}
        return out
    versions = list(value.get("versions") or [])
    if not versions:
        seed = _legacy(client_id)
        if seed:
            value = {**value, "versions": [seed]}
            try:
                _write_row(client_id, value)
                _log(f"client {client_id}: brief migrated from {seed['sources']['migrated_from']}")
            except Exception as e:
                _log(f"client {client_id}: migration write failed ({type(e).__name__}); using the file")
            versions = [seed]
    elif versions[-1].get("kind") == "migrated" and (versions[-1].get("fields") or {}).get("parser") != PARSER_VERSION:
        # The first migration (2026-11-24) stored the text whole but no fields;
        # re-run it ONCE from the repo file while nobody has edited since
        try:
            remigrate(client_id, by="migration")
            return record(client_id, fresh=True)
        except Exception as e:
            _log(f"client {client_id}: re-migration failed ({type(e).__name__})")
    out = {"versions": versions, "draft_job": dict(value.get("draft_job") or {}),
           "source": "database" if versions else "none"}
    _cache[client_id] = (time.monotonic(), copy.deepcopy(out))
    return out


def current(client_id: int) -> dict:
    """The newest version ({} = no brief)."""
    versions = record(client_id)["versions"]
    return dict(versions[-1]) if versions else {}


def text(client_id: int) -> str:
    return str(current(client_id).get("text") or "")


def palette(client_id: int):
    p = current(client_id).get("palette")
    return p if isinstance(p, dict) else None


def _append(client_id: int, version: dict) -> dict:
    value = _read_row(client_id)
    if value is None:
        raise BriefError("database_unavailable")
    versions = list(value.get("versions") or [])
    if not versions:
        seed = _legacy(client_id)
        versions = [seed] if seed else []
    version = {**version, "v": (int(versions[-1].get("v") or 0) + 1) if versions else 1, "at": _now(),
               "fields": structured(version.get("text", ""))}
    versions.append(version)
    value["versions"] = versions[-MAX_VERSIONS:]
    _write_row(client_id, value)
    return version


def _clean_palette(p) -> dict:
    """{"variants": {label: {role: HEX}}, ...} with only valid hex kept."""
    if not isinstance(p, dict):
        return None
    out = {k: v for k, v in p.items() if k != "variants"}
    variants = {}
    for label, roles in (p.get("variants") or {}).items():
        if isinstance(roles, dict):
            variants[str(label)[:60]] = {r: str(roles[r]).upper() for r in COLOR_ROLES
                                         if HEX_RE.match(str(roles.get(r) or ""))}
    out["variants"] = variants
    return out


def save_edit(client_id: int, new_text: str = None, new_palette=None, by: str = "manager") -> dict:
    """The manager's correction: a new version, the status unchanged (a
    draft stays a draft until approved; an approved brief stays approved)."""
    cur = current(client_id)
    body = cur.get("text", "") if new_text is None else str(new_text)
    if not body.strip():
        raise BriefError("text_required")
    if len(body) > TEXT_MAX:
        raise BriefError("text_too_long")
    pal = cur.get("palette") if new_palette is None else _clean_palette(new_palette)
    if body == cur.get("text") and pal == cur.get("palette"):
        return cur
    return _append(client_id, {"by": str(by)[:40], "kind": "edited", "status": cur.get("status") or "draft",
                               "text": body, "palette": pal, "gaps": list(cur.get("gaps") or []),
                               "sources": dict(cur.get("sources") or {})})


def remigrate(client_id: int, by: str = "manager") -> dict:
    """Re-run the migration from the repo file: a NEW version whose text and
    palette are the file's, byte-for-byte (earlier versions stay in the
    history). The file is never deleted here."""
    seed = _legacy(client_id)
    if not seed:
        raise BriefError("no_repo_file")
    return _append(client_id, {"by": str(by)[:40], "kind": "migrated", "status": seed["status"], "text": seed["text"],
                               "palette": seed["palette"], "gaps": [], "sources": seed["sources"]})


def repo_file_check(client_id: int, body: str) -> dict:
    """Does the stored text equal knowledge/brands/<id>.md exactly?"""
    seed = _legacy(client_id)
    if not seed:
        return {"exists": False}
    return {"exists": True, "path": seed["sources"]["migrated_from"], "matches": seed["text"] == body,
            "file_chars": len(seed["text"]), "stored_chars": len(body or "")}


def approve(client_id: int, by: str = "manager") -> dict:
    """Clears the draft state: the Status line says approved; the text the
    manager corrected is kept as it is."""
    cur = current(client_id)
    if not cur:
        raise BriefError("no_brief")
    line = f"Status: APPROVED by the manager — {datetime.now(timezone.utc).date().isoformat()}"
    body = cur["text"]
    body = STATUS_LINE_RE.sub(line, body, count=1) if STATUS_LINE_RE.search(body) else line + "\n" + body
    return _append(client_id, {"by": str(by)[:40], "kind": "approved", "status": "approved", "text": body,
                               "palette": cur.get("palette"), "gaps": list(cur.get("gaps") or []),
                               "sources": dict(cur.get("sources") or {})})


# ─── What agents get ─────────────────────────────────────────────────────────

def for_agents(client_id: int) -> dict:
    """{"text", "status", "lacks"}: the brief as it is (a draft says so in its
    own Status line), or the MISSING note — never an exception."""
    try:
        cur = current(client_id)
    except Exception as e:
        _log(f"client {client_id}: brief unreadable for agents ({type(e).__name__})")
        cur = {}
    if not cur or not str(cur.get("text") or "").strip():
        request_draft(client_id, "agent_needed_it")
        return {"text": MISSING_NOTE, "status": "missing", "lacks": ["brand_brief"]}
    lacks = [f"palette: {g}" for g in (cur.get("gaps") or []) if str(g).startswith("palette")] if cur.get("gaps") else []
    if not cur.get("palette"):
        lacks.append("palette (neutral colours used)")
    # Unmarked = inferred: the agents read a rule + the list of every unmarked
    # field / item after the brief (the brief itself stays as written)
    return {"text": cur["text"].rstrip() + "\n\n" + confidence_footer(cur["text"]), "source_text": cur["text"],
            "status": cur.get("status") or "draft", "lacks": lacks}


# ─── Palette roles (slide_spec) ──────────────────────────────────────────────

def roles_for(client_id: int, variant_label: str) -> dict:
    """{"roles": {bg, fg, accent, muted}, "neutral": [roles that fell back],
    "known": bool (the client has a palette), "variant_found": bool}."""
    p = palette(client_id)
    variants = (p or {}).get("variants") or {}
    found = variants.get(str(variant_label or ""))
    if found is None and len(variants) == 1 and str(variant_label or "default") == "default":
        found = next(iter(variants.values()))           # a single derived variant serves "default"
    roles, neutral = {}, []
    for r in COLOR_ROLES:
        value = str((found or {}).get(r) or "")
        if HEX_RE.match(value):
            roles[r] = value.upper()
        else:
            roles[r] = NEUTRAL[r]
            neutral.append(r)
    return {"roles": roles, "neutral": neutral, "known": bool(variants), "variant_found": found is not None}


# ─── The Control Room view ───────────────────────────────────────────────────

_MD_RE = re.compile(r"\*\*|__|`")
_BULLET_RE = re.compile(r"^(?:[-*•]|\d+[.)])\s+(.*)$")
_NUM_RE = re.compile(r"^\d+[.)]\s+")
_KEY_RE = re.compile(r"^([A-Za-z\u0590-\u05FF][^:\[\]]{0,59}?)\s*:\s*(.*)$")
PARSER_VERSION = 2


def strip_md(s: str) -> str:
    """Markdown out of a key / value: bold / italic markers, code ticks, a
    heading's #s. Never a word."""
    s = re.sub(r"^#+\s+", "", str(s or "").strip())
    return re.sub(r"\s+", " ", _MD_RE.sub("", s)).strip()


def _is_key(k: str) -> bool:
    return bool(k) and "http" not in k.lower() and "." not in k and len(k.split()) <= 8


def _marked(text: str) -> tuple:
    """(text without the marks, first mark or "", the mark's note, every
    mark in order)."""
    found = list(MARK_RE.finditer(text))
    clean = re.sub(r"\s+", " ", MARK_RE.sub("", text))
    clean = re.sub(r"\s+([.,;:])", r"\1", clean).strip(" ;,")
    if not found:
        return clean, "", "", []
    m = found[0]
    note = re.sub(r"\s+", " ", m.group(0)[1:-1][len(m.group(1)):]).strip(" —-–:,")
    return clean, m.group(1).lower(), note, [x.group(1).lower() for x in found]


def parse_fields(lines: list) -> list:
    """The fields of ONE section, complete: [{"key", "value", "mark",
    "mark_note", "items": [{"text", "mark", "mark_note", "effective"}],
    "effective", "unmarked"}].

    - a field's value runs over its continuation lines (indented, or a plain
      line that is not "key: value"), joined with spaces;
    - a "key:" with list items under it (nested bullets, numbered lines, or
      key-less top-level bullets right after an empty "key:") keeps them AS A
      LIST, each item with its own continuation lines;
    - a key-less bullet with no list open is its own field (key "") — no
      line of the brief is ever dropped;
    - markdown is stripped from keys and values;
    - the confidence mark is found wherever it is in the field or an item
      (also across a line break). NO mark anywhere = effective "inferred"
      (`unmarked: True`): nothing unverified can pass as a fact."""
    fields, f, item = [], None, None

    def new_field(key, value):
        nonlocal f, item
        f = {"key": key, "raw": [value] if value else [], "items": [], "open": not value}
        fields.append(f)
        item = None

    def new_item(text):
        nonlocal item, f
        if f is None:
            new_field("", "")
        item = {"raw": [text]}
        f["items"].append(item)

    for line in lines:
        if not line.strip() or line.strip() == "---" or line.startswith("## "):
            item = None
            continue
        indent = len(line) - len(line.lstrip())
        s = line.strip()
        bullet = _BULLET_RE.match(s)
        if indent == 0 and bullet:
            body = bullet.group(1)
            km = _KEY_RE.match(strip_md(body))
            if s.startswith("-") and km and _is_key(km.group(1)):
                new_field(km.group(1).strip(), body.split(":", 1)[1].strip())
            elif f is not None and (f["open"] or (_NUM_RE.match(s) and f["items"])):
                new_item(body)
            else:
                new_field("", body)
        elif indent == 0:
            km = _KEY_RE.match(strip_md(s))
            if km and _is_key(km.group(1)) and not s.startswith("#"):
                new_field(km.group(1).strip(), s[s.find(":") + 1:].strip())
            elif s.startswith("#"):
                new_field("title", s)
            elif item is not None:
                item["raw"].append(s)
            elif f is not None:
                f["raw"].append(s)
            else:
                new_field("", s)
        elif bullet:
            new_item(bullet.group(1))
        elif item is not None:
            item["raw"].append(s)
        elif f is not None:
            f["raw"].append(s)
        else:
            new_field("", s)
    out = []
    for f in fields:
        value, mark, note, marks = _marked(strip_md(" ".join(f["raw"])))
        items = []
        for it in f["items"]:
            text, imark, inote, imarks = _marked(strip_md(" ".join(it["raw"])))
            items.append({"text": text, "mark": imark, "mark_note": inote, "marks": imarks,
                          "effective": imark or mark or "inferred"})
        any_mark = bool(mark) or any(i["mark"] for i in items)
        effective = mark or (next((i["mark"] for i in items if i["mark"]), "") if items and all(
            i["mark"] for i in items) else "") or "inferred"
        out.append({"key": strip_md(f["key"]), "value": value, "mark": mark, "mark_note": note, "marks": marks,
                    "items": items,
                    "effective": effective, "unmarked": not any_mark})
    return out


def sections(body: str) -> list:
    """[{"title", "text", "fields"}] — the raw text of each section (for the
    section editor, byte-for-byte) and its complete fields (parse_fields).
    The header (before the first "## ") is section 0 with title ""."""
    out, cur = [], {"title": "", "lines": []}
    for line in str(body or "").splitlines():
        if line.startswith("## "):
            out.append(cur)
            cur = {"title": line[3:].strip(), "lines": [line]}
        else:
            cur["lines"].append(line)
    out.append(cur)
    return [{"title": strip_md(s["title"]), "text": "\n".join(s["lines"]), "fields": parse_fields(s["lines"])}
            for s in out]


def structured(body: str) -> dict:
    """What is stored next to the text in every version."""
    return {"parser": PARSER_VERSION,
            "sections": [{"title": s["title"], "fields": s["fields"]} for s in sections(body)]}


def unmarked_fields(body: str) -> list:
    """"section.key" (or "section: item") for everything with no mark."""
    out = []
    for s in sections(body):
        if not s["title"] or s["title"].lower().startswith("open questions"):
            continue
        for f in s["fields"]:
            label = f"{s['title']}.{f['key']}" if f["key"] else f"{s['title']}: {(f['value'] or (f['items'][0]['text'] if f['items'] else ''))[:60]}"
            if f["unmarked"]:
                out.append(label)
            elif f["items"] and not f["mark"]:
                out += [f"{label} — item: {i['text'][:60]}" for i in f["items"] if not i["mark"]]
    return out


CONFIDENCE_RULE = ("## How to read the confidence marks (added by the system, not part of the brief)\n"
                   "- [stated] and [observed] may support a factual claim.\n"
                   "- [inferred], and EVERY field, line or list item WITHOUT a mark, are inferred: direction only. "
                   "Never publish a factual claim that rests on one; ask the manager instead.\n")


def confidence_footer(body: str) -> str:
    rows = unmarked_fields(body)
    listed = "".join(f"  - {r}\n" for r in rows[:40]) + (f"  - … and {len(rows) - 40} more\n" if len(rows) > 40 else "")
    return CONFIDENCE_RULE + ("- Unmarked in this brief, so treated as [inferred]:\n" + listed if rows else "")

def admin_view(client_id: int) -> dict:
    rec = record(client_id, fresh=True)
    cur = rec["versions"][-1] if rec["versions"] else {}
    marks = {"stated": 0, "observed": 0, "inferred": 0, "unmarked": 0}
    secs = sections(cur.get("text", "")) if cur else []
    for s in secs[1:]:                                    # the header and the open questions carry no facts
        if s["title"].lower().startswith("open questions"):
            continue
        for f in s["fields"]:
            marks["unmarked" if f["unmarked"] else f["effective"]] += 1
    pal = cur.get("palette") if isinstance(cur.get("palette"), dict) else None
    gaps = list(cur.get("gaps") or [])
    if cur and not (pal or {}).get("variants"):
        gaps = gaps + ["palette: none defined — neutral colours are used"]
    return {"exists": bool(cur), "status": cur.get("status", "missing") if cur else "missing",
            "source": rec["source"], "version": cur.get("v"), "by": cur.get("by", ""), "at": cur.get("at", ""),
            "kind": cur.get("kind", ""), "text": cur.get("text", ""), "sections": secs, "marks": marks,
            "palette": pal, "neutral": NEUTRAL, "gaps": gaps, "sources": cur.get("sources") or {},
            "draft_job": rec["draft_job"], "repo_file": repo_file_check(client_id, cur.get("text", "")) if cur else {},
            "unmarked": unmarked_fields(cur.get("text", "")) if cur else [],
            "history": [{k: v.get(k) for k in ("v", "at", "by", "kind", "status")} for v in reversed(rec["versions"])][:15]}


# ─── Auto-draft at onboarding (the intake agent) ─────────────────────────────

def request_draft(client_id: int, reason: str = "onboarding") -> bool:
    """Queue ONE draft for a client with no brief. Never raises."""
    try:
        value = _read_row(client_id)
        if value is None or value.get("versions") or _legacy(client_id):
            return False
        job = value.get("draft_job") or {}
        if job.get("state") in ("queued", "running", "done"):
            return False
        value["draft_job"] = {"state": "queued", "reason": str(reason)[:40], "queued_at": _now(),
                              "attempts": int(job.get("attempts") or 0)}
        _write_row(client_id, value)
        return True
    except Exception as e:
        _log(f"client {client_id}: draft not queued ({type(e).__name__})")
        return False


def _queued_clients() -> list:
    rows = (_db().table("app_settings").select("key,value").like("key", f"{PREFIX}%").execute().data) or []
    out = []
    for r in rows:
        v = r.get("value") if isinstance(r.get("value"), dict) else {}
        job = v.get("draft_job") or {}
        if not v.get("versions") and job.get("state") in ("queued", "running") and int(job.get("attempts") or 0) < 3:
            try:
                out.append(int(str(r["key"])[len(PREFIX):]))
            except ValueError:
                pass
    return out


def run_due(budget_s: float) -> dict:
    """The scheduler minute: draft the queued briefs inside this request."""
    deadline = time.monotonic() + max(0.0, float(budget_s))
    done = []
    try:
        queued = _queued_clients()
    except Exception as e:
        return {"error": type(e).__name__}
    for cid in queued:
        if deadline - time.monotonic() < 70:
            break
        done.append({"client_id": cid, **{k: v for k, v in draft_now(cid).items() if k in ("ok", "error")}})
    return {"drafted": done, "queued": len(queued)}


def _set_job(client_id: int, **fields):
    value = _read_row(client_id) or {}
    value["draft_job"] = {**(value.get("draft_job") or {}), **fields}
    _write_row(client_id, value)


def draft_now(client_id: int, by: str = "intake_agent") -> dict:
    """Gather the sources, write the draft, store it as version 1. Refuses
    when a brief already exists (a draft never overwrites the manager)."""
    from core import brand_intake
    if current(client_id):
        return {"ok": False, "error": "exists"}
    value = _read_row(client_id) or {}
    attempts = int((value.get("draft_job") or {}).get("attempts") or 0) + 1
    try:
        _set_job(client_id, state="running", started_at=_now(), attempts=attempts)
    except Exception:
        pass
    try:
        drafted = brand_intake.draft(client_id)
    except Exception as e:
        _log(f"client {client_id}: draft failed ({type(e).__name__}: {str(e)[:160]})")
        try:
            _set_job(client_id, state="failed" if attempts >= 3 else "queued", error=type(e).__name__)
        except Exception:
            pass
        return {"ok": False, "error": type(e).__name__}
    if current(client_id):                     # someone saved one meanwhile — theirs wins
        return {"ok": False, "error": "exists"}
    version = _append(client_id, {"by": by, "kind": "drafted", "status": "draft", "text": drafted["text"],
                                  "palette": drafted.get("palette"), "gaps": drafted.get("gaps") or [],
                                  "sources": drafted.get("sources") or {}})
    _set_job(client_id, state="done", finished_at=_now())
    _log(f"client {client_id}: brief drafted (v{version['v']}, {len(drafted['text'])} chars)")
    return {"ok": True, "version": version["v"]}


def schema_text() -> str:
    try:
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""
