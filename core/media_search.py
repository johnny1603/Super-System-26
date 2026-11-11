"""THE file-name matcher (2026-11-11, docs/MEDIA-TAB.md) — one implementation
for the client's Media tab search AND the agents' find_media_file card
(drive_mirror.find). Pure Python, no I/O.

Why not Drive's own `name contains`: it matches the START of words only, so
"test 3" missed "test_3.mp4" (the underscore glues the words), a single digit
was dropped, Hebrew needed prefix-letter tricks, and Thai (no spaces between
words) could not match at all.

Rules:
- NFKC + casefold, combining marks of Latin/Hebrew/Arabic dropped (niqqud,
  accents: "café" = "cafe"), but NOT Thai/Indic marks — there they are
  vowels, part of the word.
- Separators don't count: spaces, _ - . , ( ) [ ] etc. "test 3", "test_3",
  "Test-3" and "TEST3" are the same name.
- The file extension is ignored for the exact match ("test 3.mp4" IS "test 3").
- Score, best first: exact name (300) > the whole query inside the name (200)
  > every word inside the name (100 + words) > some words (count). Nothing in
  common → no match.
- Words that describe the REQUEST ("the video", "הסרטון", "my") are dropped,
  unless that would leave nothing — then they are the name.
"""
import re
import unicodedata

# Words that describe what is asked, not the file's name (agents' and clients' words)
STOP_WORDS = {
    "the", "a", "an", "of", "for", "my", "our", "this", "that", "video", "videos", "clip", "file", "files",
    "photo", "photos", "image", "images", "picture", "use", "send", "upload", "please", "with", "from", "to",
    "in", "called", "named", "drive", "folder",
    "את", "של", "על", "עם", "זה", "הזה", "הזאת", "שלי", "שלנו", "סרטון", "הסרטון", "סרטונים", "וידאו",
    "הווידאו", "קובץ", "הקובץ", "תמונה", "התמונה", "תמונות", "קליפ", "הקליפ", "תשתמש", "תשלח", "תעלה",
    "בבקשה", "לי", "מה", "בשם", "שנקרא", "שנקראת", "בדרייב", "בתיקייה",
}
_HE_PREFIX = "הובלמש"
_KEEP_MARKS = ("THAI", "LAO", "DEVANAGARI", "BENGALI", "TAMIL", "TELUGU", "KANNADA", "MALAYALAM",
               "GUJARATI", "GURMUKHI", "ORIYA", "SINHALA", "KHMER", "MYANMAR", "TIBETAN",
               "COMBINING KATAKANA-HIRAGANA")    # が ≠ か
_EXT = re.compile(r"\.[A-Za-z0-9]{1,5}$")


def _keep_mark(ch: str) -> bool:
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return False
    return name.startswith(_KEEP_MARKS)


def _word_char(ch: str) -> bool:
    cat = unicodedata.category(ch)
    return cat[0] in "LN" or (cat[0] == "M" and _keep_mark(ch))


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text or "")).casefold()
    decomposed = unicodedata.normalize("NFD", text)
    kept = "".join(ch for ch in decomposed if unicodedata.category(ch)[0] != "M" or _keep_mark(ch))
    return unicodedata.normalize("NFC", kept)


def compact(text: str) -> str:
    """The name with every separator gone: "Test_3 .MP4" → "test3mp4"."""
    return "".join(ch for ch in _clean(text) if _word_char(ch))


def words(text: str) -> list:
    """The words of a request/name, in order: runs of letters/digits (Thai
    marks included). Single characters are kept — "3" matters."""
    out, current = [], []
    for ch in _clean(text):
        if _word_char(ch):
            current.append(ch)
        elif current:
            out.append("".join(current))
            current = []
    if current:
        out.append("".join(current))
    return out


def query_words(query: str) -> list:
    all_words = words(query)
    kept = [w for w in all_words if w not in STOP_WORDS]
    return kept or all_words


def _variants(word: str) -> tuple:
    """A Hebrew word with a prefix letter ("המוצר") also matches "מוצר"."""
    if len(word) >= 4 and word[0] in _HE_PREFIX and "֐" <= word[0] <= "׿":
        return (word, word[1:])
    return (word,)


def stem(name: str) -> str:
    return _EXT.sub("", str(name or "").strip())


def score(name: str, query: str) -> int:
    """How well a file NAME matches what someone typed/said. 0 = no match."""
    qwords = query_words(query)
    if not qwords:
        return 0
    joined = "".join(qwords)
    full = compact(name)
    if not full:
        return 0
    if compact(stem(name)) == joined:
        return 300
    if joined in full:
        return 200
    hits = sum(1 for w in qwords if any(v in full for v in _variants(w)))
    if hits == len(qwords):
        return 100 + hits
    # A lone digit/letter matched somewhere inside an unrelated name is noise
    return sum(1 for w in qwords if len(w) > 1 and any(v in full for v in _variants(w)))


def search(items: list, query: str = "", kind: str = "", folder: str = None, sort: str = "newest",
           best_only: bool = False) -> list:
    """Filter + rank Drive items (drive_mirror's shape: name, kind, folder,
    modified_time). `kind`: video / image / document ("" = all). `folder`: a
    folder path ("" = the root itself, None = everywhere; a path includes its
    subfolders). `sort`: newest / oldest / name — applied within a score when
    there is a query. best_only (the agents): only the top score group, so a
    near miss never sits next to the file they named."""
    out = []
    for item in items or []:
        if kind and item.get("kind") != kind and item.get("target_kind") != kind:
            continue          # a shortcut counts as the kind it points to
        if folder is not None:
            path = item.get("folder") or ""
            if folder == "" and path != "":
                continue
            if folder and path != folder and not path.startswith(folder + "/"):
                continue
        out.append(item)
    keyed = sorted(out, key=lambda i: compact(i.get("name"))) if sort == "name" else \
        sorted(out, key=lambda i: i.get("modified_time") or "", reverse=sort != "oldest")
    if not str(query or "").strip():
        return keyed
    scored = [(score(i.get("name"), query), n, i) for n, i in enumerate(keyed)]
    scored = [s for s in scored if s[0] > 0]
    if best_only and scored:
        best = max(s[0] for s in scored)
        scored = [s for s in scored if s[0] == best]
    return [i for _s, _n, i in sorted(scored, key=lambda s: (-s[0], s[1]))]
