"""THE list of tool and vendor names that never reach a client, and the plain
words that replace them (2026-11-16, docs/VENDOR-NAMES.md).

These are the tools that run BEHIND our service: the AI video, image and voice
tools, the SEO tools, our hosting, the automation and editing tools. Clients
read what a thing does ("the AI video tool"), never whose it is.

NOT on this list, on purpose: the client's OWN platforms, the accounts they
own and connect (Google, Google Ads, YouTube, Meta / Facebook / Instagram,
TikTok, WordPress, WhatsApp, Gmail). Naming those is how a client knows what
to connect.

Allowed to name a vendor (the client must act on the vendor's own site):
the connection help and sign-up flow (core/connection_help,
core/connection_catalog), the ownership / handover view (core/ownership: the
client's own accounts there), the legal pages, and every admin-only view.

Used by:
- `scrub()`: the code backstop on text the models wrote before it reaches a
  client (the chat reply via vendor_tools.scrub_vendor_names, the idea cards,
  every card a client reads, the proposal's QA guard);
- `PROMPT_RULE`: the one line the generating prompts carry;
- `tests/test_vendor_names.py`: fails when a name appears in client-facing
  text.
"""
import re

LANGS = ("he", "en", "fr", "ar", "ru")


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


LABELS = {
    "video": _five("כלי הווידאו מבוסס AI", "the AI video tool", "l'outil vidéo IA", "أداة الفيديو بالذكاء الاصطناعي",
                   "ИИ-инструмент для видео"),
    "image": _five("כלי התמונות מבוסס AI", "the AI image tool", "l'outil d'images IA", "أداة الصور بالذكاء الاصطناعي",
                   "ИИ-инструмент для изображений"),
    "voice": _five("כלי הקול", "the voice tool", "l'outil vocal", "أداة الصوت", "голосовой инструмент"),
    "seo": _five("כלי הקידום המקצועיים", "the professional SEO tools", "les outils SEO professionnels",
                 "أدوات تحسين محركات البحث المهنية", "профессиональные SEO-инструменты"),
    "hosting": _five("האחסון שלנו", "our hosting", "notre hébergement", "الاستضافة لدينا", "наш хостинг"),
    "automation": _five("מערכת האוטומציה לשיחות", "the chat automation system", "le système d'automatisation des conversations",
                        "نظام أتمتة المحادثات", "система автоматизации переписки"),
    "editing": _five("כלי עריכת הווידאו", "the video editing tool", "l'outil de montage vidéo", "أداة تحرير الفيديو",
                     "инструмент монтажа видео"),
    "music": _five("ספריית המוזיקה", "the music library", "la bibliothèque musicale", "مكتبة الموسيقى", "музыкальная библиотека"),
}

# name pattern → kind. Word-bounded; the short ones need their version
# suffix so ordinary words are never touched.
VENDORS = (
    (r"higgs\s?field", "video"), (r"hey\s?gen", "video"), (r"kling(?:\s?ai)?", "video"), (r"veo\s?\d(?:\.\d)?", "video"),
    (r"veo", "video"), (r"sora(?:\s?\d)?", "video"), (r"seedance", "video"), (r"wan\s?2(?:\.\d)?", "video"),
    (r"runway\s?(?:ml|gen-?\d)", "video"), (r"synthesia", "video"),
    (r"nano\s?banana", "image"), (r"midjourney", "image"), (r"soul\s?image", "image"),
    (r"eleven\s?labs", "voice"),
    (r"semrush", "seo"), (r"ahrefs", "seo"), (r"seoptimer", "seo"), (r"data\s?for\s?seo", "seo"),
    (r"open\s?pagerank", "seo"), (r"domcop", "seo"),
    (r"insta\s?wp", "hosting"),
    (r"many\s?chat", "automation"), (r"make\.com", "automation"), (r"zapier", "automation"),
    (r"creatomate", "editing"),
    (r"uppbeat", "music"), (r"suno", "music"),
)
_RE = re.compile(r"\b(" + "|".join(p for p, _ in VENDORS) + r")\b(?:'s)?", re.IGNORECASE)
_URL = re.compile(r"(https?://\S+|www\.\S+)")
_KIND = [(re.compile(r"^(?:" + p + r")$", re.IGNORECASE), kind) for p, kind in VENDORS]

PROMPT_RULE = ("NEVER name a tool or vendor that runs behind our service (AI video, image or voice tools, SEO "
               "tools, hosting, automation or editing tools) in anything the client reads — say what it does "
               "(\"the AI video tool\"). The client's own platforms (Google, YouTube, Meta, Facebook, Instagram, "
               "TikTok, WordPress, WhatsApp) may be named.")


def _kind(name: str) -> str:
    name = re.sub(r"'s$", "", name.strip(), flags=re.IGNORECASE)
    return next((kind for pattern, kind in _KIND if pattern.match(name)), "video")


def find(text: str) -> list:
    return [m.group(0) for m in _RE.finditer(str(text or ""))]


def scrub(text: str, lang: str = "he") -> str:
    """Every vendor name in `text` → the plain words for what it does. A name
    inside brackets ("(ManyChat)") goes with its brackets."""
    if not text:
        return text or ""
    lang = lang if lang in LANGS else "he"
    text = str(text)

    def swap(match):
        return LABELS[_kind(match.group(1))][lang]

    def plain(part: str) -> str:
        # "(Higgsfield)" / "(Semrush/Ahrefs)": the bracket carried only the name — drop it whole
        part = re.sub(r"\s*\((?:\s*(?:" + "|".join(p for p, _ in VENDORS) + r")\s*[/,]?\s*)+\)", "", part,
                      flags=re.IGNORECASE)
        return re.sub(r"[ \t]{2,}", " ", _RE.sub(swap, part))
    # A link is never touched (a sign-up URL carries the vendor's domain on purpose)
    pieces = _URL.split(text)
    return "".join(piece if i % 2 else plain(piece) for i, piece in enumerate(pieces))


def guess_lang(text: str) -> str:
    """The script a text is written in (for a proposal, which carries no
    language field): he / ar / ru, French by its accents, else en."""
    text = str(text or "")
    if re.search(r"[\u0590-\u05FF]", text):
        return "he"
    if re.search(r"[\u0600-\u06FF]", text):
        return "ar"
    if re.search(r"[\u0400-\u04FF]", text):
        return "ru"
    if re.search(r"[éèêàçùœ]", text, re.IGNORECASE):
        return "fr"
    return "en"


def scrub_auto(text: str) -> str:
    """`scrub` in the language the text is written in (model messages carry none)."""
    return scrub(text, guess_lang(text))


def scrub_tree(node, lang: str = "he") -> int:
    """`scrub` on every string VALUE of a dict / list, in place. Returns how
    many strings changed."""
    changed = 0
    items = node.items() if isinstance(node, dict) else enumerate(node) if isinstance(node, list) else ()
    for key, value in list(items):
        if isinstance(value, str):
            clean = scrub(value, lang)
            if clean != value:
                node[key] = clean.strip()
                changed += 1
        elif isinstance(value, (dict, list)):
            changed += scrub_tree(value, lang)
    return changed
