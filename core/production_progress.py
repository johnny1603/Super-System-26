"""Real progress for a post's production (2026-11-25).

The steps are known when production starts (plan_for): one generation per
image/video slide, one pose + one background removal per character slide,
one render per text slide, the layout, and the assembly. Each step is
weighted by its EXPECTED duration — measured: every finished step's own
duration is kept per kind (app_settings `production_step_seconds`, the last
SAMPLES), and its median replaces the default once MIN_SAMPLES exist. Steps
that run together (GEN_PARALLEL / POSE_PARALLEL) share the wall time, so a
phase weighs its expected WALL time. A running step counts by how long it has
run against its expectation, capped at RUNNING_CAP: the bar moves at a
roughly even rate, never jumps 10% → 90% and sits, and never claims a step
done that isn't.

- never backwards: the shown fraction is the max of everything shown so far;
- never lies: 1.0 only when production ended; a failed step stops counting
  and its reason is listed (`problems`); a failed run freezes the bar;
- the current step in words ("מייצר פוזה 2 מתוך 4"): the step begun last
  that is still running;
- elapsed always; remaining only when every kind in the plan has been measured
  MIN_SAMPLES times, and marked an estimate.

State: app_settings `production_progress:<client_id>` = {item_id: record}
(its own row, never the Social queue's — parallel steps write it). The run
holds its tracker in a contextvar (parallel.gather copies contextvars), so
deep code reports with begin()/done() and a call outside a production is a
no-op.
"""
import contextvars
import statistics
import threading
import time
from datetime import datetime, timezone

SERVICE_NAME = "production_progress"
KEY_PREFIX = "production_progress:"
TIMES_KEY = "production_step_seconds"
KINDS = ("generate", "pose", "cutout", "render", "layout", "assemble")
DEFAULT_SECONDS = {"generate": 45.0, "pose": 40.0, "cutout": 2.0, "render": 0.4, "layout": 4.0, "assemble": 2.0}
PARALLEL = {"generate": 4, "pose": 4, "cutout": 4, "render": 1, "layout": 1, "assemble": 1}
PHASES = (("generate", ("generate",)), ("poses", ("pose", "cutout")), ("design", ("render", "layout")),
          ("assemble", ("assemble",)))
SAMPLES = 20
MIN_SAMPLES = 3
RUNNING_CAP = 0.9
FLUSH_S = 0.7
MAX_RECORDS = 10

_CURRENT = contextvars.ContextVar("production_tracker", default=None)
_ROW_LOCK = threading.Lock()        # the client's row is read-modify-written by every tracker of this process


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


STEP_TEXT = {
    "generate": _five("מייצר תמונה {k} מתוך {of}", "Making image {k} of {of}", "Création de l'image {k} sur {of}",
                      "إنشاء الصورة {k} من {of}", "Создаю изображение {k} из {of}"),
    "pose": _five("מייצר פוזה {k} מתוך {of}", "Making pose {k} of {of}", "Création de la pose {k} sur {of}",
                  "إنشاء الوضعية {k} من {of}", "Создаю позу {k} из {of}"),
    "cutout": _five("מסיר רקע מפוזה {k} מתוך {of}", "Removing the background of pose {k} of {of}",
                    "Détourage de la pose {k} sur {of}", "إزالة خلفية الوضعية {k} من {of}",
                    "Убираю фон позы {k} из {of}"),
    "render": _five("מרנדר שקופית {n}", "Rendering slide {n}", "Rendu de la diapositive {n}", "عرض الشريحة {n}",
                    "Отрисовываю слайд {n}"),
    "layout": _five("מעצב את השקופיות", "Laying out the slides", "Mise en page des diapositives", "تنسيق الشرائح",
                    "Верстаю слайды"),
    "assemble": _five("מרכיב את הפוסט בלשונית סושיאל", "Assembling the post in the Social tab",
                      "Assemblage du post dans l'onglet Social", "تجميع المنشور في تبويب السوشيال",
                      "Собираю пост во вкладке «Соцсети»"),
    "preparing": _five("מתכונן להפקה…", "Getting ready…", "Préparation…", "جارٍ التحضير…", "Готовлюсь…"),
    "parallel": _five(" ({r} במקביל)", " ({r} at once)", " ({r} en parallèle)", " ({r} بالتوازي)", " ({r} параллельно)"),
}


