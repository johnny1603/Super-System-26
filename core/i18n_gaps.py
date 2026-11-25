"""Translation gaps, server + pages (2026-11-19, docs/LANGUAGES.md).

Read-only, stdlib only (AST + the page report in scripts/i18n_report.py):

- `hardcoded(path)`  Hebrew string literals in a Python module that BYPASS the
  translation tables — not in a 5-language table (a dict with he/en/fr/ar/ru
  keys, or a 5-argument _five/_txt call), not a regex, not a prompt/pattern
  constant, not a docstring. tests/test_language_separation.py fails when one
  appears in a CLIENT_FACING module (outside ALLOW).
- `table_gaps(path)` 5-language tables in a module whose slot for a language is
  missing, empty, or still Hebrew.
- `report()`         per language: what is missing, server and pages — shown
  to the owner at /admin/i18n/ (GET /api/admin/i18n/gaps).
"""
import ast
import io
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LANGS = ("he", "en", "fr", "ar", "ru")
HEB = re.compile(r"[֐-׿]")
_REGEX_CALLS = {"compile", "search", "match", "sub", "findall", "fullmatch", "split", "finditer"}
_DATA_NAME = re.compile(r"(RULE|SYSTEM|PROMPT|_RE|PATTERN|PATTERNS|WORDS|KEYWORDS|HINTS?|_HE)$")

# Modules whose strings reach a CLIENT (chat lines, cards, notifications,
# emails, dashboard data). A Hebrew literal outside a table fails the test.
CLIENT_FACING = (
    "agents/morning_briefing.py", "agents/engagement_agent.py", "agents/media_agent.py",
    "agents/interview_agent.py", "agents/store_agent.py", "agents/support_agent.py",
    "core/agent_actions.py", "core/chat_uploads.py", "core/chat_approval.py", "core/chat_fallback.py",
    "core/quarter_goals.py", "core/quarter_goals_actions.py", "core/organic_plan.py",
    "core/organic_plan_actions.py", "core/home_focus.py", "core/content_team.py", "core/content_team_actions.py",
    "core/branding_actions.py", "core/seo_report_actions.py", "core/competitor_watch_actions.py",
    "core/tiktok_uploads.py", "core/client_language.py", "core/email_service.py",
)

# Hebrew on purpose: "file:fragment" — staff-only lines inside a client
# module, names, and the Hebrew SOURCE of a translated table. Keep it small.
ALLOW = (
    # staff-only (the admin chat / the owner's approval email / the admin dry run)
    # "file:@function" = every literal inside that (staff-only) function
    "core/agent_actions.py:לא הסתיים בזמן ונעצר", "core/agent_actions.py:לא בוצע:",
    "core/agent_actions.py:CHECK_QUESTIONS", "core/agent_actions.py:בהמשך לפנייה",
    "core/agent_actions.py:@_attach_label", "core/agent_actions.py:@_prepare_attach",
    "core/tiktok_uploads.py:@_notify_owner",
    "core/email_service.py:@send_admin_alert", "core/email_service.py:@_send_weekly_platform_report",
    "core/email_service.py:@send_operator_code", "core/email_service.py:@_send",
    # the specialists' names (Hebrew names, shown through the dashboard's table)
    "agents/support_agent.py:יואב", "agents/support_agent.py:מאיה", "agents/support_agent.py:אורי",
    "agents/support_agent.py:ליאור",
    # model-facing data (the model writes in reply_language)
    "agents/support_agent.py:Yoav is precise", "agents/support_agent.py:Meta — the client",
    "agents/support_agent.py:Maya is energetic", "agents/support_agent.py:Uri is methodical",
    "agents/support_agent.py:'website_overview' is REAL", "agents/support_agent.py:Lior is creative",
    "agents/support_agent.py:'media_usage_this_month'", "agents/support_agent.py:'google_ads_performance'",
    "agents/store_agent.py:היום", "agents/store_agent.py:אתמול", "agents/store_agent.py:השבוע",
    "agents/store_agent.py:הימים האחרונים", "agents/store_agent.py:החודש", "agents/store_agent.py:ממתינה לתשלום",
    "agents/store_agent.py:בטיפול", "agents/store_agent.py:בהמתנה", "agents/store_agent.py:הושלמה",
    "agents/store_agent.py:בוטלה", "agents/store_agent.py:הוחזרה", "agents/store_agent.py:נכשלה",
    "agents/store_agent.py:טיוטה", "agents/store_agent.py:בפח",
    "agents/morning_briefing.py:DELIVERED_ACTIONS", "agents/morning_briefing.py:CAPABILITY_HE",
    "agents/morning_briefing.py:קמפיינים ב",
)


