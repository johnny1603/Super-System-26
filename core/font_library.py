"""The fonts the slide renderer can draw with (2026-11-26, docs/FONTS.md).

Three sources, one shape (`Face`):

  bundled   Rubik and Assistant, variable files in assets/fonts — in the repo,
            always there; the starters' fonts and the end of every fallback
            chain.
  library   every Google Fonts family carrying the Hebrew subset (open source,
            OFL / Apache). The SET is pinned in assets/fonts/hebrew-fonts.lock.json
            and the files are installed into the image at build time by
            scripts/fetch_hebrew_fonts.py (the build fails when a pinned
            family is missing). Static files, one per weight the family ships.
  upload    a client's own typeface (core/brand_fonts): converted on upload,
            kept in the client's Drive, fetched and cached at render time.

Weights are REAL files only. A family without the asked weight draws with its
nearest real weight (`Face.weight_for`) — bold is never synthesized by
thickening the outline. `has_bold` / `nearest_with_bold` are what the branding
page says about it.

A face knows which characters it has (`covers`) from the ranges recorded when
the file was inspected — the lock for the library, the upload's record for a
client font — so the renderer chooses the font of each text run without
opening a font file.
"""
import bisect
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS_DIR = os.path.join(ROOT, "assets", "fonts")
LOCK_PATH = os.path.join(FONTS_DIR, "hebrew-fonts.lock.json")
BUNDLED = {"Rubik": {"file": "Rubik[wght].ttf", "axis": (300, 900)},
           "Assistant": {"file": "Assistant[wght].ttf", "axis": (200, 800)}}
LAST_RESORT = "Rubik"
CATEGORIES = ("sans", "serif", "display", "handwriting", "monospace")
REGULAR = 400
BOLD = 700
# The 22 letters and the five final forms: what "has Hebrew" means
HEBREW_LETTERS = "אבגדהוזחטיכלמנסעפצקרשתךםןףץ"

_STATE = {}


