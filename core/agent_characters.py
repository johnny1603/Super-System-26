"""The agents as their own characters in posts (2026-11-24,
docs/AGENT-CHARACTERS.md).

Each uallak agent (maya, lior, yoav, johnny, elinor, nissim, uri) has a
Higgsfield REFERENCE ELEMENT, built by the manager from approved source
images. An image prompt that carries `<<<element_id>>>` is generated with that
character (the backend injects the reference itself — the id never goes into
a media / input-images list). Soul models are NOT trained for this.

Two modes per asset (core/post_brief `characters` + `character_mode`):
- IN_SCENE  — the character is generated inside the scene: one placeholder per
  character + STYLE_CLAUSE, always (in_scene_prompt).
- COMPOSITE — a cut-out is drawn onto a brand-template slide by
  core/slide_renderer, UNDER the text: a pose is generated with the
  placeholder on a plain background, the background is removed (a transparent
  PNG, verified in code) and the pose is kept in the library. Reusing a
  stored pose costs nothing.

THE ONLY GUARDRAIL: an agent never creates or rebuilds a reference element —
not its own, not another agent's. There is no create path anywhere
(create_element / rebuild_element refuse and alert); an agent that judges its
element weak REPORTS it (report_element_weak), it does not act. Rebuilding
identity from generated output compounds drift until the faces change.

Storage (app_settings, global — not per client):
- `agent_elements`: {"agents": {agent: {element_id, name, status, source,
  candidates}}, "overrides": {agent: element_id}, "elements": [...],
  "unmatched": [...], "last_sync", "last_error", "reports": [...]}
  Matched from the element list by name `uallak-<agent>` (case-insensitive;
  a name that merely CONTAINS it, e.g. "char_uallak-lior", is a loose match,
  shown as such). Two candidates for one agent = a conflict: no automatic
  pick, the admin chooses (override). Re-read every SYNC_HOURS from the
  minute tick and on the admin button.
- `agent_poses`: {"poses": [record], "last": {agent: pose_id}} — the pose
  library. Accumulates, never gates: a generated pose is stored at once
  (approved False, usable by the post that made it); when the manager approves
  a post, every character image it used is marked approved and tagged (agent,
  outfit, setting, framing, transparent). find_poses offers APPROVED poses
  before anything is generated, ranked by match minus the usage penalty
  (core/asset_feedback.penalty) and never the pose that agent used last.

Generation runs on uallak's OWN Higgsfield key (HIGGSFIELD_AGENTS_KEY): the
elements are scoped to the account that created them, so a client's key
cannot use them. Without the key the feature refuses plainly.
"""
import io
import os
import re
import secrets
from datetime import datetime, timezone, timedelta

SERVICE_NAME = "agent_characters"
AGENTS = ("maya", "lior", "yoav", "johnny", "elinor", "nissim", "uri")
MODES = ("composite", "in_scene")
MAPPING_KEY = "agent_elements"
POSES_KEY = "agent_poses"
SYNC_HOURS = 6
MAX_CHARACTERS = 3                 # in one in-scene image
MAX_POSES = 3000
STYLE_CLAUSE = "same stylized 3D character look and same face as the reference figure"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
PLACEHOLDER_RE = re.compile(r"<<<\s*[^<>]*\s*>>>")
POSE_ID_RE = re.compile(r"^[a-f0-9]{12}$")
KEY_ENV = "HIGGSFIELD_AGENTS_KEY"
POSE_FOLDER = "uallak-agents"
POSE_BACKGROUND = "on a plain flat light-grey seamless studio background, nothing else in the frame"


class CharacterUnavailable(RuntimeError):
    """No usable element for that agent (missing, conflict, not ready, no key)."""

    def __init__(self, agent: str, reason: str):
        super().__init__(f"agent {agent}: {reason}")
        self.agent, self.reason = agent, reason


class ElementCreationRefused(PermissionError):
    """THE guardrail: no agent creates or rebuilds a reference element."""