# Modules OUTSIDE CLIENT_FACING that still hold Hebrew literals outside a table
# (staff pages, operator, model data, and client lines not yet moved). A ratchet:
# the count per file may only go DOWN (tests/test_language_separation.py);
# moving a file to zero = take it out of here and add it to CLIENT_FACING.
KNOWN_DEBT = {
    "core/alert_hygiene.py": 1,
    "core/anthropic_credits.py": 11,
    "core/api_server.py": 74,
    "core/automation_service.py": 1,
    "core/avatar_video.py": 26,
    "core/branding.py": 1,
    "core/bulk_delete.py": 1,
    "core/chat_automation.py": 1,
    "core/chat_route.py": 3,
    "core/client_auth.py": 2,
    "core/client_journey.py": 1,
    "core/client_settings.py": 14,          # admin-only labels (label_he); +2 drive-folders cap settings (publish snapshot removed 2026-11-24); +2 governance (package size, sync escalation) 2026-11-26
    "core/connection_catalog.py": 34,
    "core/connection_help.py": 2,
    "core/control_room.py": 17,
    "core/crm_service.py": 3,
    "core/drive_file_service.py": 1,
    "core/drive_merge.py": 5,
    "core/drive_mirror.py": 17,
    "core/drive_pull.py": 29,
    "core/elevenlabs_service.py": 4,
    "core/export_service.py": 7,
    "core/feature_catalog.py": 124,         # +5: connected_drive_folders, +5: media_library (2026-11-23), +3: avatars (2026-11-25), Hebrew like every entry
    "core/frustration.py": 13,
    "core/gmail_outreach.py": 1,
    "core/israel_calendar.py": 36,
    "core/landing_creative.py": 1,
    "core/landing_domains.py": 4,
    "core/landing_images.py": 2,
    "core/landing_pages.py": 29,
    "core/landing_tracking.py": 1,
    "core/media_search.py": 3,
    "core/operating_costs.py": 32,
    "core/operator_actions.py": 28,
    "core/operator_agent.py": 48,
    "core/operator_changes.py": 12,
    "core/operator_flags.py": 13,
    "core/operator_github.py": 6,
    "core/operator_insight.py": 11,
    "core/operator_presence.py": 10,
    "core/operator_threads.py": 2,
    "core/outreach_policy.py": 1,
    "core/outreach_tracker.py": 17,
    "core/ownership.py": 47,
    "core/paypal_service.py": 1,
    "core/performance.py": 1,
    "core/seo_report.py": 1,
    "core/site_updates.py": 7,
    "core/vendor_tools.py": 9,
    "agents/avatar_agent.py": 8,
    "agents/budget_agent.py": 1,
    "agents/content_docs_agent.py": 3,
    "agents/crm_agent.py": 2,
    "agents/google_ads_agent.py": 2,
    "agents/landing_page_agent.py": 20,
    "agents/meta_ads_agent.py": 2,
    "agents/meta_content_agent.py": 2,
    "agents/onboarding_agent.py": 41,
    "agents/qa_agent_content.py": 10,
    "agents/tiktok_content_agent.py": 1,
    "agents/website_agent.py": 29,
    "agents/youtube_content_agent.py": 2,
}


def _rel(path: str) -> str:
    return os.path.relpath(path, ROOT).replace("\\", "/") if os.path.isabs(path) else path.replace("\\", "/")


def _parents(tree) -> dict:
    out = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            out[child] = node
    return out


def _lang_table(node) -> bool:
    if isinstance(node, ast.Dict):
        keys = {k.value for k in node.keys if isinstance(k, ast.Constant)}
        return len(keys & set(LANGS)) >= 3
    if isinstance(node, ast.Call):
        return len(node.args) >= 5 or any(k.arg in LANGS for k in node.keywords)
    return False


def _assigned_name(node) -> str:
    if isinstance(node, ast.Assign):
        return ",".join(getattr(t, "id", getattr(t, "attr", "")) or "" for t in node.targets)
    if isinstance(node, ast.AnnAssign):
        return getattr(node.target, "id", "")
    return ""


def _why_allowed(node, parents) -> str:
    p = parents.get(node)
    if isinstance(p, ast.Expr):
        return "docstring"
    while p is not None:
        if _lang_table(p):
            return "table"
        if isinstance(p, ast.Call):
            fn = p.func.attr if isinstance(p.func, ast.Attribute) else getattr(p.func, "id", "")
            if fn in _REGEX_CALLS:
                return "regex"
        name = _assigned_name(p)
        if name and _DATA_NAME.search(name):
            return "data:" + name
        if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            return ""
        p = parents.get(p)
    return ""


def _enclosing_name(node, parents) -> str:
    """The nearest assigned name, else the function; plus every enclosing
    function as "@name" (so a staff-only function can be allowed whole)."""
    near, funcs = "", []
    p = parents.get(node)
    while p is not None:
        if not near and not funcs:
            near = _assigned_name(p)
        if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append("@" + p.name)
        p = parents.get(p)
    return " ".join([near or (funcs[0][1:] if funcs else "")] + funcs)


