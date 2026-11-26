"""The Hebrew font library (core/font_library, docs/FONTS.md).

Every Google Fonts family that carries the Hebrew subset — all open source
(OFL / Apache) — is installed into the image at BUILD time from a list pinned
in the repo:

    assets/fonts/hebrew-fonts.lock.json     the pinned set: family, category,
                                            every upright weight it ships, and
                                            per file its URL + SHA-256

    python scripts/fetch_hebrew_fonts.py --install     (the Dockerfile; stdlib only)
        downloads every pinned file, checks its SHA-256, and FAILS when a
        pinned family or file is missing or different. Nothing is guessed.

    python scripts/fetch_hebrew_fonts.py --refresh     (a developer; needs fonttools)
        asks Google Fonts for the CURRENT set of families with the "hebrew"
        subset — the Developer API when GOOGLE_FONTS_API_KEY is set, otherwise
        the public catalog the fonts.google.com site itself reads — downloads
        each upright weight, verifies the Hebrew alphabet is really in the
        file, and rewrites the lock. Review the diff, then commit it.

    python scripts/fetch_hebrew_fonts.py --check       (the Dockerfile, on the final image)
        reads the installed library back from disk: every pinned file with
        its SHA-256, no unpinned font file, a lock that pins something.
        Exit 1 = the build fails.

Italics are not installed: the slide renderer has no italic, and Hebrew has
none. Weights are the family's own files — bold is never synthesized.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCK_PATH = os.path.join(ROOT, "assets", "fonts", "hebrew-fonts.lock.json")
LIBRARY_DIR = os.path.join(ROOT, "assets", "fonts", "library")
CATALOG_URL = "https://fonts.google.com/metadata/fonts"
API_URL = "https://www.googleapis.com/webfonts/v1/webfonts"
CSS_URL = "https://fonts.googleapis.com/css2"
SUBSET = "hebrew"
CATEGORIES = {"sans serif": "sans", "sans-serif": "sans", "serif": "serif", "display": "display",
              "handwriting": "handwriting", "monospace": "monospace"}
# The 22 letters and the five final forms
HEBREW_LETTERS = "אבגדהוזחטיכלמנסעפצקרשתךםןףץ"
SAMPLE_HE = "המתנה שנשארת איתכם הרבה אחרי שהעטיפה נפתחה, כי כל זוג מעוצב פעם אחת בלבד"
RANGE_LIMIT = 0x3000            # coverage is recorded below this (Latin, Cyrillic, Hebrew, punctuation) …
EXTRA_BLOCKS = ((0xFB00, 0xFB4F),)   # … plus the Hebrew presentation forms


def _get(url: str, tries: int = 4) -> bytes:
    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "font-library-build"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except Exception as e:           # a build must not die on one dropped connection
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"could not fetch {url.split('?')[0]} ({type(last).__name__}: {str(last)[:120]})")


def file_name(family: str, weight: int) -> str:
    return f"{re.sub(r'[^A-Za-z0-9]+', '', family)}-{int(weight)}.ttf"


# ─── Install (the build) ─────────────────────────────────────────────────────

def load_lock() -> dict:
    with open(LOCK_PATH, encoding="utf-8") as f:
        return json.load(f)


def _install_one(dest: str, family: str, entry: dict) -> str:
    """"" when the file is in place with the pinned hash, else what is wrong."""
    path = os.path.join(dest, entry["file"])
    try:
        with open(path, "rb") as f:
            if hashlib.sha256(f.read()).hexdigest() == entry["sha256"]:
                return ""
    except OSError:
        pass
    try:
        data = _get(entry["url"])
    except RuntimeError as e:
        return f"{family} {entry['weight']}: {e}"
    digest = hashlib.sha256(data).hexdigest()
    if digest != entry["sha256"]:
        return f"{family} {entry['weight']}: SHA-256 {digest[:16]}… is not the pinned {entry['sha256'][:16]}…"
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return ""


def lock_problems(lock: dict) -> list:
    """What makes the LOCK itself unusable: an empty or cut-off lock must fail
    the build — "nothing pinned, nothing missing" is not a pass."""
    families = lock.get("families") if isinstance(lock, dict) else None
    if not families:
        return ["the lock pins no families (empty or cut-off file)"]
    out = []
    if lock.get("count") != len(families):
        out.append(f"the lock says {lock.get('count')} families and lists {len(families)}")
    out += [f"{fam.get('family')}: pinned with no files" for fam in families if not fam.get("files")]
    return out


def install(dest: str) -> int:
    try:
        lock = load_lock()
    except (OSError, ValueError) as e:
        print(f"FONT LIBRARY BUILD FAILED — the lock cannot be read ({type(e).__name__}: {str(e)[:120]})")
        return 1
    problems = lock_problems(lock)
    jobs = []
    if not problems:
        os.makedirs(dest, exist_ok=True)
        jobs = [(fam["family"], entry) for fam in lock["families"] for entry in fam["files"]]
        with ThreadPoolExecutor(max_workers=8) as pool:
            left = [j for j, p in zip(jobs, pool.map(lambda j: _install_one(dest, *j), jobs)) if p]
        # a dropped connection is not a broken pin: what failed gets ONE more pass, one file at a time
        problems = [p for p in (_install_one(dest, *j) for j in left) if p]
    if problems:
        print(f"FONT LIBRARY BUILD FAILED — {len(problems)} problem(s), pinned file(s) missing or different:")
        for p in problems:
            print("  " + p)
        return 1
    print(f"font library: {len(lock['families'])} Hebrew families, {len(jobs)} files installed in {dest}")
    return 0


def check(dest: str) -> int:
    """The installed set against the lock, read back from disk: every pinned
    file present with its SHA-256, and no font file the lock does not pin.
    The Dockerfile runs it on the FINAL image — a mismatch fails the build."""
    try:
        lock = load_lock()
    except (OSError, ValueError) as e:
        print(f"FONT LIBRARY CHECK FAILED — the lock cannot be read ({type(e).__name__}: {str(e)[:120]})")
        return 1
    bad = lock_problems(lock)
    pinned = set()
    for fam in (lock.get("families") or []) if not bad else []:
        for entry in fam["files"]:
            pinned.add(entry["file"])
            try:
                with open(os.path.join(dest, entry["file"]), "rb") as f:
                    if hashlib.sha256(f.read()).hexdigest() != entry["sha256"]:
                        bad.append(f"{fam['family']} {entry['weight']}: different file")
            except OSError:
                bad.append(f"{fam['family']} {entry['weight']}: missing")
    if not bad:
        try:
            extra = sorted(n for n in os.listdir(dest) if n.lower().endswith((".ttf", ".otf")) and n not in pinned)
        except OSError:
            extra = []
        bad += [f"{name}: in the library and not pinned in the lock" for name in extra]
    if bad:
        print(f"FONT LIBRARY CHECK FAILED — {len(bad)} problem(s), the installed set is not the pinned set:")
        for line in bad:
            print("  " + line)
        return 1
    print(f"ok: {len(lock['families'])} families, {len(pinned)} files match the lock")
    return 0


# ─── Refresh (a developer) ───────────────────────────────────────────────────

def discover() -> tuple:
    """([{"family", "category", "weights"}], source) — the families that carry
    the Hebrew subset RIGHT NOW, upright weights only."""
    key = os.environ.get("GOOGLE_FONTS_API_KEY", "").strip()
    if key:
        data = json.loads(_get(f"{API_URL}?subset={SUBSET}&key={urllib.parse.quote(key)}"))
        out = []
        for item in data.get("items") or []:
            if SUBSET not in (item.get("subsets") or []):
                continue
            weights = sorted({400 if v == "regular" else int(v) for v in item.get("variants") or []
                              if v == "regular" or v.isdigit()})
            out.append({"family": item["family"], "category": item.get("category", ""), "weights": weights})
        return out, "Google Fonts Developer API (webfonts/v1), subset=hebrew"
    raw = _get(CATALOG_URL).decode("utf-8")
    data = json.loads(raw[raw.index("{"):])
    out = []
    for item in data.get("familyMetadataList") or []:
        if SUBSET not in (item.get("subsets") or []):
            continue
        weights = sorted({int(k) for k in (item.get("fonts") or {}) if k.isdigit()})
        out.append({"family": item["family"], "category": item.get("category", ""), "weights": weights})
    return out, "Google Fonts catalog (fonts.google.com/metadata/fonts), families whose subsets include hebrew"


_FACE_RE = re.compile(r"@font-face\s*\{(.*?)\}", re.S)


def css_files(family: str, weights: list) -> dict:
    """{weight: url} of the family's upright TrueType files (the css2 endpoint
    answers a client without WOFF2 support with one full .ttf per weight)."""
    spec = urllib.parse.quote(family).replace("%20", "+") + ":wght@" + ";".join(str(w) for w in weights)
    css = _get(f"{CSS_URL}?family={spec}").decode("utf-8")
    out = {}
    for block in _FACE_RE.findall(css):
        style = re.search(r"font-style:\s*(\w+)", block)
        weight = re.search(r"font-weight:\s*(\d+)", block)
        url = re.search(r"url\((https://[^)]+\.ttf)\)", block)
        if style and weight and url and style.group(1) == "normal":
            out[int(weight.group(1))] = url.group(1)
    return out


def analyse(data: bytes) -> dict:
    """Coverage and width of one font file (fonttools)."""
    import io
    from fontTools.ttLib import TTFont
    font = TTFont(io.BytesIO(data), lazy=True)
    cmap = font.getBestCmap() or {}
    points = sorted(c for c in cmap if c < RANGE_LIMIT or any(a <= c <= b for a, b in EXTRA_BLOCKS))
    ranges = []
    for c in points:
        if ranges and c == ranges[-1][1] + 1:
            ranges[-1][1] = c
        else:
            ranges.append([c, c])
    upem = font["head"].unitsPerEm
    hmtx = font["hmtx"]
    advance = sum(hmtx[cmap[ord(ch)]][0] for ch in SAMPLE_HE if ord(ch) in cmap)
    return {"missing_hebrew": [ch for ch in HEBREW_LETTERS if ord(ch) not in cmap], "ranges": ranges,
            "he_width": round(advance / upem / len(SAMPLE_HE), 4),
            "latin": all(ord(ch) in cmap for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")}


def refresh(dest: str) -> int:
    families, source = discover()
    os.makedirs(dest, exist_ok=True)
    locked, skipped = [], []
    for fam in sorted(families, key=lambda f: f["family"].lower()):
        name = fam["family"]
        urls = css_files(name, fam["weights"])
        lost = [w for w in fam["weights"] if w not in urls]
        if lost:
            print(f"FAILED  {name}: no file offered for weight(s) {lost}")
            return 1
        files, facts = [], None
        for weight in fam["weights"]:
            data = _get(urls[weight])
            with open(os.path.join(dest, file_name(name, weight)), "wb") as f:
                f.write(data)
            files.append({"weight": weight, "file": file_name(name, weight), "url": urls[weight],
                          "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
            if facts is None or abs(weight - 400) < abs(facts[0] - 400):
                facts = (weight, analyse(data))
        info = facts[1]
        if info["missing_hebrew"]:
            # listed under the Hebrew subset but the file lacks letters: never offered as a Hebrew font
            skipped.append({"family": name, "missing": "".join(info["missing_hebrew"])})
            print(f"skipped {name}: the file lacks {''.join(info['missing_hebrew'])}")
            continue
        locked.append({"family": name, "category": CATEGORIES.get(fam["category"].lower(), "display"),
                       "weights": fam["weights"], "latin": info["latin"], "he_width": info["he_width"],
                       "ranges": info["ranges"], "files": files})
        print(f"ok      {name}: {fam['weights']}")
    lock = {"source": source, "subset": SUBSET, "fetched_at": time.strftime("%Y-%m-%d", time.gmtime()),
            "listed": len(families), "count": len(locked), "skipped": skipped,
            "note": "Written by scripts/fetch_hebrew_fonts.py --refresh. Never edit by hand.",
            "families": locked}
    with open(LOCK_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(lock, ensure_ascii=False, indent=1, separators=(",", ": ")))
        f.write("\n")
    print(f"\n{len(locked)} of {len(families)} listed families pinned ({sum(len(f['files']) for f in locked)} files) "
          f"from: {source}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--install", action="store_true")
    mode.add_argument("--refresh", action="store_true")
    mode.add_argument("--check", action="store_true")
    parser.add_argument("--dest", default=os.environ.get("FONT_LIBRARY_DIR") or LIBRARY_DIR)
    args = parser.parse_args()
    if args.install:
        return install(args.dest)
    if args.check:
        return check(args.dest)
    return refresh(args.dest)


if __name__ == "__main__":
    sys.exit(main())