class BackgroundNotRemoved(RuntimeError):
    """The background remover returned an image with no transparency."""


def _db():
    from agents.client_agent import _db as db
    return db()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _alert(lines: list):
    try:
        from core.agent_base import agent_alert
        agent_alert(SERVICE_NAME, lines)
    except Exception:
        print(f"[{SERVICE_NAME}] " + " | ".join(lines))


def _read(key: str) -> dict:
    rows = (_db().table("app_settings").select("value").eq("key", key).limit(1).execute().data) or []
    value = rows[0].get("value") if rows else None
    return value if isinstance(value, dict) else {}


def _write(key: str, value: dict):
    _db().table("app_settings").upsert({"key": key, "value": value, "updated_at": _now().isoformat()},
                                       on_conflict="key").execute()


def clean_agent(agent) -> str:
    a = str(agent or "").strip().lower()
    if a not in AGENTS:
        raise ValueError(f"unknown agent {agent!r} (one of {', '.join(AGENTS)})")
    return a


# ─── The key (uallak's own account — the one that owns the elements) ─────────

def agents_key() -> str:
    from core import media_gen_service as gen
    raw = os.environ.get(KEY_ENV, "")
    return gen.normalize_key(raw) if raw else ""


def configured() -> bool:
    from core import media_gen_service as gen
    key = agents_key()
    return bool(key) and gen.has_both_parts(key)


# ─── Mapping: agent → element ────────────────────────────────────────────────

def _norm_name(name: str) -> str:
    return re.sub(r"\s+", "", str(name or "")).lower()


def match_elements(elements: list, overrides: dict = None) -> dict:
    """The element list → {"agents", "unmatched"} (pure, no I/O).
    exact = the whole name is uallak-<agent> (case-insensitive); loose = the
    name contains it as its own token (e.g. char_uallak-lior). Exact beats
    loose; two of the best kind = a conflict (no automatic pick). Only a
    "completed" element is usable. An override always wins."""
    overrides = overrides or {}
    els = []
    for e in elements or []:
        if not isinstance(e, dict):
            continue
        eid = str(e.get("id") or e.get("element_id") or "").strip().lower()
        if not UUID_RE.match(eid):
            continue
        els.append({"id": eid, "name": str(e.get("name") or "")[:120],
                    "status": str(e.get("status") or "").lower()[:30],
                    "category": str(e.get("category") or "")[:40]})
    used, agents = set(), {}
    for agent in AGENTS:
        exact, loose = [], []
        for e in els:
            n = _norm_name(e["name"])
            if n == f"uallak-{agent}":
                exact.append(e)
            elif re.search(rf"(^|[^a-z0-9])uallak-{agent}$", n):
                loose.append(e)
        best = exact or loose
        candidates = [{"id": e["id"], "name": e["name"], "status": e["status"]} for e in exact + loose]
        entry = {"element_id": "", "name": "", "status": "missing", "source": "auto",
                 "match": "exact" if exact else "loose" if loose else "", "candidates": candidates}
        if len(best) == 1:
            e = best[0]
            entry.update(element_id=e["id"], name=e["name"],
                         status="ready" if e["status"] == "completed" else "not_ready")
        elif len(best) > 1:
            entry["status"] = "conflict"
        override = str(overrides.get(agent) or "").strip().lower()
        if override:
            found = next((e for e in els if e["id"] == override), None)
            entry.update(element_id=override, source="override", name=(found or {}).get("name", ""),
                         status="ready" if (found or {}).get("status") == "completed"
                         else "not_ready" if found else "override_unlisted")
        if entry["element_id"]:
            used.add(entry["element_id"])
        used.update(c["id"] for c in candidates)
        agents[agent] = entry
    unmatched = [e for e in els if e["id"] not in used and _norm_name(e["name"]).startswith(("uallak", "char_uallak"))]
    others = sum(1 for e in els if e["id"] not in used) - len(unmatched)
    return {"agents": agents, "unmatched": unmatched, "other_elements": others}


