"""Client ownership & portability (core/ownership + the api_server routes).

1. "החשבונות והנכסים שלי": each connected account with its identity, our
   access, since when and status — never a key, token or any part of one.
2. "ייצוא הכל" and the handover document: the session's client only.
3. The admin offboarding kit: handover FIRST, then our access is removed
   through the client's own disconnect path; nothing of the client's at a
   vendor is deleted.
4. The ownership clause in the sales chat, the report email and the terms,
   in all 5 languages.
5. NO client route can return a decrypted client secret: an AST taint check
   over every route in core/api_server.py, an allowlist of the modules that
   decrypt at all, and the client pages never naming a secret field.

Stdlib only. Run from the tests folder:  python -I -B test_ownership.py -v
"""
import ast
import os
import re
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

sys.modules.setdefault("httpx", types.ModuleType("httpx"))
_supabase = types.ModuleType("supabase")
_supabase.create_client = lambda *a, **k: None
sys.modules.setdefault("supabase", _supabase)

from core import ownership                     # noqa: E402
from core import secrets_vault                 # noqa: E402

VENDOR = re.compile(r"heygen|eleven\s?labs|higgsfield", re.IGNORECASE)
LANGS = ("he", "en", "fr", "ar", "ru")
SECRETS = ("ya29-REFRESH-TOKEN-SECRET", "sk_live_ELEVEN_SECRET_KEY", "wpuser:abcd efgh ijkl mnop")


