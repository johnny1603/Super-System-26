"""Translation report for the CLIENT pages (2026-11-11, docs/MEDIA-TAB.md §i18n).

Stdlib only, read-only. Prints:
  1. the supported languages (from dashboard/assets/i18n.js SUPPORTED);
  2. per page: keys missing a language (they fall back to English at runtime,
     then Hebrew — dashboard/assets/i18n.js t());
  3. per page: Hebrew text that BYPASSES the translation system — markup text
     without a data-i18n attribute, and Hebrew string literals in the page's
     script outside its string table (comments ignored).

Run from the repo root:  python scripts/i18n_report.py   (exit 0 always; the
guard is tests/test_media_tab.py, which uses the same functions.)
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT_PAGES = ("dashboard/client/index.html", "dashboard/profile/index.html", "dashboard/login/index.html",
                "dashboard/onboarding/index.html", "dashboard/landing/index.html")
HEBREW = re.compile(r"[֐-׿]")
_ENTRY = re.compile(r"^\s*['\"]?([A-Za-z_][\w]*)['\"]?\s*:\s*\{")
_LANG = re.compile(r"(?<![\w$])(he|en|fr|ar|ru)\s*:")


def read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding="utf-8-sig") as f:
        return f.read()


def supported() -> list:
    m = re.search(r"var SUPPORTED = \[([^\]]*)\]", read("dashboard/assets/i18n.js"))
    return re.findall(r"'(\w+)'", m.group(1)) if m else []


def _strip_js_comments(code: str) -> str:
    """Remove // and /* */ comments, keeping string literals intact."""
    out, i, n, quote = [], 0, len(code), None
    while i < n:
        ch = code[i]
        if quote:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(code[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "'\"`":
            quote = ch
            out.append(ch)
            i += 1
            continue
        if code.startswith("//", i) and (i == 0 or code[i - 1] != ":"):
            j = code.find("\n", i)
            i = n if j == -1 else j
            continue
        if code.startswith("/*", i):
            j = code.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


TABLE_NAMES = ("DASH_I18N", "I18N", "Q_I18N", "CHROME")
# Hebrew SOURCE text that a table translates by id (onboarding: localizeQuestion)
SOURCE_NAMES = ("BASE_QUESTIONS", "CONDITIONAL_QUESTIONS")


def _match_brace(src: str, i: int) -> int:
    """Index just past the } matching the { at i (strings respected)."""
    depth, quote = 0, None
    while i < len(src):
        ch = src[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif src.startswith("//", i):
            nxt = src.find("\n", i)                    # a comment ("Google's") is not a quote
            i = len(src) if nxt == -1 else nxt
            continue
        elif src.startswith("/*", i):
            nxt = src.find("*/", i + 2)
            i = len(src) if nxt == -1 else nxt + 2
            continue
        elif ch in "'\"`":
            quote = ch
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(src)


def blocks(src: str, names) -> list:
    """(name, start, end) of every `const NAME = {…}` / `[…]` block."""
    out = []
    for m in re.finditer(r"\n(?:const|var|let) (" + "|".join(names) + r") = ([\{\[])", src):
        out.append((m.group(1), m.start(), _match_brace(src, m.end() - 1)))
    return out


def table_span(src: str):
    """(start, end) of the page's string table object literal, or None."""
    m = re.search(r"\n(?:const|var|let) (?:DASH_I18N|I18N|Q_I18N|CHROME) = \{", src)
    if not m:
        return None
    depth, i = 0, m.end() - 1
    quote = None
    while i < len(src):
        ch = src[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "'\"`":
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return m.start(), i + 1
        i += 1
    return None


def tables(src: str) -> list:
    """Every string table (and translated source block) in the page."""
    return [(a, b) for _n, a, b in blocks(src, TABLE_NAMES + SOURCE_NAMES)]


def _entries(src: str, start: int, end: int):
    """(key, text) of each top-level `key: {…}` entry of an object literal."""
    i = src.index("{", start) + 1
    while i < end:
        m = re.compile(r"\s*['\"]?([A-Za-z_]\w*)['\"]?\s*:\s*\{").match(src, i)
        if not m:
            nxt = src.find("\n", i)
            if nxt == -1 or nxt >= end:
                return
            i = nxt + 1
            continue
        close = _match_brace(src, m.end() - 1)
        yield m.group(1), src[m.end() - 1:close]
        i = close


def missing_languages(src: str, langs: list) -> dict:
    """{key: [missing langs]} for every entry of the page's tables; for a
    translated source block (onboarding's questions), the ids its table
    (Q_I18N) doesn't carry in every non-Hebrew language."""
    out = {}
    found = blocks(src, TABLE_NAMES)
    for name, start, end in found:
        for key, text in _entries(src, start, end):
            have = set(_LANG.findall(_strip_js_comments(text)))
            if name == "Q_I18N":
                have.add("he")                 # Hebrew lives in the question bank itself
            gaps = [l for l in langs if l not in have]
            if have and gaps:
                out[key] = gaps
    q = next(((a, b) for n, a, b in found if n == "Q_I18N"), None)
    if q:
        carried = {k: set(_LANG.findall(t)) for k, t in _entries(src, *q)}
        for _n, a, b in blocks(src, SOURCE_NAMES):
            for qid in set(re.findall(r"\bid:\s*['\"](\w+)['\"]", src[a:b])):
                gaps = [l for l in langs if l != "he" and l not in carried.get(qid, set())]
                if gaps:
                    out[qid] = gaps
    return out


def wrong_script(src: str, langs: list) -> dict:
    """{key: [langs]} whose translation still holds Hebrew (a non-Hebrew slot
    with Hebrew letters = shown in Hebrew to that reader)."""
    out = {}
    for start, end in tables(src):
        for m in re.finditer(r"(?<![\w$])(en|fr|ar|ru)\s*:\s*('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|`(?:[^`\\]|\\.)*`)",
                             src[start:end]):
            line_start = src.rfind("\n", 0, start + m.start()) + 1
            if "i18n-ok" in src[line_start:src.find("\n", start + m.start())]:
                continue                       # Hebrew kept on purpose (e.g. Israeli form names)
            if HEBREW.search(m.group(2)):
                key = _ENTRY.match(src[line_start:src.find("\n", start + m.start())])
                name = key.group(1) if key else f"line {src.count(chr(10), 0, start + m.start()) + 1}"
                out.setdefault(name, []).append(m.group(1))
    return out


def bypassing_hebrew(src: str) -> list:
    """[(line, text)] Hebrew outside the translation system."""
    found = []
    spans = tables(src)

    def in_table(pos):
        return any(a <= pos < b for a, b in spans)

    # Scripts: Hebrew string literals outside the tables, line by line
    # (comment lines and trailing // comments dropped). A line marked
    # `i18n-ok` is Hebrew on purpose: a Hebrew-only branch, or staff-only UI
    # (the admin view), which is Hebrew everywhere like the admin pages.
    for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", src):
        offset = m.start(1)
        in_block = False
        for line in m.group(1).split("\n"):
            pos, offset = offset, offset + len(line) + 1
            code = line.strip()
            if in_block:
                in_block = "*/" not in code
                continue
            if code.startswith("/*"):
                in_block = "*/" not in code
                continue
            if code.startswith(("//", "*")) or "i18n-ok" in line or in_table(pos):
                continue
            code = re.sub(r"(^|\s)//\s.*$", "", line)
            for lit in re.finditer(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|`(?:[^`\\]|\\.)*`?", code):
                if HEBREW.search(lit.group(0)):
                    found.append((src.count("\n", 0, pos) + 1, lit.group(0)[:90]))
    # Markup: text nodes with Hebrew whose element has no data-i18n*. Not
    # counted: <title> when the table has _page_title, text inside a
    # data-i18n-html element (replaced whole), and elements whose id the
    # script rewrites (getElementById('x').textContent/innerHTML = …).
    scripts = "\n".join(re.findall(r"<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", src))
    rewritten = set(re.findall(r"getElementById\('([\w-]+)'\)\.(?:textContent|innerHTML)\s*=", scripts))
    rewritten |= set(re.findall(r"getElementById\('([\w-]+)'\);\s*\n?\s*\w+\.(?:textContent|innerHTML)\s*=", scripts))
    has_title_key = "_page_title" in src
    body = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<!--[\s\S]*?-->",
                  lambda m: "\n" * m.group(0).count("\n"), src)
    html_i18n = [(m.start(), body.find(f"</{m.group(1)}>", m.end()))
                 for m in re.finditer(r"<(\w+)[^<>]*data-i18n-(?:html|skip)[^<>]*>", body)]
    for m in re.finditer(r"<(\w+)([^<>]*)>([^<]*)", body):
        tag, attrs, text = m.group(1), m.group(2), m.group(3)
        idm = re.search(r'\bid="([\w-]+)"', attrs)
        skip = ((tag == "title" and has_title_key) or (idm and idm.group(1) in rewritten)
                or any(a < m.start() < b for a, b in html_i18n))
        if HEBREW.search(text) and "data-i18n" not in attrs and not skip:
            found.append((body.count("\n", 0, m.start()) + 1, f"<{tag}> {text.strip()[:80]}"))
        for attr in re.finditer(r"\b(title|placeholder|aria-label|alt)=\"([^\"]*)\"", attrs):
            if HEBREW.search(attr.group(2)) and f"data-i18n-{attr.group(1)}" not in attrs:
                found.append((body.count("\n", 0, m.start()) + 1, f"{attr.group(1)}=\"{attr.group(2)[:70]}\""))
    return sorted(found)


def report() -> dict:
    langs = supported()
    out = {"languages": langs, "pages": {}}
    for page in CLIENT_PAGES:
        src = read(page)
        out["pages"][page] = {"missing": missing_languages(src, langs), "bypassing": bypassing_hebrew(src),
                              "wrong_script": wrong_script(src, langs)}
    return out


def main():
    data = report()
    print("Supported languages:", ", ".join(data["languages"]))
    for page, info in data["pages"].items():
        print(f"\n== {page}")
        per_lang = {}
        for key, gaps in info["missing"].items():
            for lang in gaps:
                per_lang.setdefault(lang, []).append(key)
        if per_lang:
            for lang in data["languages"]:
                keys = per_lang.get(lang) or []
                print(f"  missing {lang}: {len(keys)}" + (f" — {', '.join(sorted(keys)[:40])}" if keys else ""))
        else:
            print("  every key has every language")
        if info["wrong_script"]:
            print(f"  translations still in Hebrew: {len(info['wrong_script'])} — "
                  + ", ".join(f"{k} ({'/'.join(v)})" for k, v in sorted(info["wrong_script"].items())[:40]))
        if info["bypassing"]:
            print(f"  Hebrew outside the translation system: {len(info['bypassing'])}")
            for line, text in info["bypassing"][:80]:
                print(f"    line {line}: {text}")
        else:
            print("  no Hebrew outside the translation system")


if __name__ == "__main__":
    sys.exit(main())