def mapping() -> dict:
    """The stored mapping (never raises; unreadable = empty)."""
    try:
        data = _read(MAPPING_KEY)
    except Exception as e:
        print(f"[{SERVICE_NAME}] mapping unreadable ({type(e).__name__})")
        data = {}
    data.setdefault("agents", {})
    data.setdefault("overrides", {})
    data.setdefault("unmatched", [])
    data.setdefault("reports", [])
    return data


def sync(force: bool = True, _list=None) -> dict:
    """Re-reads the element list from Higgsfield and re-matches. The mapping
    is saved even when the read fails (last_error), keeping the last good
    match. `_list` is the test seam (returns the element list)."""
    data = mapping()
    data["last_sync"] = _now().isoformat()
    try:
        if _list is not None:
            elements = _list()
        else:
            if not configured():
                raise CharacterUnavailable("*", "not_configured")
            from core import media_gen_service as gen
            elements = gen.list_elements(agents_key())
        matched = match_elements(elements, data.get("overrides"))
        data.update(matched)
        data["elements"] = [{"id": e.get("id"), "name": str(e.get("name") or "")[:120],
                             "status": str(e.get("status") or "")[:30]}
                            for e in elements if isinstance(e, dict)][:300]
        data["last_error"] = ""
    except CharacterUnavailable as e:
        data["last_error"] = e.reason
    except Exception as e:
        data["last_error"] = f"{type(e).__name__}: {str(e)[:200]}"
        _alert([f"agent elements: re-read failed ({data['last_error']})"])
    _write(MAPPING_KEY, data)
    return view(data)


def sync_due(_list=None) -> dict:
    """The minute tick: re-read when the last read is older than SYNC_HOURS.
    Never raises."""
    try:
        data = mapping()
        last = data.get("last_sync") or ""
        if last:
            then = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            if _now() - then < timedelta(hours=SYNC_HOURS):
                return {"skipped": "fresh"}
        if not configured() and _list is None:
            return {"skipped": "not_configured"}
        out = sync(_list=_list)
        return {"synced": True, "error": out.get("last_error", "")}
    except Exception as e:
        return {"error": type(e).__name__}


def set_override(agent: str, element_id: str = "", by: str = "admin") -> dict:
    """The admin's manual pick for one agent ("" clears it). Re-matches from
    the stored element list at once — no Higgsfield call."""
    agent = clean_agent(agent)
    element_id = str(element_id or "").strip().lower()
    if element_id and not UUID_RE.match(element_id):
        raise ValueError("element_id must be an element UUID")
    data = mapping()
    if element_id:
        data["overrides"][agent] = element_id
    else:
        data["overrides"].pop(agent, None)
    data.update(match_elements(data.get("elements") or [], data["overrides"]))
    data.setdefault("history", []).append({"at": _now().isoformat(), "agent": agent, "element_id": element_id,
                                           "by": str(by)[:40]})
    data["history"] = data["history"][-50:]
    _write(MAPPING_KEY, data)
    return view(data)


def element_for(agent: str, data: dict = None) -> str:
    """The usable element id for `agent`, or CharacterUnavailable."""
    agent = clean_agent(agent)
    data = data if data is not None else mapping()
    entry = (data.get("agents") or {}).get(agent) or {}
    if entry.get("status") not in ("ready", "override_unlisted") or not entry.get("element_id"):
        raise CharacterUnavailable(agent, entry.get("status") or "missing")
    return entry["element_id"]


def view(data: dict = None) -> dict:
    """The admin screen: every agent with its element name, id and status,
    the unmatched elements, conflicts, the last read."""
    data = data if data is not None else mapping()
    rows = []
    for agent in AGENTS:
        e = (data.get("agents") or {}).get(agent) or {}
        rows.append({"agent": agent, "element_id": e.get("element_id", ""), "name": e.get("name", ""),
                     "status": e.get("status", "missing"), "source": e.get("source", "auto"),
                     "match": e.get("match", ""), "candidates": e.get("candidates") or [],
                     "override": (data.get("overrides") or {}).get(agent, "")})
    return {"agents": rows, "unmatched": data.get("unmatched") or [],
            "other_elements": data.get("other_elements", 0), "last_sync": data.get("last_sync", ""),
            "last_error": data.get("last_error", ""), "configured": configured(),
            "reports": (data.get("reports") or [])[-20:], "poses": pose_counts(),
            "elements": [e for e in data.get("elements") or [] if isinstance(e, dict)][:300]}