def _src(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


API_SOURCE = _src("core", "api_server.py")
API_TREE = ast.parse(API_SOURCE)


def _disconnect_groups() -> dict:
    node = next(n for n in API_TREE.body if isinstance(n, ast.Assign)
                and any(getattr(t, "id", "") == "_DISCONNECT_GROUPS" for t in n.targets))
    return ast.literal_eval(node.value)


GROUPS = _disconnect_groups()


def _function(name: str) -> ast.FunctionDef:
    return next(n for n in API_TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)


def _row(platform, account_id, secret="", status="active", created_at="2026-10-01T10:00:00+00:00"):
    """A client_accounts row as the DB holds it, through the real public_account."""
    raw = {"id": hash(platform) % 1000, "client_id": 27, "platform": platform, "account_id": account_id,
           "status": status, "created_at": created_at, "access_token": secret,
           "access_token_enc": ("v1." + "Q" * 40) if secret else None,
           "secret_last4": secret[-4:] if secret else None, "secret_fingerprint": "f" * 16 if secret else None}
    return secrets_vault.public_account(raw)


ACCOUNTS = [
    _row("google_ads", "1234567890", SECRETS[0]),
    _row("meta_ads", "act_555", SECRETS[0]),
    _row("meta_page", "777", SECRETS[0]),
    _row("youtube_pending", "pending_channel", SECRETS[0]),
    _row("wordpress", "https://shop.example.co.il", SECRETS[2]),
    _row("elevenlabs", "elevenlabs", SECRETS[1]),
    _row("crm", "hubspot", SECRETS[1]),
    _row("manychat", "api_token", SECRETS[1]),
    _row("landing_domain", "go.example.co.il", "", status="active"),
]


class Stubbed(unittest.TestCase):
    """ownership's reads, replaced: no DB, no network."""

    def setUp(self):
        self.saved = {n: getattr(ownership, n) for n in
                      ("_accounts", "_asset_activity", "_count_landing_pages", "_count_leads")}
        ownership._accounts = lambda cid: list(ACCOUNTS)
        ownership._asset_activity = lambda cid: [{"action_type": "website_provisioned"},
                                                 {"action_type": "media_folder_created",
                                                  "details": {"folder_id": "FOLDER"}}]
        ownership._count_landing_pages = lambda cid: 2
        ownership._count_leads = lambda cid: 14
        self.modules = {}
        vt = types.ModuleType("core.vendor_tools")
        vt.status = lambda cid, tool: "connected"
        vt.client_label = lambda tool, lang="he": {"elevenlabs": "הקול שלך", "heygen": "דמות מצולמת שלך",
                                                   "higgsfield": "דמות AI"}[tool]
        crm = types.ModuleType("core.crm_service")
        crm.supported_vendors = lambda: [{"key": "hubspot", "label": "HubSpot"}]
        gas = types.ModuleType("core.google_ads_service")
        gas.format_customer_id = lambda cid: f"{cid[:3]}-{cid[3:6]}-{cid[6:]}"
        gaa = types.ModuleType("agents.google_ads_agent")
        gaa.grant_needs_reconnect = lambda cid: False
        for name, mod in (("core.vendor_tools", vt), ("core.crm_service", crm),
                          ("core.google_ads_service", gas), ("agents.google_ads_agent", gaa)):
            self.modules[name] = sys.modules.get(name)
            sys.modules[name] = mod
            package, _, attr = name.rpartition(".")
            if package in sys.modules:
                self.modules[name + "#attr"] = getattr(sys.modules[package], attr, None)
                setattr(sys.modules[package], attr, mod)

    def tearDown(self):
        for n, fn in self.saved.items():
            setattr(ownership, n, fn)
        for name, mod in self.modules.items():
            if name.endswith("#attr"):
                real = name[:-5]
                package, _, attr = real.rpartition(".")
                if mod is None:
                    if hasattr(sys.modules[package], attr):
                        delattr(sys.modules[package], attr)
                else:
                    setattr(sys.modules[package], attr, mod)
            elif mod is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = mod


# ─── 1. The page ─────────────────────────────────────────────────────────────

class GroupsMatchTheDisconnectGroups(unittest.TestCase):
    def test_every_disconnect_group_is_described(self):
        self.assertEqual(set(GROUPS) - {"avatar"}, set(ownership.GROUPS))
        self.assertEqual(GROUPS["crm"], ["crm"])
        self.assertEqual(GROUPS["manychat"], ["manychat"])
        self.assertEqual(GROUPS["make"], ["make"])

    def test_every_text_in_every_language(self):
        for group, spec in ownership.GROUPS.items():
            self.assertIn(spec["kind"], ownership.KEY_NOTE, group)
            self.assertEqual(set(spec["access"]), set(LANGS), group)
            self.assertTrue(all(spec["access"].values()), group)
            if spec["title"] is not None:
                self.assertTrue(all(spec["title"][lang] for lang in LANGS), group)
            self.assertTrue(spec["steps"], group)
        for table in (*ownership.KEY_NOTE.values(), *ownership.ASSET_TITLES.values()):
            self.assertTrue(all(table[lang] for lang in LANGS))

    def test_titles_and_access_never_name_a_creative_vendor(self):
        for group, spec in ownership.GROUPS.items():
            for text in list(spec["access"].values()) + list((spec["title"] or {}).values()):
                self.assertIsNone(VENDOR.search(text), (group, text))
        self.assertIsNone(ownership.GROUPS["heygen"]["title"])     # from vendor_tools.client_label


class AccountsView(Stubbed):
    def view(self, lang="he"):
        return ownership.accounts_view(27, GROUPS, lang)

    def test_identity_access_since_status(self):
        view = {e["group"]: e for e in self.view()}
        self.assertEqual(set(view), {"google_ads", "meta", "youtube", "wordpress", "elevenlabs", "crm", "manychat"})
        self.assertEqual(view["google_ads"]["items"], [{"what": "", "text": "123-456-7890", "link": ""}])
        self.assertEqual(view["google_ads"]["connected_at"], "2026-10-01T10:00:00+00:00")
        self.assertEqual(view["google_ads"]["status"], "connected")
        self.assertEqual([i["what"] for i in view["meta"]["items"]], ["חשבון מודעות", "עמוד"])
        self.assertEqual(view["meta"]["items"][1]["link"], "https://www.facebook.com/777")
        self.assertEqual(view["wordpress"]["items"][0]["link"], "https://shop.example.co.il")
        self.assertEqual(view["crm"]["items"][0]["text"], "HubSpot")         # the client pays for it
        self.assertEqual(view["manychat"]["items"][0]["text"], "ManyChat")
        self.assertEqual(view["elevenlabs"]["title"], "הקול שלך")
        self.assertEqual(view["elevenlabs"]["items"], [])                    # marker id hidden
        self.assertEqual(view["youtube"]["status"], "pending")               # channel not created yet
        self.assertEqual(view["youtube"]["items"], [])

    def test_key_line_explains_where_the_key_lives(self):
        view = {e["group"]: e for e in self.view()}
        self.assertIn("הוא נמצא בחשבון שלך אצל הכלי", view["elevenlabs"]["key_note"])
        self.assertIn("אפשר לבטל", view["wordpress"]["key_note"])
        self.assertIn("רק הרשאה שאישרת", view["google_ads"]["key_note"])

    def test_never_a_secret_or_any_part_of_one(self):
        for lang in LANGS:
            text = repr(self.view(lang)) + repr(ownership.assets_view(27, lang))
            for secret in SECRETS:
                self.assertNotIn(secret, text)
                self.assertNotIn(secret[-4:], text)
            for field in ("last4", "has_secret", "access_token", "fingerprint", "secret"):
                self.assertNotIn(field, text)

    def test_needs_attention(self):
        sys.modules["core.vendor_tools"].status = lambda cid, tool: "key_rejected"
        sys.modules["agents.google_ads_agent"].grant_needs_reconnect = lambda cid: True
        view = {e["group"]: e for e in self.view()}
        self.assertEqual(view["elevenlabs"]["status"], "needs_attention")
        self.assertEqual(view["google_ads"]["status"], "needs_attention")

    def test_other_languages(self):
        view = {e["group"]: e for e in self.view("en")}
        self.assertEqual(view["google_ads"]["title"], "Google Ads advertising")
        self.assertTrue(view["wordpress"]["key_note"].startswith("The application password"))
        self.assertEqual(ownership.accounts_view(27, GROUPS, "xx")[0]["title"], "פרסום ב-Google Ads")

    def test_assets(self):
        assets = ownership.assets_view(27)
        self.assertEqual([a["kind"] for a in assets],
                         ["website", "hosted_site", "landing_pages", "landing_domain", "drive_folder", "leads"])
        self.assertEqual(assets[0]["link"], "https://shop.example.co.il")
        self.assertEqual(assets[2]["text"], "2")
        self.assertEqual(assets[5]["text"], "14")

    def test_a_failed_read_leaves_the_asset_out(self):
        ownership._count_leads = lambda cid: (_ for _ in ()).throw(RuntimeError("db down"))
        ownership._asset_activity = lambda cid: (_ for _ in ()).throw(RuntimeError("db down"))
        kinds = [a["kind"] for a in ownership.assets_view(27)]
        self.assertNotIn("leads", kinds)
        self.assertNotIn("hosted_site", kinds)
        self.assertIn("website", kinds)


# ─── 2+3. Handover document and the kit ──────────────────────────────────────

class Handover(Stubbed):
    def test_every_account_and_asset_with_its_steps(self):
        doc = ownership.handover(27, GROUPS, {"business_name": "העסק של דנה"})
        whats = [r["what"] for r in doc["rows"]]
        self.assertIn("פרסום ב-Google Ads", whats)
        self.assertIn("אחסון האתר", whats)
        self.assertTrue(doc["subtitle"].startswith("העסק של דנה"))
        self.assertEqual([c[0] for c in doc["columns"]], ["what", "identity", "access", "steps"])
        voice = next(r for r in doc["rows"] if r["what"] == "הקול שלך")
        # Where the client must act at the vendor, naming it is necessary
        self.assertIn("ElevenLabs", voice["steps"])
        self.assertNotIn("{vendor}", repr(doc))
        self.assertIn("שום מידע שלך אצל הספקים לא נמחק", " ".join(doc["summary"]))
        text = repr(doc)
        for secret in SECRETS:
            self.assertNotIn(secret, text)
            self.assertNotIn(secret[-4:], text)

    def test_every_transfer_step_promises_nothing_is_deleted_at_the_vendor(self):
        for group, spec in ownership.GROUPS.items():
            self.assertNotIn("נמחק אצל", " ".join(spec["steps"]), group)
        for group in ("google_ads", "meta", "tiktok", "youtube", "heygen", "elevenlabs", "higgsfield", "crm",
                      "gmail"):
            self.assertRegex(" ".join(ownership.GROUPS[group]["steps"]), "נשאר|נשארים|לא נפגעת", group)


class RevokeAll(unittest.TestCase):
    def test_every_group_through_the_clients_own_disconnect(self):
        calls = []

        def disconnect(client_id, group):
            calls.append((client_id, group))
            if group == "meta":
                raise RuntimeError("provider down")
            return {"removed": 1 if group in ("google_ads", "wordpress") else 0,
                    "revoked": False if group == "wordpress" else True}

        result = ownership.revoke_all(27, GROUPS, disconnect)
        self.assertEqual({g for _, g in calls}, set(GROUPS) - {"avatar"})   # legacy alias skipped
        self.assertTrue(all(cid == 27 for cid, _ in calls))
        self.assertEqual(result["failed"], [{"group": "meta", "error": "RuntimeError"}])
        self.assertEqual(result["revoked"], [{"group": "google_ads", "provider_revoked": True},
                                             {"group": "wordpress", "provider_revoked": False}])


class KitRoute(unittest.TestCase):
    def body(self, name):
        node = _function(name)
        return ast.get_source_segment(API_SOURCE, node)

    def test_admin_only_typed_confirm_handover_before_revoke(self):
        src = self.body("admin_offboarding_kit")
        self.assertIn("_require_admin(request)", src)
        self.assertIn('!= str(client_id)', src)
        self.assertLess(src.index("ownership.handover("), src.index("ownership.revoke_all("))
        self.assertIn("ownership.revoke_all(client_id, _DISCONNECT_GROUPS, _disconnect_platform)", src)
        # Never touches billing, status, records or a vendor's data itself
        for forbidden in ("cancel_subscription", "update_client_status", "_archive_and_purge_client",
                          "delete_clients", "remove_accounts", ".delete("):
            self.assertNotIn(forbidden, src)
        self.assertIn('alert("client_offboarding"', src)

    def test_disconnect_only_removes_our_access(self):
        src = self.body("_disconnect_platform")
        calls = {n.func.id if isinstance(n.func, ast.Name) else n.func.attr
                 for n in ast.walk(_function("_disconnect_platform")) if isinstance(n, ast.Call)}
        self.assertTrue({"_revoke_grant", "remove_accounts"} <= calls)
        self.assertNotIn("delete_site", src)
        revoke = self.body("_revoke_grant") + self.body("_revoke_wordpress_password")
        for forbidden in ("delete_site", "delete_post", "delete_page", "trash", "delete_voice", "delete_file"):
            self.assertNotIn(forbidden, revoke)


def _load_wordpress_revoke(stub):
    """Run api_server._revoke_wordpress_password with a stub wordpress_service."""
    import core
    saved = sys.modules.get("core.wordpress_service"), getattr(core, "wordpress_service", None)
    sys.modules["core.wordpress_service"] = stub
    core.wordpress_service = stub
    namespace = {}
    exec(compile(ast.Module(body=[_function("_revoke_wordpress_password")], type_ignores=[]),
                 "api_server", "exec"), namespace)

    def restore():
        if saved[0] is None:
            sys.modules.pop("core.wordpress_service", None)
        else:
            sys.modules["core.wordpress_service"] = saved[0]
        if saved[1] is None:
            if hasattr(core, "wordpress_service"):
                del core.wordpress_service
        else:
            core.wordpress_service = saved[1]
    return namespace["_revoke_wordpress_password"], restore


class WordPressRevoke(unittest.TestCase):
    def setUp(self):
        self.calls = []
        stub = types.ModuleType("core.wordpress_service")
        stub.introspect_application_password = lambda site, user, pw: (
            self.calls.append(("introspect", site, user)) or {"uuid": "OURS"})
        stub.delete_application_password = lambda site, user, pw, uuid: self.calls.append(("delete", site, uuid))
        self.stub = stub
        self.fn, self.restore = _load_wordpress_revoke(stub)

    def tearDown(self):
        self.restore()

    def test_deletes_exactly_our_password(self):
        rows = [{"platform": "wordpress", "account_id": "https://shop.example.co.il"}]
        self.assertTrue(self.fn(rows, "wpuser:abcd efgh"))
        self.assertEqual(self.calls, [("introspect", "https://shop.example.co.il", "wpuser"),
                                      ("delete", "https://shop.example.co.il", "OURS")])

    def test_not_confirmed_and_nothing_stored(self):
        self.stub.introspect_application_password = lambda *a: (_ for _ in ()).throw(RuntimeError("site down"))
        rows = [{"platform": "wordpress", "account_id": "https://shop.example.co.il"}]
        self.assertFalse(self.fn(rows, "wpuser:pw"))
        self.assertIsNone(self.fn(rows, ""))
        self.assertIsNone(self.fn([], "wpuser:pw"))


class ClientRoutes(unittest.TestCase):
    def test_session_scoped_routes(self):
        for name in ("client_ownership", "client_ownership_handover", "client_export_all"):
            src = ast.get_source_segment(API_SOURCE, _function(name))
            self.assertIn("client_id = _require_session(request)", src, name)
        self.assertIn('@app.get("/api/client/ownership")', API_SOURCE)
        self.assertIn('@app.get("/api/client/export-all")', API_SOURCE)
        self.assertIn('@app.post("/api/client/disconnect")', API_SOURCE)

    def test_export_all_parts(self):
        src = ast.get_source_segment(API_SOURCE, _function("_export_all_zip"))
        for part in ('"leads.xlsx"', '"media-and-videos.xlsx"', '"outreach.xlsx"', '"landing-pages.xlsx"',
                     '"activity-report.xlsx"', '"billing.xlsx"', '"handover.docx"', '"full-record.json"'):
            self.assertIn(part, src)
        self.assertIn("missing.append(name)", src)                 # one failure never sinks the rest

    def test_admin_view_reads_the_page_but_never_exports(self):
        from core import admin_view
        self.assertIn("client_ownership", admin_view.READ_ENDPOINTS)
        for name in ("client_export_all", "client_ownership_handover", "client_disconnect"):
            self.assertNotIn(name, admin_view.READ_ENDPOINTS)


# ─── 4. The clause ───────────────────────────────────────────────────────────

class OwnershipClause(unittest.TestCase):
    def test_sales_chat_shows_it_with_every_proposal(self):
        html = _src("dashboard", "onboarding", "index.html")
        line = next(l for l in html.splitlines() if l.strip().startswith("proposal_term_ownership:"))
        for lang in LANGS:
            self.assertRegex(line, lang + r":'(?:[^'\\]|\\.){20,}'")
        self.assertIn("t('proposal_term_ownership')])", html)

    def test_report_email(self):
        src = _src("core", "email_service.py")
        self.assertIn("'term_allowance', 'term_ownership')", src)
        line = next(l for l in src.splitlines() if l.strip().startswith('"term_ownership"'))
        for lang in LANGS:
            self.assertIn(f'"{lang}":"', line)

    def test_terms_page(self):
        html = _src("dashboard", "terms", "index.html")
        self.assertIn('data-i18n-html="s7_p3"', html)
        line = next(l for l in html.splitlines() if l.strip().startswith("s7_p3:"))
        for lang in LANGS:
            self.assertRegex(line, lang + r":'<strong>")
        self.assertIn("אינה מוחקת מידע של הלקוח", line)


# ─── 5. No decrypted secret can reach a client session ──────────────────────

SECRET_SOURCES = {"get_client_secret", "hydrate", "use_vendor_key", "decrypt"}

# The only modules that decrypt at all. A new one must be reviewed for where
# the plaintext goes, then added here.
DECRYPTING_MODULES = {
    "core/api_server.py", "core/gmail_outreach.py", "core/rank_tracking.py", "core/secrets_vault.py",
    "core/vendor_tools.py", "agents/website_agent.py", "agents/tiktok_content_agent.py",
    "agents/youtube_content_agent.py", "agents/google_ads_agent.py", "agents/client_agent.py",
    "agents/meta_ads_agent.py", "agents/crm_agent.py", "agents/meta_content_agent.py", "agents/media_agent.py",
    "agents/avatar_agent.py", "core/media_gen_service.py", "core/elevenlabs_service.py",
    "core/avatar_video.py",      # use_vendor_key → straight into the TTS / render call
    "core/site_traffic.py",      # GA refresh token → straight into analytics_service reads
    "core/drive_storage.py",     # drive.file refresh token → a closure feeding drive_service._headers
}


def _call_name(call: ast.Call) -> str:
    f = call.func
    return f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")


def _carries(node, tainted: set, sources: set) -> bool:
    """Does this expression hold a secret? A call to a decrypting function
    does; so does a method/subscript on something that holds one
    (tokens.get(...), secret.split(...)). A call to any OTHER function
    consumes the secret (revoke_token(token) returns the provider's answer)."""
    if node is None:
        return False
    if isinstance(node, ast.Name):
        return node.id in tainted
    if isinstance(node, ast.Call):
        if _call_name(node) in sources:
            return True
        if isinstance(node.func, ast.Attribute):
            return _carries(node.func.value, tainted, sources)
        return False
    if isinstance(node, (ast.Attribute, ast.Subscript, ast.Starred)):
        return _carries(node.value, tainted, sources)
    if isinstance(node, ast.Lambda):
        return False
    return any(_carries(child, tainted, sources) for child in ast.iter_child_nodes(node)
               if isinstance(child, ast.expr) or isinstance(child, ast.comprehension)
               or isinstance(child, (ast.keyword, ast.FormattedValue)))


def _targets(target) -> set:
    return {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}


def _returns_secret(fn: ast.FunctionDef, sources: set) -> bool:
    tainted = set()
    for _ in range(3):                                   # propagate through re-assignments
        for node in ast.walk(fn):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and _carries(node.value, tainted, sources):
                for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                    tainted |= _targets(t)
            if isinstance(node, ast.For) and _carries(node.iter, tainted, sources):
                tainted |= _targets(node.target)
    for node in ast.walk(fn):
        if isinstance(node, (ast.Return, ast.Yield)) and _carries(node.value, tainted, sources):
            return True
        if isinstance(node, ast.Call) and _call_name(node) in ("Response", "JSONResponse", "HTMLResponse",
                                                              "RedirectResponse", "StreamingResponse"):
            if any(_carries(a, tainted, sources) for a in node.args) or \
               any(_carries(k.value, tainted, sources) for k in node.keywords):
                return True
    return False


def _is_route(fn: ast.FunctionDef) -> bool:
    for d in fn.decorator_list:
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in (
                "get", "post", "put", "patch", "delete") and getattr(d.func.value, "id", "") == "app":
            return True
    return False


class NoSecretReachesAClient(unittest.TestCase):
    def test_taint_check_catches_a_leak(self):
        # The checker itself must see these, or the real check proves nothing
        leaky = ast.parse(
            "def a(r):\n    return {'k': get_client_secret(1, 'x')}\n"
            "def b(r):\n    t = get_client_secret(1, 'x')\n    u, p = t.split(':')\n    return {'p': p}\n"
            "def c(r):\n    t = {x: hydrate(x) for x in r}\n    return Response(content=t.get('a'))\n"
            "def d(r):\n    t = get_client_secret(1, 'x')\n    return f'key {t}'\n")
        for fn in leaky.body:
            self.assertTrue(_returns_secret(fn, SECRET_SOURCES), fn.name)
        safe = ast.parse(
            "def e(r):\n    t = get_client_secret(1, 'x')\n    ok = revoke_token(t)\n    return {'ok': ok}\n"
            "def f(r):\n    sites = list_sites(get_client_secret(1, 'x'))\n    return {'sites': sites}\n")
        for fn in safe.body:
            self.assertFalse(_returns_secret(fn, SECRET_SOURCES), fn.name)

    def test_no_route_returns_a_decrypted_secret(self):
        functions = [n for n in API_TREE.body if isinstance(n, ast.FunctionDef)]
        # Helpers that hand a secret back count as sources too (to a fixpoint)
        sources = set(SECRET_SOURCES)
        for _ in range(5):
            grown = {fn.name for fn in functions if not _is_route(fn) and _returns_secret(fn, sources)}
            if grown <= sources:
                break
            sources |= grown
        routes = [fn for fn in functions if _is_route(fn)]
        self.assertGreater(len(routes), 150)
        leaking = [fn.name for fn in routes if _returns_secret(fn, sources)]
        self.assertEqual(leaking, [])

    def test_only_known_modules_decrypt(self):
        pattern = re.compile(r"\b(get_client_secret|hydrate|use_vendor_key)\s*\(")
        found = set()
        for folder in ("core", "agents"):
            for name in os.listdir(os.path.join(ROOT, folder)):
                if name.endswith(".py") and pattern.search(_src(folder, name)):
                    found.add(f"{folder}/{name}")
        self.assertEqual(found - DECRYPTING_MODULES, set(),
                         "a new module decrypts client secrets — review where the plaintext goes")

    def test_the_screens_drop_every_secret_column(self):
        row = {"platform": "x", "access_token": SECRETS[0], "access_token_enc": "v1.QQ",
               "secret_fingerprint": "ff", "secret_last4": "CRET"}
        public = secrets_vault.public_account(row)
        for column in secrets_vault.SECRET_COLUMNS:
            self.assertNotIn(column, public)
        self.assertNotIn(SECRETS[0], repr(public))

    def test_client_pages_never_name_a_secret_field(self):
        for page in ("client", "profile", "onboarding", "login", "landing"):
            html = _src("dashboard", page, "index.html")
            for field in ("access_token", "secret_last4", "access_token_enc", "refresh_token", ".last4",
                          "secret_fingerprint"):
                self.assertNotIn(field, html, (page, field))

    def test_client_tool_statuses_carry_no_part_of_a_key(self):
        src = _src("core", "vendor_tools.py")
        statuses = src[src.index("def statuses("):src.index("def is_connected(")]
        self.assertNotIn("secret_last4", statuses)
        self.assertNotIn('"last4"', statuses)

    def test_dashboard_page(self):
        html = _src("dashboard", "client", "index.html")
        self.assertIn("'personal', 'ownership', 'support'", html)
        self.assertIn('id="viewOwnership"', html)
        self.assertIn('href="/api/client/export-all"', html)
        page_js = html[html.index("function ownEl("):html.index("function ownExportStarted(")]
        self.assertNotIn("innerHTML", page_js)                   # every server value set as text
        self.assertIn("/^https:\\/\\//.test(item.link)", page_js)
        self.assertIn("body: JSON.stringify({ platform: group })", page_js)


if __name__ == "__main__":
    unittest.main()
