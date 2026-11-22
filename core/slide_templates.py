"""A client's named slide templates (2026-11-23, docs/SLIDE-RENDERER.md).

A design the manager approved in the Social tab is saved here as a named,
VERSIONED template: {client_id, template_name, spec, version, created_at}.
Saving an unchanged spec adds no version; restoring an earlier version adds a
new version with that spec (history stays linear and nothing is lost). The
manager's critiques are stored against the template, so the media agent reads
the client's taste on every later design.

One app_settings row per client, `slide_templates:<client_id>` (no migration;
cleared with the client, guarded by deletion_guard).
"""
import copy
import re
from datetime import datetime, timezone

PREFIX = "slide_templates:"
NAME_MAX = 40
MAX_TEMPLATES = 30
MAX_VERSIONS = 30
MAX_CRITIQUES = 100
CRITIQUE_MAX = 600


class TemplateError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _db():
    from agents.client_agent import _db as db
    return db()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load(client_id: int) -> dict:
    rows = (_db().table("app_settings").select("value").eq("key", f"{PREFIX}{int(client_id)}")
            .limit(1).execute().data or [])
    value = rows[0].get("value") if rows else None
    value = value if isinstance(value, dict) else {}
    value.setdefault("templates", {})
    return value


def _save(client_id: int, data: dict):
    _db().table("app_settings").upsert({"key": f"{PREFIX}{int(client_id)}", "value": data,
                                        "updated_at": _now()}, on_conflict="key").execute()


def clean_name(name) -> str:
    name = re.sub(r"\s+", " ", str(name or "")).strip()[:NAME_MAX]
    if len(name) < 2:
        raise TemplateError("bad_name")
    return name


def _entry(name: str, tpl: dict, version: dict) -> dict:
    return {"name": name, "version": version["version"], "spec": copy.deepcopy(version["spec"]),
            "created_at": version["created_at"], "by": version.get("by", ""), "note": version.get("note", ""),
            "palette_variant": version["spec"].get("palette_variant", ""), "versions": len(tpl["versions"])}


# ─── Read ────────────────────────────────────────────────────────────────────

def names(client_id: int) -> list:
    data = _load(client_id)
    out = []
    for name, tpl in data["templates"].items():
        last = tpl["versions"][-1]
        out.append({"name": name, "version": last["version"], "palette_variant": last["spec"].get("palette_variant", ""),
                    "updated_at": last["created_at"], "versions": len(tpl["versions"])})
    return sorted(out, key=lambda t: t["updated_at"], reverse=True)


def get(client_id: int, name: str, version: int = None) -> dict:
    """The template at `version` (default: the latest), or None."""
    tpl = _load(client_id)["templates"].get(str(name or ""))
    if not tpl:
        return None
    if version is None:
        return _entry(name, tpl, tpl["versions"][-1])
    match = [v for v in tpl["versions"] if v["version"] == int(version)]
    return _entry(name, tpl, match[0]) if match else None


def history(client_id: int, name: str) -> list:
    tpl = _load(client_id)["templates"].get(str(name or "")) or {}
    return [{"version": v["version"], "created_at": v["created_at"], "by": v.get("by", ""), "note": v.get("note", "")}
            for v in tpl.get("versions") or []]


def for_variant(client_id: int, variant: str) -> dict:
    """The most recently saved template drawn in this palette variant, or None."""
    for t in names(client_id):
        if t["palette_variant"] == variant:
            return get(client_id, t["name"])
    return None


# ─── Write ───────────────────────────────────────────────────────────────────

def save(client_id: int, name: str, spec: dict, by: str = "client", note: str = "") -> dict:
    """A new version of `name` (created on first save). An unchanged spec
    returns the current version without adding one."""
    from core import slide_spec
    name = clean_name(name)
    spec = slide_spec.validate(spec)
    data = _load(client_id)
    tpl = data["templates"].get(name)
    if tpl and tpl["versions"][-1]["spec"] == spec:
        return _entry(name, tpl, tpl["versions"][-1])
    if not tpl:
        if len(data["templates"]) >= MAX_TEMPLATES:
            raise TemplateError("too_many_templates")
        # Critiques given while it was still a draft move with it
        earlier = (data.get("pending_critiques") or {}).pop(name, {}).get("critiques", [])
        tpl = data["templates"][name] = {"created_at": _now(), "versions": [], "critiques": earlier}
    number = (tpl["versions"][-1]["version"] + 1) if tpl["versions"] else 1
    version = {"version": number, "spec": spec, "created_at": _now(), "by": str(by)[:40],
               "note": re.sub(r"\s+", " ", str(note or "")).strip()[:200]}
    tpl["versions"].append(version)
    tpl["versions"] = tpl["versions"][-MAX_VERSIONS:]
    _save(client_id, data)
    return _entry(name, tpl, version)


def restore(client_id: int, name: str, version: int, by: str = "client") -> dict:
    """An earlier version back as the NEWEST version (its spec, a new number)."""
    old = get(client_id, name, version)
    if not old:
        raise TemplateError("version_not_found")
    current = get(client_id, name)
    if current and current["spec"] == old["spec"]:
        return current
    data = _load(client_id)
    tpl = data["templates"][name]
    number = tpl["versions"][-1]["version"] + 1
    entry = {"version": number, "spec": old["spec"], "created_at": _now(), "by": str(by)[:40],
             "note": f"restored from version {int(version)}"}
    tpl["versions"].append(entry)
    tpl["versions"] = tpl["versions"][-MAX_VERSIONS:]
    _save(client_id, data)
    return _entry(name, tpl, entry)


def add_critique(client_id: int, name: str, text: str, version: int = None, item_id: str = "",
                 by: str = "client") -> dict:
    """The manager's critique, kept against the template (created empty if
    the design was never saved yet), newest last."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) < 2:
        raise TemplateError("critique_required")
    name = clean_name(name)
    data = _load(client_id)
    tpl = data["templates"].get(name)
    if tpl is None:
        tpl = data.setdefault("pending_critiques", {}).setdefault(name, {"critiques": []})
    data["seq"] = int(data.get("seq") or 0) + 1          # orders two critiques within one clock tick
    row = {"at": _now(), "seq": data["seq"], "text": text[:CRITIQUE_MAX], "version": version,
           "item_id": str(item_id or "")[:40], "by": str(by)[:40]}
    tpl.setdefault("critiques", []).append(row)
    tpl["critiques"] = tpl["critiques"][-MAX_CRITIQUES:]
    _save(client_id, data)
    return row


def critiques(client_id: int, name: str = None, limit: int = 20) -> list:
    """The client's critiques, newest first — one template's, or all of them
    (the client's taste across designs)."""
    data = _load(client_id)
    pools = list(data["templates"].items()) + list((data.get("pending_critiques") or {}).items())
    rows = []
    for tpl_name, tpl in pools:
        if name and tpl_name != name:
            continue
        rows += [{**c, "template": tpl_name} for c in tpl.get("critiques") or []]
    return sorted(rows, key=lambda c: (c["at"], c.get("seq", 0)), reverse=True)[:limit]