# ─── THE guardrail ───────────────────────────────────────────────────────────

def create_element(*args, by: str = "agent", **kwargs):
    """Refused, always. Elements are built by the manager from approved source
    images, by hand, in Higgsfield — never by an agent, never from generated
    output. Alerts so the attempt is seen."""
    _alert([f"refused: {by} tried to create/rebuild a reference element — agents never do this; "
            f"if an element is weak, report_element_weak"])
    raise ElementCreationRefused("agents never create or rebuild a reference element; report it instead")


rebuild_element = create_element
update_element = create_element


def report_element_weak(agent: str, reason: str, by: str = "media") -> dict:
    """An agent judges its (or another agent's) element weak: it REPORTS, the
    manager decides. Nothing else happens."""
    agent = clean_agent(agent)
    reason = re.sub(r"\s+", " ", str(reason or "")).strip()[:300] or "no reason given"
    entry = {"at": _now().isoformat(), "agent": agent, "reason": reason, "by": str(by)[:40]}
    try:
        data = mapping()
        data["reports"] = (data.get("reports") or [])[-49:] + [entry]
        _write(MAPPING_KEY, data)
    except Exception:
        pass
    _alert([f"agent element for {agent} reported weak by {by}: {reason} — the manager rebuilds it from "
            f"approved source images if needed"])
    return entry


# ─── Prompts ─────────────────────────────────────────────────────────────────

def placeholder(element_id: str) -> str:
    return f"<<<{element_id}>>>"


def _strip_placeholders(text: str) -> str:
    return re.sub(r"\s{2,}", " ", PLACEHOLDER_RE.sub("", str(text or ""))).strip()


def in_scene_prompt(scene: str, agents: list, data: dict = None) -> str:
    """The scene prompt with ONE placeholder per character and the style
    clause, always. Placeholders the model wrote itself are removed first."""
    agents = [clean_agent(a) for a in agents or []]
    if not agents:
        raise ValueError("in_scene needs at least one character")
    if len(set(agents)) != len(agents):
        raise ValueError("a character appears twice")
    if len(agents) > MAX_CHARACTERS:
        raise ValueError(f"at most {MAX_CHARACTERS} characters in one image")
    data = data if data is not None else mapping()
    ids = [element_for(a, data) for a in agents]
    who = " and ".join(placeholder(i) for i in ids)
    scene = _strip_placeholders(scene).rstrip(". ")
    prompt = f"{scene}. Featuring {who}, {STYLE_CLAUSE}."
    return with_style(prompt)


def with_style(prompt: str) -> str:
    """The style clause is in every character prompt — appended if missing."""
    prompt = str(prompt or "").strip()
    if STYLE_CLAUSE not in prompt:
        prompt = f"{prompt.rstrip('. ')}. {STYLE_CLAUSE}."
    return prompt


def pose_prompt(agent: str, pose: str, outfit: str = "", framing: str = "full body", data: dict = None) -> str:
    """A composite pose: the character alone on a plain background (the
    background is removed afterwards)."""
    eid = element_for(agent, data)
    bits = [placeholder(eid), _strip_placeholders(pose) or "standing, relaxed, friendly"]
    if outfit:
        bits.append(f"wearing {_strip_placeholders(outfit)}")
    bits.append(f"{framing or 'full body'} framing, the whole figure inside the frame, centred")
    bits.append(POSE_BACKGROUND)
    bits.append("no text, no props, no other people")
    return with_style(", ".join(bits))


def placeholders_in(prompt: str) -> list:
    return [m.strip("<> ").lower() for m in PLACEHOLDER_RE.findall(str(prompt or ""))]