def _pick(table: dict, lang: str) -> str:
    return table.get(lang) or table["he"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db():
    from agents.client_agent import _db as db
    return db()


def _read(key: str) -> dict:
    rows = (_db().table("app_settings").select("value").eq("key", key).limit(1).execute().data) or []
    value = rows[0].get("value") if rows else None
    return value if isinstance(value, dict) else {}


def _write(key: str, value: dict):
    _db().table("app_settings").upsert({"key": key, "value": value, "updated_at": _now_iso()},
                                       on_conflict="key").execute()


# ─── The plan ────────────────────────────────────────────────────────────────

def plan_for(slides: list) -> list:
    """The real step list of ONE post, in order. slides = the post's slides
    (asset_type, characters, character_mode)."""
    steps, text = [], []
    for n, s in enumerate(slides or [], 1):
        if s.get("asset_type") == "text_slide":
            text.append((n, s))
        else:
            steps.append({"id": f"generate:{n}", "kind": "generate", "n": n})
    for n, s in text:
        if s.get("characters") and (s.get("character_mode") or "composite") == "composite":
            steps.append({"id": f"pose:{n}", "kind": "pose", "n": n})
            steps.append({"id": f"cutout:{n}", "kind": "cutout", "n": n})
    if text:
        steps.append({"id": "layout", "kind": "layout", "n": 0})
    for n, _s in text:
        steps.append({"id": f"render:{n}", "kind": "render", "n": n})
    steps.append({"id": "assemble", "kind": "assemble", "n": 0})
    for s in steps:
        s["status"] = "pending"
    return steps


# ─── Measured step durations (the weights) ──────────────────────────────────

def measured() -> dict:
    try:
        data = _read(TIMES_KEY)
    except Exception:
        data = {}
    return {k: [float(x) for x in (data.get(k) or []) if isinstance(x, (int, float))] for k in KINDS}


def expected_seconds(times: dict = None) -> dict:
    """Per kind: the median of the measured durations once MIN_SAMPLES exist,
    else the default. {kind: (seconds, measured?)}."""
    times = measured() if times is None else times
    out = {}
    for k in KINDS:
        samples = times.get(k) or []
        out[k] = (statistics.median(samples), True) if len(samples) >= MIN_SAMPLES else (DEFAULT_SECONDS[k], False)
    return out


def record_durations(steps: list):
    """Every step that really ran (begun → done, not reused/skipped) adds its
    own duration to its kind's samples."""
    new = {}
    for s in steps:
        if s.get("status") == "done" and not s.get("skipped") and s.get("began") and s.get("ended"):
            new.setdefault(s["kind"], []).append(round(max(0.0, s["ended"] - s["began"]), 3))
    if not new:
        return
    try:
        data = _read(TIMES_KEY)
        for k, values in new.items():
            data[k] = ([float(x) for x in data.get(k) or []] + values)[-SAMPLES:]
        _write(TIMES_KEY, data)
    except Exception as e:
        print(f"[{SERVICE_NAME}] step durations not recorded ({type(e).__name__})")


# ─── The bar ─────────────────────────────────────────────────────────────────

def compute(record: dict, now: float = None, expected: dict = None, lang: str = "he") -> dict:
    """{"fraction", "text", "elapsed_s", "remaining_s", "estimated",
    "problems", "state"} from a stored record. Pure."""
    now = time.time() if now is None else now
    if record.get("expected"):
        # the weights this run started with — another run finishing never shifts this bar
        expected = {k: (float(v[0]), bool(v[1])) for k, v in record["expected"].items()}
    expected = expected or {k: (v, False) for k, v in DEFAULT_SECONDS.items()}
    steps = record.get("steps") or []
    started = float(record.get("t0") or now)
    elapsed = max(0.0, float(record.get("t1") or now) - started)
    if record.get("t1"):
        now = float(record["t1"])
    total_w, done_w, remaining_w = 0.0, 0.0, 0.0
    for _phase, kinds in PHASES:
        mine = [s for s in steps if s["kind"] in kinds]
        if not mine:
            continue
        weight = sum(expected[s["kind"]][0] / PARALLEL[s["kind"]] for s in mine)
        parts = []
        for s in mine:
            e = max(0.05, expected[s["kind"]][0])
            if s["status"] in ("done", "failed"):
                parts.append(1.0)
            elif s["status"] == "running":
                parts.append(min(RUNNING_CAP, max(0.0, now - float(s.get("began") or now)) / e))
            else:
                parts.append(0.0)
        p = sum(parts) / len(parts)
        total_w += weight
        done_w += weight * p
        remaining_w += weight * (1 - p)
    fraction = done_w / total_w if total_w else 0.0
    state = record.get("state") or "running"
    if state == "done":
        fraction = 1.0
    else:
        fraction = min(fraction, 0.99)
    fraction = max(float(record.get("shown") or 0.0), fraction)          # never backwards
    all_measured = all(expected[s["kind"]][1] for s in steps) if steps else False
    remaining = round(remaining_w, 1) if all_measured and state == "running" else None
    return {"fraction": round(fraction, 4), "text": step_text(record, lang), "elapsed_s": round(elapsed, 1),
            "remaining_s": remaining, "estimated": remaining is not None,
            "problems": [s["reason"] for s in steps if s["status"] == "failed" and s.get("reason")],
            "state": state}


def step_text(record: dict, lang: str = "he") -> str:
    """The step actually running: the one begun LAST that is still running."""
    steps = record.get("steps") or []
    running = [s for s in steps if s["status"] == "running"]
    if not running:
        if record.get("state") == "running" and not any(s["status"] != "pending" for s in steps):
            return _pick(STEP_TEXT["preparing"], lang)
        return ""
    cur = max(running, key=lambda s: float(s.get("began") or 0))
    same = [s for s in steps if s["kind"] == cur["kind"]]
    finished = sum(1 for s in same if s["status"] in ("done", "failed"))
    text = (_pick(STEP_TEXT[cur["kind"]], lang).replace("{k}", str(min(len(same), finished + 1)))
            .replace("{of}", str(len(same))).replace("{n}", str(cur.get("n") or "")))
    together = sum(1 for s in running if s["kind"] == cur["kind"])
    if together > 1:
        text += _pick(STEP_TEXT["parallel"], lang).replace("{r}", str(together))
    return text


# ─── The tracker (the running production) ────────────────────────────────────

class Tracker:
    def __init__(self, client_id: int, item_id: str, steps: list):
        self.client_id, self.item_id = int(client_id), str(item_id)
        self.lock = threading.Lock()
        self.expected = expected_seconds()
        self.record = {"item_id": self.item_id, "t0": time.time(), "started_at": _now_iso(), "state": "running",
                       "steps": steps, "shown": 0.0,
                       "expected": {k: [v[0], v[1]] for k, v in self.expected.items()}}
        self.last_write = 0.0
        self._flush(force=True)

    def _step(self, step_id: str):
        return next((s for s in self.record["steps"] if s["id"] == step_id), None)

    def begin(self, step_id: str):
        with self.lock:
            s = self._step(step_id)
            if not s or s["status"] != "pending":
                return
            s["status"], s["began"] = "running", time.time()
            self._flush(force=s["kind"] not in ("render", "cutout"))

    def done(self, step_id: str, ok: bool = True, reason: str = "", skipped: bool = False):
        with self.lock:
            s = self._step(step_id)
            if not s or s["status"] in ("done", "failed"):
                return
            if s["status"] == "pending":
                s["began"] = time.time()
            s["status"], s["ended"] = ("done" if ok else "failed"), time.time()
            if skipped:
                s["skipped"] = True
            if reason and not ok:
                s["reason"] = str(reason)[:300]
            self._flush(force=not ok or s["kind"] not in ("render", "cutout"))

    def finish(self, state: str):
        """Production ended: done = 1.0; failed / stopped / partial = frozen
        where it is. The measured durations feed the weights."""
        with self.lock:
            self.record["state"] = "done" if state == "done" else state
            self.record["t1"] = time.time()
            self._flush(force=True)
        record_durations(self.record["steps"])

    def snapshot(self, lang: str = "he") -> dict:
        return compute(self.record, expected=self.expected, lang=lang)

    def _flush(self, force: bool = False):
        now = time.monotonic()
        if not force and now - self.last_write < FLUSH_S:
            return
        self.record["shown"] = compute(self.record, expected=self.expected)["fraction"]
        self.record["updated_at"] = _now_iso()
        self.last_write = now
        try:
            key = f"{KEY_PREFIX}{self.client_id}"
            with _ROW_LOCK:            # two posts produced at once share the row: no lost update here
                self._merge(key)
        except Exception as e:
            print(f"[{SERVICE_NAME}] client {self.client_id}: progress not written ({type(e).__name__})")

    def _merge(self, key: str):
        """This post's record into the client's row; the other posts' stay as read."""
        data = _read(key)
        data[self.item_id] = self.record
        if len(data) > MAX_RECORDS:
            for old in sorted(data, key=lambda k: str((data[k] or {}).get("started_at", "")))[:-MAX_RECORDS]:
                data.pop(old, None)
        _write(key, data)


def start(client_id: int, item_id: str, slides: list) -> Tracker:
    """The tracker for this production, current in this context."""
    tracker = Tracker(client_id, item_id, plan_for(slides))
    _CURRENT.set(tracker)
    return tracker


def current():
    return _CURRENT.get()


def begin(step_id: str):
    t = _CURRENT.get()
    if t is not None:
        t.begin(step_id)


def done(step_id: str, ok: bool = True, reason: str = "", skipped: bool = False):
    t = _CURRENT.get()
    if t is not None:
        t.done(step_id, ok, reason, skipped)


def stop_tracking():
    _CURRENT.set(None)


SINCE_SLACK_S = 10


def read(client_id: int, item_id: str, lang: str = "he", since=None) -> dict:
    """What a card / the Social tab shows for this post's production, from
    the stored record; {} when there is none. `since` (a datetime): a record
    started before it is an earlier run of the same post → {}."""
    try:
        record = (_read(f"{KEY_PREFIX}{int(client_id)}") or {}).get(str(item_id))
    except Exception:
        return {}
    if not isinstance(record, dict) or not record.get("steps"):
        return {}
    if since is not None:
        try:
            if float(record.get("t0") or 0) < since.timestamp() - SINCE_SLACK_S:
                return {}
        except (AttributeError, TypeError, ValueError):
            pass
    return compute(record, lang=lang)


def read_all(client_id: int, lang: str = "he") -> dict:
    """{item_id: what the bar shows} for every stored record of the client
    (the Social tab's production cards). Never raises."""
    try:
        data = _read(f"{KEY_PREFIX}{int(client_id)}") or {}
    except Exception:
        return {}
    return {k: compute(v, lang=lang) for k, v in data.items() if isinstance(v, dict) and v.get("steps")}


def preparing(lang: str = "he") -> dict:
    """Before the tracker exists (the run's own request is on its way): 0,
    said in words — the bar is there from the very start."""
    return {"fraction": 0.0, "text": _pick(STEP_TEXT["preparing"], lang), "elapsed_s": 0.0, "remaining_s": None,
            "estimated": False, "problems": [], "state": "running"}