class FontUnavailable(RuntimeError):
    """The face cannot be drawn with right now. `reason`: not_installed |
    fetch_failed | unreadable | no_file."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


def library_dir() -> str:
    return os.environ.get("FONT_LIBRARY_DIR") or os.path.join(FONTS_DIR, "library")


def norm(name: str) -> str:
    """A family name as it is compared: case and spacing do not matter."""
    return "".join(str(name or "").lower().split())


class Face:
    """One family the renderer can draw with."""

    def __init__(self, family: str, source: str, weights, ranges=None, paths: dict = None, axis: tuple = None,
                 variable_path: str = "", fetch=None, hebrew: bool = True, latin: bool = True, ident: str = ""):
        self.family = family
        self.source = source                    # bundled | library | upload
        self.weights = sorted(int(w) for w in weights)
        self.axis = axis                        # (min, max) of a bundled variable file
        self.variable_path = variable_path
        self.hebrew, self.latin = bool(hebrew), bool(latin)
        self._paths = dict(paths or {})
        self._fetch = fetch                     # upload: weight -> a local file path (may raise FontUnavailable)
        self._starts = [r[0] for r in ranges] if ranges else None
        self._ends = [r[1] for r in ranges] if ranges else None
        self._known = {}
        self.key = f"{source}:{norm(family)}:{ident}"

    @property
    def has_bold(self) -> bool:
        return bool(self.axis and self.axis[1] >= BOLD) or any(w >= BOLD for w in self.weights)

    def has(self, ch: str) -> bool:
        hit = self._known.get(ch)
        if hit is None:
            if self._starts is None:
                hit = True                      # no record: assumed (bundled without a lock)
            else:
                i = bisect.bisect_right(self._starts, ord(ch)) - 1
                hit = i >= 0 and ord(ch) <= self._ends[i]
            self._known[ch] = hit
        return hit

    def covers(self, text: str) -> bool:
        return all(self.has(ch) for ch in text if not ch.isspace())

    def weight_for(self, weight: int) -> int:
        """The REAL weight that draws when `weight` is asked: itself inside a
        variable axis; otherwise the nearest file the family ships (a tie
        goes to the lighter one below 550, the heavier one above)."""
        weight = int(weight)
        if self.axis:
            return max(self.axis[0], min(self.axis[1], weight))
        if not self.weights:
            raise FontUnavailable("no_file", self.family)
        return min(self.weights, key=lambda w: (abs(w - weight), w if weight < 550 else -w))

    def path(self, weight: int) -> str:
        """The file of an exact real weight (see weight_for)."""
        if self.axis:
            return self.variable_path
        if self._fetch is not None:
            return self._fetch(int(weight))
        path = self._paths.get(int(weight)) or ""
        if not path or not os.path.isfile(path):
            raise FontUnavailable("not_installed", f"{self.family} {weight}")
        return path


# ─── The pinned library ──────────────────────────────────────────────────────

def lock() -> dict:
    """The pinned set ({} when the file is absent or unreadable)."""
    try:
        stamp = os.path.getmtime(LOCK_PATH)
    except OSError:
        return {}
    if _STATE.get("stamp") != stamp:
        try:
            with open(LOCK_PATH, encoding="utf-8") as f:
                value = json.load(f)
        except (OSError, ValueError):
            value = {}
        _STATE.clear()
        _STATE.update(stamp=stamp, lock=value, faces={},
                      by_name={norm(f["family"]): f for f in value.get("families") or []})
    return _STATE["lock"]


def _entry(family: str) -> dict:
    lock()
    return (_STATE.get("by_name") or {}).get(norm(family)) or {}


def installed(family: str) -> bool:
    """Every pinned file of the family is in the library directory."""
    if norm(family) in {norm(n) for n in BUNDLED}:
        return os.path.isfile(os.path.join(FONTS_DIR, BUNDLED[canonical(family)]["file"]))
    entry = _entry(family)
    root = library_dir()
    return bool(entry) and all(os.path.isfile(os.path.join(root, f["file"])) for f in entry["files"])


def canonical(family: str) -> str:
    """The family's own spelling ("heebo" -> "Heebo"), "" when it is neither
    bundled nor in the library."""
    for name in BUNDLED:
        if norm(name) == norm(family):
            return name
    return _entry(family).get("family", "")


def face(family: str):
    """The Face of a bundled or library family, or None when there is no such
    family. A library family whose files are not installed still gets a Face
    (its `path` raises FontUnavailable): what is known about it stays true."""
    name = canonical(family)
    if not name:
        return None
    lock()
    cache = _STATE.setdefault("faces", {})
    key = (name, library_dir())
    if key not in cache:
        entry = _entry(name)
        if name in BUNDLED:
            b = BUNDLED[name]
            cache[key] = Face(name, "bundled", range(b["axis"][0], b["axis"][1] + 1, 100), entry.get("ranges"),
                              axis=b["axis"], variable_path=os.path.join(FONTS_DIR, b["file"]))
        else:
            root = library_dir()
            cache[key] = Face(name, "library", entry["weights"], entry.get("ranges"),
                              paths={int(f["weight"]): os.path.join(root, f["file"]) for f in entry["files"]},
                              latin=entry.get("latin", True))
    return cache[key]


def bundled(family: str = LAST_RESORT) -> Face:
    return face(family if family in BUNDLED else LAST_RESORT) or Face(
        LAST_RESORT, "bundled", range(300, 901, 100), None, axis=BUNDLED[LAST_RESORT]["axis"],
        variable_path=os.path.join(FONTS_DIR, BUNDLED[LAST_RESORT]["file"]))


def catalog() -> list:
    """Every family of the library, in the lock's order: [{"family",
    "category", "weights", "has_bold", "latin", "installed"}]."""
    out = []
    for f in lock().get("families") or []:
        out.append({"family": f["family"], "category": f.get("category") or "display",
                    "weights": list(f["weights"]), "has_bold": max(f["weights"]) >= BOLD,
                    "latin": bool(f.get("latin", True)), "installed": installed(f["family"])})
    return out


def grouped() -> list:
    """[{"category", "families": [...]}] in CATEGORIES order — the picker."""
    rows = catalog()
    return [{"category": c, "families": [r for r in rows if r["category"] == c]}
            for c in CATEGORIES if any(r["category"] == c for r in rows)]


def nearest_with_bold(family: str) -> str:
    """The family closest to `family` that DOES ship a bold: same category,
    the nearest measured Hebrew width. "" when there is none (or the family
    is unknown)."""
    mine = _entry(family)
    if not mine:
        return ""
    width = float(mine.get("he_width") or 0)
    best = [f for f in lock().get("families") or []
            if f.get("category") == mine.get("category") and max(f["weights"]) >= BOLD
            and norm(f["family"]) != norm(family)]
    if not best:
        return ""
    return min(best, key=lambda f: (abs(float(f.get("he_width") or 0) - width), f["family"]))["family"]


def status() -> dict:
    """{"pinned", "installed", "missing": [family, …]} — the startup check."""
    rows = catalog()
    missing = [r["family"] for r in rows if not r["installed"]]
    return {"pinned": len(rows), "installed": len(rows) - len(missing), "missing": missing,
            "source": lock().get("source", ""), "fetched_at": lock().get("fetched_at", "")}