# ─── Background removal → a verified transparent PNG ─────────────────────────

def ensure_transparent(png: bytes) -> bytes:
    """The image as an RGBA PNG, or BackgroundNotRemoved when it has no
    fully transparent pixels (a remover that returned the original)."""
    from PIL import Image
    img = Image.open(io.BytesIO(png))
    if img.mode not in ("RGBA", "LA") and not (img.mode == "P" and "transparency" in img.info):
        raise BackgroundNotRemoved("the image has no alpha channel")
    img = img.convert("RGBA")
    lo, hi = img.getchannel("A").getextrema()
    if lo > 0:
        raise BackgroundNotRemoved("no transparent pixels — the background was not removed")
    if hi == 0:
        raise BackgroundNotRemoved("the image is fully transparent")
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


# ─── The pose library ────────────────────────────────────────────────────────

TAG_FIELDS = ("outfit", "setting", "framing")


def _library() -> dict:
    try:
        data = _read(POSES_KEY)
    except Exception as e:
        print(f"[{SERVICE_NAME}] pose library unreadable ({type(e).__name__})")
        data = {}
    data.setdefault("poses", [])
    data.setdefault("last", {})
    return data


def _short(text, n: int = 160) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:n]


def add_pose(agent: str, file_id: str, transparent: bool, client_id: int = 0, outfit: str = "",
             setting: str = "", framing: str = "", description: str = "", tags: list = None,
             source: str = "generated", prompt: str = "") -> dict:
    """A character image into the library (approved False until a post that
    used it is approved). client_id 0 = a global cut-out; an in-scene image
    belongs to the client whose scene it is."""
    agent = clean_agent(agent)
    record = {"pose_id": secrets.token_hex(6), "agent": agent, "file_id": str(file_id or "")[:200],
              "transparent": bool(transparent), "client_id": int(client_id or 0),
              "outfit": _short(outfit, 80), "setting": _short(setting, 120), "framing": _short(framing, 40),
              "description": _short(description, 300),
              "tags": [_short(t, 40) for t in (tags or []) if _short(t, 40)][:12],
              "source": source if source in ("generated", "in_scene", "manual") else "generated",
              "approved": False, "created_at": _now().isoformat(), "uses": {"count": 0, "last_used_at": ""},
              "prompt": _short(_strip_placeholders(prompt), 400)}
    data = _library()
    data["poses"] = (data["poses"] + [record])[-MAX_POSES:]
    _write(POSES_KEY, data)
    return record


def get_pose(pose_id: str, data: dict = None) -> dict:
    data = data if data is not None else _library()
    return next((p for p in data["poses"] if p.get("pose_id") == str(pose_id)), {})


def record_pose_use(pose_id: str) -> dict:
    """A pose went into a post: counted, and remembered as that agent's last
    one (the next post gets another). Never raises."""
    try:
        data = _library()
        pose = get_pose(pose_id, data)
        if not pose:
            return {}
        u = pose.get("uses") or {}
        pose["uses"] = {"count": int(u.get("count") or 0) + 1, "last_used_at": _now().isoformat()}
        data["last"][pose["agent"]] = pose["pose_id"]
        _write(POSES_KEY, data)
        return pose
    except Exception as e:
        print(f"[{SERVICE_NAME}] pose use not recorded ({type(e).__name__})")
        return {}