def _mostly_latin(text: str) -> bool:
    """An English model-facing description that only quotes a Hebrew name."""
    heb = len(HEB.findall(text))
    latin = len(re.findall(r"[A-Za-z]", text))
    return latin >= 40 and heb * 6 < latin


def hardcoded(path: str) -> list:
    """[(line, text, owner)] Hebrew literals outside every table."""
    full = path if os.path.isabs(path) else os.path.join(ROOT, path)
    with io.open(full, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    parents = _parents(tree)
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and HEB.search(node.value):
            if not _why_allowed(node, parents) and not _mostly_latin(node.value):
                out.append((node.lineno, node.value.strip()[:90], _enclosing_name(node, parents)))
    return sorted(out)


def allowed(path: str, text: str, owner: str) -> bool:
    rel = _rel(path)
    for entry in ALLOW:
        file, _, frag = entry.partition(":")
        if file != rel:
            continue
        if frag.startswith("@"):
            if frag in owner.split():
                return True
        elif frag in text or frag == owner.split()[0] if owner else frag in text:
            return True
    return False


def violations(paths=CLIENT_FACING) -> list:
    out = []
    for path in paths:
        for line, text, owner in hardcoded(path):
            if not allowed(path, text, owner):
                out.append((path, line, text, owner))
    return out


def table_gaps(path: str) -> dict:
    """{lang: [(line, sample)]} 5-language tables missing / empty / Hebrew in a slot."""
    full = path if os.path.isabs(path) else os.path.join(ROOT, path)
    with io.open(full, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    out = {}
    for node in ast.walk(tree):
        slots = {}
        if isinstance(node, ast.Dict):
            keys = [k.value if isinstance(k, ast.Constant) else None for k in node.keys]
            if len(set(keys) & set(LANGS)) < 3 or "he" not in keys:
                continue
            slots = dict(zip(keys, node.values))
        elif isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("_five", "_txt", "F") and len(node.args) == 5:
            slots = dict(zip(LANGS, node.args))
        else:
            continue
        he = slots.get("he")
        if not (isinstance(he, ast.Constant) and isinstance(he.value, str) and he.value.strip()):
            continue
        sample = he.value.strip()[:50]
        for lang in LANGS[1:]:
            value = slots.get(lang)
            text = value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else None
            if value is None or (text is not None and not text.strip()) or (text and HEB.search(text)):
                if text and HEB.search(text) and "i18n-ok" in sample:
                    continue
                out.setdefault(lang, []).append((node.lineno, sample))
    return out


def _python_files():
    for root in ("core", "agents"):
        for name in sorted(os.listdir(os.path.join(ROOT, root))):
            if name.endswith(".py"):
                yield f"{root}/{name}"


def report() -> dict:
    """What the owner reads: per language, the missing translations (server
    tables + client pages), and the Hebrew that bypasses the tables."""
    langs = list(LANGS)
    server_missing = {lang: [] for lang in langs[1:]}
    for path in _python_files():
        try:
            gaps = table_gaps(path)
        except (SyntaxError, OSError):
            continue
        for lang, rows in gaps.items():
            server_missing[lang].extend({"file": path, "line": line, "he": sample} for line, sample in rows)
    pages_missing = {lang: [] for lang in langs[1:]}
    pages_bypassing = []
    try:
        import sys
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import i18n_report
        pages = i18n_report.report()["pages"]
        for page, info in pages.items():
            for key, missing in info["missing"].items():
                for lang in missing:
                    if lang in pages_missing:
                        pages_missing[lang].append({"page": page, "key": key})
            for key, wrong in info["wrong_script"].items():
                for lang in wrong:
                    if lang in pages_missing:
                        pages_missing[lang].append({"page": page, "key": key, "hebrew_in_slot": True})
            pages_bypassing.extend({"page": page, "line": line, "text": text} for line, text in info["bypassing"])
    except Exception as e:
        pages_bypassing.append({"page": "-", "line": 0, "text": f"page report failed: {type(e).__name__}"})
    hard = [{"file": f, "line": line, "text": text, "in": owner.split()[0] if owner else ""}
            for f, line, text, owner in violations()]
    debt = []
    for path, baseline in sorted(KNOWN_DEBT.items()):
        try:
            now = len(hardcoded(path))
        except (SyntaxError, OSError):
            continue
        debt.append({"file": path, "hebrew_literals": now, "baseline": baseline})
    return {
        "languages": langs,
        "per_language": {lang: {"server_tables": len(server_missing[lang]), "pages": len(pages_missing[lang])}
                         for lang in langs[1:]},
        "server_missing": server_missing,
        "pages_missing": pages_missing,
        "pages_hebrew_outside_tables": pages_bypassing,
        "client_paths_hebrew_outside_tables": hard,
        "not_yet_swept": debt,
    }