def find_poses(agent: str, query: str = "", transparent=None, client_id: int = None, limit: int = 5,
               data: dict = None) -> list:
    """APPROVED poses of one agent, best first: match score (the same scorer
    as core/drive_assets) minus the usage penalty (core/asset_feedback), and
    the pose this agent used last always ranks last — the same pose is not
    repeated in consecutive posts. `transparent` True = cut-outs only;
    `client_id` given = global cut-outs plus that client's own scenes."""
    from core import asset_feedback, drive_assets
    agent = clean_agent(agent)
    data = data if data is not None else _library()
    last = (data.get("last") or {}).get(agent, "")
    qwords = drive_assets._query_words(query)
    usage = {"usage": {p["pose_id"]: p.get("uses") or {} for p in data["poses"]}}
    out = []
    for p in data["poses"]:
        if p.get("agent") != agent or not p.get("approved") or not p.get("file_id"):
            continue
        if transparent is not None and bool(p.get("transparent")) != bool(transparent):
            continue
        if client_id is not None and p.get("client_id") not in (0, int(client_id)):
            continue
        item = {"tags": list(p.get("tags") or []) + [p.get(k, "") for k in TAG_FIELDS],
                "description": p.get("description", ""), "name": p.get("prompt", "")}
        score = drive_assets._match_score(item, "", qwords) if qwords else 0.0
        pen = asset_feedback.penalty(usage, p["pose_id"])
        out.append({**p, "score": round(score - pen, 4), "repeat": p["pose_id"] == last})
    out.sort(key=lambda p: (p["repeat"], -p["score"], p.get("created_at", "")))
    return out[:max(1, int(limit))]


def pose_counts() -> dict:
    try:
        data = _library()
    except Exception:
        return {}
    out = {}
    for p in data["poses"]:
        c = out.setdefault(p.get("agent", "?"), {"approved": 0, "pending": 0, "transparent": 0})
        c["approved" if p.get("approved") else "pending"] += 1
        c["transparent"] += 1 if p.get("transparent") else 0
    return out


def approve_poses(pose_ids: list, tags: dict = None, client_id: int = 0, item_id: str = "") -> list:
    """The manager approved a post: every character image it used enters the
    library as approved, tagged. Never raises."""
    ids = [str(p) for p in pose_ids or [] if POSE_ID_RE.match(str(p))]
    if not ids:
        return []
    try:
        data = _library()
        done = []
        for p in data["poses"]:
            if p.get("pose_id") in ids:
                p["approved"] = True
                p["approved_at"] = _now().isoformat()
                if item_id:
                    p["approved_item"] = f"{int(client_id or 0)}:{str(item_id)[:20]}"
                for k, v in ((tags or {}).get(p["pose_id"]) or {}).items():
                    if k in TAG_FIELDS and v and not p.get(k):
                        p[k] = _short(v, 120)
                done.append(p["pose_id"])
        if done:
            _write(POSES_KEY, data)
        return done
    except Exception as e:
        _alert([f"poses not marked approved ({type(e).__name__}: {str(e)[:120]})"])
        return []


def on_items_approved(client_id: int, items: list) -> list:
    """social_queue.approve's hook: the poses an approved item carries
    (item["characters"] = [{"agent", "pose_id"}]) and the poses in an
    approved design's spec/slides."""
    ids = []
    for item in items or []:
        ids += [c.get("pose_id") for c in item.get("characters") or [] if isinstance(c, dict)]
        d = item.get("design") or {}
        ids += poses_in_design(d)
    return approve_poses(list(dict.fromkeys(i for i in ids if i)), client_id=client_id,
                         item_id=(items[0]["id"] if items else ""))


def poses_in_design(d: dict) -> list:
    ids = []
    spec = (d or {}).get("spec") or {}
    for part in [spec] + list((spec.get("role_overrides") or {}).values()):
        if (part.get("character") or {}).get("pose_id"):
            ids.append(part["character"]["pose_id"])
    ids += [(s.get("character") or {}).get("pose_id") for s in (d or {}).get("slides") or []]
    return [i for i in ids if i]


# ─── Files: our Shared Drive, uallak-agents/<agent>/ ─────────────────────────

def _pose_folder(agent: str) -> str:
    from core import drive_service as drive, drive_storage
    root = drive_storage.shared_parent_id()
    if not root:
        raise RuntimeError("Missing key: DRIVE_MEDIA_FOLDER_ID")
    return drive.ensure_folder(agent, drive.ensure_folder(POSE_FOLDER, root))


def upload_pose_png(agent: str, png: bytes) -> dict:
    from core import drive_service as drive, drive_storage
    with drive_storage.as_service_account():
        folder = _pose_folder(agent)
        stamp = _now().strftime("%Y%m%d-%H%M%S")
        return drive.upload_bytes(folder, f"{agent}-pose-{stamp}.png", png, "image/png")


def pose_bytes(pose_id: str) -> bytes:
    """The pose's PNG (the renderer's loader, "pose:<id>")."""
    pose = get_pose(pose_id)
    if not pose or not pose.get("file_id"):
        raise LookupError(f"no pose {pose_id}")
    from core import drive_service as drive, drive_storage
    with drive_storage.as_service_account():
        return drive.download_file(pose["file_id"])


def make_pose(agent: str, pose: str, outfit: str = "", framing: str = "full body", setting: str = "",
              aspect_ratio: str = "3:4", _gen=None, _remove=None, _upload=None) -> dict:
    """COMPOSITE step a: the pose with the placeholder on a plain background →
    background removal → a VERIFIED transparent PNG → the library. Runs on
    uallak's key (the account that owns the elements). Raises
    CharacterUnavailable / BackgroundNotRemoved / the vendor's error."""
    from core import media_gen_service as gen
    agent = clean_agent(agent)
    if _gen is None and not configured():
        raise CharacterUnavailable(agent, "not_configured")
    prompt = pose_prompt(agent, pose, outfit, framing)
    key = agents_key()
    made = (_gen or gen.generate_with_elements)(key, prompt, aspect_ratio)
    cut = (_remove or gen.remove_background)(key, made["url"])
    png = ensure_transparent(cut["content"])
    uploaded = (_upload or upload_pose_png)(agent, png)
    record = add_pose(agent, uploaded.get("id", ""), True, 0, outfit=outfit, setting=setting or "plain cut-out",
                      framing=framing, description=pose, tags=[w for w in re.findall(r"[a-z]{4,}", pose.lower())][:8],
                      source="generated", prompt=prompt)
    return {**record, "credits": (made.get("credits") or 0), "png": png}


def pose_for(agent: str, pose: str, outfit: str = "", framing: str = "full body", **kw) -> dict:
    """COMPOSITE: a stored approved cut-out FIRST (free, no generation); else
    a new one (make_pose). The result's use is recorded."""
    found = find_poses(agent, " ".join([pose, outfit, framing]), transparent=True, limit=1)
    if found and not found[0]["repeat"]:
        record_pose_use(found[0]["pose_id"])
        return {**found[0], "reused": True, "credits": 0}
    made = make_pose(agent, pose, outfit, framing, **kw)
    record_pose_use(made["pose_id"])
    return {**made, "reused": False}


# ─── Approval-card notices (shown, never blocking) ───────────────────────────

AI_PEOPLE_FORBIDDEN_RE = re.compile(
    r"(no|never|without|avoid|forbid\w*|do not use|don't use)\b[^.\n]{0,40}\b(ai|generated|artificial)[ -]?"
    r"(generated\s+)?(people|persons?|faces?|humans?|characters?|models?|avatars?)"
    r"|ללא\s+(אנשים|דמויות)\s+(שנוצרו|מחוללים|AI)|בלי\s+(אנשים|דמויות)\s+(של\s+)?(AI|בינה)", re.IGNORECASE)


def approval_flags(client_id: int, has_characters: bool, _brief_text=None) -> list:
    """["ai_person_forbidden"] when the client's brand brief forbids AI people,
    ["ai_label"] when an AI character appears (the platforms' AI-content
    label) — both only SHOWN on the approval card. Never raises."""
    if not has_characters:
        return []
    flags = ["ai_label"]
    try:
        if _brief_text is not None:
            text = _brief_text
        else:
            from core import brand_brief
            text = brand_brief.text(client_id) or ""
        if AI_PEOPLE_FORBIDDEN_RE.search(str(text)):
            flags.insert(0, "ai_person_forbidden")
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: brand brief unreadable for the AI-person check ({type(e).__name__})")
    return flags
