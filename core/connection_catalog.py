"""Connections onboarding through the chat (2026-11-01).

The dashboard has ONE "חבר" button per connection. It opens the client's
agent chat with that connection as context, and the flow is:

  agent: one question, two chips — "יש לי חשבון" / "אין לי עדיין"
  "יש לי"  → the existing connect flow (OAuth / the key field on the card)
  "אין לי" → the sign-up link + short plain-Hebrew steps; for a paid tier also
             the plan link, ONE recommended plan, and that the cost is paid
             directly to the vendor, never to uallak
  "סיימתי" → the system checks the connection and confirms in Hebrew; if it
             is not there yet, ONE gentle reminder later (never more)

Included-tier connections (part of the package, nothing for the client to
open) show nothing at all.

LINKS COME ONLY FROM THE TABLE `connection_catalog` (migrations/2026-11-01-
connection-catalog.sql), editable by the owner on /admin/connections/ without
a deploy. This module holds no vendor URL of its own. A row that is disabled,
missing, or has no sign-up link falls back to human support (WhatsApp + a
staff alert) — never to a guessed link.

Keys pasted during the flow go through the card's existing key field
(core/vendor_tools.connect → encrypted client_accounts row): this module never
receives, echoes or logs a key.

NO CONNECT LOOP (2026-11-22, client 27: "נחבר את TikTok. יש לכם כבר חשבון שם?"
nine times in ten days while TikTok and YouTube were connected). Before any
step, the LIVE state (`live_state`):
  connected      → "already connected ✓", never the question
  reconnect      → connected but not working (TikTok without the upload
                   permission, a dead grant, a refused key): ONE plain line
                   naming exactly that, and the connect opens directly
  not_connected  → read this connection's own past turns from the chat
                   thread (`_asked`, client_communications — no new storage):
                   already answered "יש לי חשבון" → never asked again: what
                   blocks it + the connect, directly; asked in the last
                   PROMPT_CAP_DAYS and not answered → the chips again, the
                   question is NOT posted again; otherwise the question.
"""
import time
from datetime import datetime, timedelta, timezone

SERVICE_NAME = "connection_catalog"
TABLE = "connection_catalog"
TIERS = ("included", "free_account", "paid_account")
FIELDS = ("key", "tier", "signup_url", "pricing_url", "recommended_plan_note", "help_notes", "enabled")
CACHE_SECONDS = 300
REMINDER_PREFIX = "conn_reminder:"
REMINDER_AFTER = timedelta(days=2)

# Which agent's chat a connection opens in (dashboard persona ids)
PERSONA = {
    "google_ads": "google", "tag_manager": "google", "search_console": "google",
    "analytics": "google", "merchant_center": "google",
    "meta": "meta",
    "youtube": "media", "tiktok": "media", "higgsfield": "media", "elevenlabs": "media",
    "creatomate": "media", "uppbeat": "media", "google_drive": "media",
    "website": "website", "store": "website",
    "gmail": "general", "manychat": "general",
}

# How "done" is verified: client_accounts platforms (any active one counts),
# or a vendor_tools tool. A key with neither cannot be checked by the system,
# so "done" goes to the team instead of pretending.
PLATFORMS = {
    "google_ads": ("google_ads",), "meta": ("meta_ads", "meta_page"), "tiktok": ("tiktok",),
    "youtube": ("youtube",), "gmail": ("gmail_send",), "search_console": ("search_console",),
    "analytics": ("google_analytics",), "google_drive": ("google_drive_file",),
    "tag_manager": ("google_tagmanager",), "website": ("wordpress",), "store": ("woocommerce",),
}
VENDOR_TOOLS = ("higgsfield", "elevenlabs")

# The full-website system is billed by uallak (Step 0: InstaWP on our own
# workspace, passed through in PRICING) — "אין לי" offers our build, not links
BUILD_INSTEAD = {"website"}

# No self-service connect (2026-11-17, core/chat_automation): every step is the
# manager's short pitch + the ONE "schedule a call with a representative" card.
# Never "do you have an account", never a link, never a vendor name.
TEAM_CONNECT = {"manychat"}

# Names the client reads (Hebrew). Vendor names appear only where the client
# must act on the vendor's own site — which is exactly this flow.
NAMES = {
    "google_ads": "Google Ads", "meta": "פייסבוק ואינסטגרם", "tiktok": "TikTok", "youtube": "YouTube",
    "gmail": "Gmail", "search_console": "Google Search Console", "analytics": "Google Analytics",
    "google_drive": "Google Drive", "tag_manager": "Google Tag Manager",
    "merchant_center": "Google Merchant Center", "website": "האתר שלך", "store": "החנות באתר",
    "higgsfield": "Higgsfield", "elevenlabs": "ElevenLabs", "creatomate": "Creatomate",
    "uppbeat": "Uppbeat", "manychat": "אוטומציית שיחות",
}

CHIP_HAS = "יש לי חשבון"
CHIP_NONE = "אין לי עדיין"
CHIP_DONE = "סיימתי"
LANGS = ("he", "en", "fr", "ar", "ru")
# The question, the chips and the connect/verify lines in the client's
# language. The Hebrew and English texts are unchanged (earlier threads are
# matched by them in _asked). The sign-up steps ("none") stay Hebrew: they
# carry the owner's Hebrew help notes from the table.
CHIPS = {
    "has": {"he": CHIP_HAS, "en": "I have an account", "fr": "J'ai un compte", "ar": "لدي حساب",
            "ru": "У меня есть аккаунт"},
    "none": {"he": CHIP_NONE, "en": "Not yet", "fr": "Pas encore", "ar": "ليس بعد", "ru": "Пока нет"},
    "done": {"he": CHIP_DONE, "en": "Done", "fr": "C'est fait", "ar": "انتهيت", "ru": "Готово"},
}
CHIPS_EN = {k: v["en"] for k, v in CHIPS.items()}
T = {
    "start": {"he": "נחבר את {name}. יש לכם כבר חשבון שם?",
              "en": "Let's connect {name}. Do you already have an account there?",
              "fr": "Connectons {name}. Avez-vous déjà un compte ?",
              "ar": "لنربط {name}. هل لديكم حساب هناك بالفعل؟",
              "ru": "Подключим {name}. У вас уже есть там аккаунт?"},
    "has_oauth": {"he": "מעולה. פותח לכם את החיבור של {name} — מאשרים במסך שייפתח, וזהו.",
                  "en": "Great. Opening the {name} connection for you — approve it on the screen that opens, and that's it.",
                  "fr": "Parfait. J'ouvre la connexion {name} — validez sur l'écran qui s'ouvre, c'est tout.",
                  "ar": "ممتاز. أفتح لكم ربط {name} — وافقوا في الشاشة التي ستفتح، وهذا كل شيء.",
                  "ru": "Отлично. Открываю подключение {name} — подтвердите в открывшемся окне, и всё."},
    "has_key": {"he": "מעולה. מדביקים את המפתח בשדה שנפתח בכרטיס של {name} (לא בצ׳אט) ולוחצים \"חיבור\".",
                "en": "Great. Paste the key into the field that opens on the {name} card (not in the chat) and press \"Connect\".",
                "fr": "Parfait. Collez la clé dans le champ qui s'ouvre sur la carte {name} (pas dans le chat) et appuyez sur « Connecter ».",
                "ar": "ممتاز. الصقوا المفتاح في الحقل الذي يفتح في بطاقة {name} (ليس في المحادثة) واضغطوا \"ربط\".",
                "ru": "Отлично. Вставьте ключ в поле на карточке {name} (не в чат) и нажмите «Подключить»."},
    "connected": {"he": "{name} מחובר ✓ — מכאן אנחנו ממשיכים.", "en": "{name} is connected ✓ — we'll take it from here.",
                  "fr": "{name} est connecté ✓ — on prend la suite.", "ar": "{name} مربوط ✓ — نكمل من هنا.",
                  "ru": "{name} подключён ✓ — дальше мы сами."},
    "not_yet": {"he": "עוד לא רואה את {name} מחובר. אחרי שהחשבון פתוח, מאשרים את החיבור במסך שנפתח — ואני אבדוק שוב.",
                "en": ("I don't see {name} connected yet. Once the account is open, approve the connection on the "
                       "screen that opens — and I'll check again."),
                "fr": "Je ne vois pas encore {name} connecté. Une fois le compte ouvert, validez la connexion sur l'écran qui s'ouvre — et je vérifie à nouveau.",
                "ar": "لا أرى {name} مربوطًا بعد. بعد فتح الحساب، وافقوا على الربط في الشاشة التي تفتح — وسأتحقق مجددًا.",
                "ru": "Пока не вижу подключения {name}. Когда аккаунт будет открыт, подтвердите подключение в открывшемся окне — и я проверю снова."},
    # ── no connect loop (2026-11-22) ──
    "already": {"he": "{name} כבר מחובר ✓ — אין צורך לחבר שוב, הכול עובד מכאן.",
                "en": "{name} is already connected ✓ — no need to connect it again, everything works from here.",
                "fr": "{name} est déjà connecté ✓ — inutile de le reconnecter, tout fonctionne d'ici.",
                "ar": "{name} مربوط بالفعل ✓ — لا حاجة لربطه مجددًا، كل شيء يعمل من هنا.",
                "ru": "{name} уже подключён ✓ — подключать заново не нужно, всё работает."},
    "reconnect_dead": {"he": "{name} מחובר, אבל הגישה שלנו אליו הפסיקה לעבוד — לכן צריך לאשר אותה מחדש. פותח לכם את מסך האישור: מאשרים פעם אחת, וזהו.",
                       "en": "{name} is connected, but our access to it stopped working — so it needs approving again. Opening the approval screen: approve once, and that's it.",
                       "fr": "{name} est connecté, mais notre accès ne fonctionne plus — il faut donc le valider à nouveau. J'ouvre l'écran d'autorisation : une validation, et c'est tout.",
                       "ar": "{name} مربوط، لكن وصولنا إليه توقف عن العمل — لذلك يجب الموافقة عليه من جديد. أفتح شاشة الموافقة: توافقون مرة واحدة، وهذا كل شيء.",
                       "ru": "{name} подключён, но наш доступ перестал работать — его нужно подтвердить заново. Открываю окно подтверждения: один раз, и всё."},
    "reconnect_upload": {"he": "{name} מחובר, אבל בלי ההרשאה לשלוח סרטונים לטיוטות שלכם — וזה מה שחוסם. במסך של {name} שנפתח עכשיו, השאירו מסומנת את ההרשאה להעלאת סרטונים ואשרו.",
                         "en": "{name} is connected, but without the permission to send videos to your drafts — that is what's blocking it. On the {name} screen that opens now, keep the video upload permission ticked and approve.",
                         "fr": "{name} est connecté, mais sans l'autorisation d'envoyer des vidéos dans vos brouillons — c'est ce qui bloque. Sur l'écran {name} qui s'ouvre, laissez cochée l'autorisation d'envoi de vidéos et validez.",
                         "ar": "{name} مربوط، لكن بدون إذن إرسال الفيديوهات إلى مسوداتكم — وهذا ما يعيق. في شاشة {name} التي تفتح الآن، أبقوا إذن رفع الفيديوهات محددًا ووافقوا.",
                         "ru": "{name} подключён, но без разрешения отправлять видео в ваши черновики — вот что мешает. В открывшемся окне {name} оставьте отмеченным разрешение на загрузку видео и подтвердите."},
    "reconnect_key": {"he": "{name} מחובר, אבל המפתח שלו נדחה — וזה מה שחוסם. מדביקים מפתח חדש בשדה שנפתח בכרטיס של {name} (לא בצ׳אט) ולוחצים \"חיבור\".",
                      "en": "{name} is connected, but its key was refused — that is what's blocking it. Paste a new key into the field that opens on the {name} card (not in the chat) and press \"Connect\".",
                      "fr": "{name} est connecté, mais sa clé a été refusée — c'est ce qui bloque. Collez une nouvelle clé dans le champ de la carte {name} (pas dans le chat) et appuyez sur « Connecter ».",
                      "ar": "{name} مربوط، لكن مفتاحه رُفض — وهذا ما يعيق. الصقوا مفتاحًا جديدًا في الحقل الذي يفتح في بطاقة {name} (ليس في المحادثة) واضغطوا \"ربط\".",
                      "ru": "{name} подключён, но его ключ отклонён — вот что мешает. Вставьте новый ключ в поле на карточке {name} (не в чат) и нажмите «Подключить»."},
    "stuck_oauth": {"he": "כבר אמרתם לי שיש לכם חשבון ב-{name}, אבל החיבור לא הושלם: מסך האישור של {name} לא החזיר אלינו אישור. מה שצריך: במסך שנפתח עכשיו להיות מחוברים לחשבון שאותו רוצים לחבר, ולאשר עד הסוף. אם זה נעצר שוב, כתבו לי מה רואים במסך ונפתור את זה יחד.",
                    "en": "You already told me you have a {name} account, but the connection didn't complete: {name}'s approval screen never sent us the approval. What's needed: on the screen that opens now, be signed in to the account you want to connect and approve all the way through. If it stops again, tell me what you see on the screen and we'll sort it out together.",
                    "fr": "Vous m'avez déjà dit avoir un compte {name}, mais la connexion n'a pas abouti : l'écran d'autorisation de {name} ne nous a jamais renvoyé l'accord. Ce qu'il faut : sur l'écran qui s'ouvre, être connecté au compte à relier et valider jusqu'au bout. Si ça bloque encore, dites-moi ce que vous voyez et on règle ça ensemble.",
                    "ar": "أخبرتموني بالفعل أن لديكم حسابًا في {name}، لكن الربط لم يكتمل: شاشة الموافقة في {name} لم تُرسل لنا الموافقة. المطلوب: في الشاشة التي تفتح الآن، كونوا مسجّلين في الحساب الذي تريدون ربطه ووافقوا حتى النهاية. إن توقف مجددًا، أخبروني بما ترونه على الشاشة ونحلّه معًا.",
                    "ru": "Вы уже говорили, что у вас есть аккаунт {name}, но подключение не завершилось: окно подтверждения {name} так и не прислало нам согласие. Что нужно: в открывшемся окне войти в тот аккаунт, который хотите подключить, и подтвердить до конца. Если снова остановится — напишите, что видите на экране, разберёмся вместе."},
    "stuck_key": {"he": "כבר אמרתם לי שיש לכם חשבון ב-{name}, אבל המפתח עוד לא נשמר אצלנו — וזה מה שחסר. מדביקים אותו בשדה שנפתח בכרטיס של {name} (לא בצ׳אט) ולוחצים \"חיבור\".",
                  "en": "You already told me you have a {name} account, but the key hasn't been saved with us yet — that's what's missing. Paste it into the field that opens on the {name} card (not in the chat) and press \"Connect\".",
                  "fr": "Vous m'avez déjà dit avoir un compte {name}, mais la clé n'est pas encore enregistrée chez nous — c'est ce qui manque. Collez-la dans le champ de la carte {name} (pas dans le chat) et appuyez sur « Connecter ».",
                  "ar": "أخبرتموني بالفعل أن لديكم حسابًا في {name}، لكن المفتاح لم يُحفظ لدينا بعد — وهذا ما ينقص. الصقوه في الحقل الذي يفتح في بطاقة {name} (ليس في المحادثة) واضغطوا \"ربط\".",
                  "ru": "Вы уже говорили, что у вас есть аккаунт {name}, но ключ у нас ещё не сохранён — вот чего не хватает. Вставьте его в поле на карточке {name} (не в чат) и нажмите «Подключить»."},
    "stuck_site": {"he": "כבר אמרתם לי שיש לכם אתר, אבל הכתובת שלו עוד לא נשמרה אצלנו — וזה מה שחסר. כתבו אותה בכרטיס שנפתח ונמשיך מכאן.",
                   "en": "You already told me you have a website, but its address hasn't been saved with us yet — that's what's missing. Type it in the card that opens and we'll continue from there.",
                   "fr": "Vous m'avez déjà dit avoir un site, mais son adresse n'est pas encore enregistrée — c'est ce qui manque. Saisissez-la dans la carte qui s'ouvre et on continue.",
                   "ar": "أخبرتموني بالفعل أن لديكم موقعًا، لكن عنوانه لم يُحفظ لدينا بعد — وهذا ما ينقص. اكتبوه في البطاقة التي تفتح ونكمل من هناك.",
                   "ru": "Вы уже говорили, что у вас есть сайт, но его адрес у нас ещё не сохранён — вот чего не хватает. Впишите его в открывшейся карточке, и продолжим."},
}
# A question asked and not answered is not posted again for this long (the chips come back alone)
PROMPT_CAP_DAYS = 7
HISTORY_DAYS = 120


def _t(key: str, lang: str, **values) -> str:
    table = T[key]
    return (table.get(lang) or table["he"]).format(**values)


def chip_label(step: str, lang: str = "he") -> str:
    labels = CHIPS.get(step) or {}
    return labels.get(lang) or labels.get("he", "")

_cache = {"at": 0.0, "rows": None}


def _db():
    from agents.client_agent import _db as client_db
    return client_db()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─── The table (the ONLY source of links) ────────────────────────────────────

def _clean(row: dict) -> dict:
    out = {k: row.get(k) for k in FIELDS}
    out["key"] = str(out["key"] or "").strip()
    out["tier"] = out["tier"] if out["tier"] in TIERS else "included"
    for k in ("signup_url", "pricing_url", "recommended_plan_note", "help_notes"):
        out[k] = str(out[k] or "").strip()
    for k in ("signup_url", "pricing_url"):
        if out[k] and not out[k].startswith("https://"):
            out[k] = ""   # only https links ever reach a client
    out["enabled"] = bool(out["enabled"])
    return out


def rows(fresh: bool = False) -> list:
    if not fresh and _cache["rows"] is not None and time.time() - _cache["at"] < CACHE_SECONDS:
        return _cache["rows"]
    try:
        data = _db().table(TABLE).select(",".join(FIELDS)).order("key").limit(500).execute().data or []
        result = [_clean(r) for r in data]
    except Exception as e:
        print(f"[{SERVICE_NAME}] table read failed: {type(e).__name__}")
        result = []   # table not created yet: every flow falls back to human support
    _cache.update(at=time.time(), rows=result)
    return result


def forget():
    _cache.update(at=0.0, rows=None)


def entry(key: str) -> dict:
    key = (key or "").strip()
    return next((r for r in rows() if r["key"] == key), {})


def admin_update(key: str, changes: dict) -> dict:
    """Owner edit from /admin/connections/. Only the editable fields; links must
    be https. Raises ValueError on a bad value or unknown key."""
    if not entry(key) and not any(r["key"] == key for r in rows(fresh=True)):
        raise ValueError("unknown_key")
    clean = {}
    for field in ("signup_url", "pricing_url", "recommended_plan_note", "help_notes", "tier", "enabled"):
        if field not in changes:
            continue
        value = changes[field]
        if field in ("signup_url", "pricing_url"):
            value = str(value or "").strip()
            if value and (not value.startswith("https://") or len(value) > 500 or any(c.isspace() for c in value)):
                raise ValueError(field)
        elif field == "tier":
            if value not in TIERS:
                raise ValueError(field)
        elif field == "enabled":
            value = bool(value)
        else:
            value = str(value or "").strip()[:1500]
        clean[field] = value
    if not clean:
        raise ValueError("nothing_to_change")
    clean["updated_at"] = _now().isoformat()
    _db().table(TABLE).update(clean).eq("key", key).execute()
    forget()
    from core.agent_base import agent_alert
    agent_alert(SERVICE_NAME, [f"connection_catalog '{key}' edited: {', '.join(sorted(k for k in clean if k != 'updated_at'))}"])
    return entry(key)


# ─── Status ──────────────────────────────────────────────────────────────────

def can_verify(key: str) -> bool:
    return key in PLATFORMS or key in VENDOR_TOOLS


def is_connected(client_id: int, key: str) -> bool:
    if key in VENDOR_TOOLS:
        from core import vendor_tools
        return vendor_tools.is_connected(client_id, key)
    platforms = PLATFORMS.get(key)
    if not platforms:
        return False
    from agents.client_agent import get_accounts
    return any(a.get("platform") in platforms and a.get("status") == "active"
               for a in (get_accounts(client_id) or []))


def _health(client_id: int, key: str) -> str:
    """For an ACTIVE connection: "" = works as far as we know, or why it does
    not: "upload_permission" (TikTok without video.upload), "dead" (the grant /
    access was refused), "key_rejected" (a creative tool's key). Stored records
    only — no vendor call. Unreadable = "" (the real call will tell)."""
    try:
        if key == "tiktok":
            from core import tiktok_grant
            state = tiktok_grant.state(client_id)
            return {"missing_upload": "upload_permission", "dead": "dead"}.get(state, "")
        if key == "google_ads":
            from agents.google_ads_agent import grant_needs_reconnect
            return "dead" if grant_needs_reconnect(client_id) else ""
        if key == "tag_manager":
            from agents.website_agent import gtm_missing_scopes
            return "dead" if gtm_missing_scopes(client_id) else ""
        if key in VENDOR_TOOLS:
            from core import vendor_tools
            return "key_rejected" if vendor_tools.status(client_id, key) == "key_rejected" else ""
    except Exception:
        return ""
    return ""


def live_state(client_id: int, key: str) -> dict:
    """{"state": "connected" | "reconnect" | "not_connected", "why": <_health>}.
    Unreadable = not_connected: the flow then simply asks, as it did before."""
    try:
        connected = is_connected(client_id, key)
    except Exception as e:
        print(f"[{SERVICE_NAME}] live state unreadable for client {client_id} / {key}: {type(e).__name__}")
        return {"state": "not_connected", "why": ""}
    if not connected:
        if key in VENDOR_TOOLS and _health(client_id, key) == "key_rejected":
            return {"state": "reconnect", "why": "key_rejected"}
        return {"state": "not_connected", "why": ""}
    why = _health(client_id, key)
    return {"state": "reconnect" if why else "connected", "why": why}


def _thread(client_id: int, key: str, texts: list) -> list:
    """This connection's own past turns in its agent's chat thread: rows whose
    content is one of `texts`, oldest first, last HISTORY_DAYS. [] when
    unreadable (then the question is simply asked)."""
    persona = PERSONA.get(key, "general")
    channel = "dashboard_chat" if persona == "general" else f"dashboard_chat:{persona}"   # = support_agent.persona_channel
    try:
        since = (_now() - timedelta(days=HISTORY_DAYS)).isoformat()
        rows = (_db().table("client_communications").select("direction, content, created_at")
                .eq("client_id", client_id).eq("channel", channel)
                .in_("content", texts).gte("created_at", since)
                .order("created_at").limit(200).execute().data or [])
        return rows
    except Exception:
        return []


def _asked(client_id: int, key: str) -> dict:
    """What this connection's start question already got:
    {"asked_at": iso of the last time it was asked, "answer": "has" | "none" |
    "" — the chip clicked right after the question FOR THIS KEY}."""
    name = _name(key)
    starts = {T["start"][lang].format(name=name) for lang in LANGS}
    answers = {CHIPS[step][lang]: step for step in ("has", "none") for lang in LANGS}
    asked_at, answer, open_question = "", "", False
    for row in _thread(client_id, key, sorted(starts | set(answers))):
        content = row.get("content") or ""
        if row.get("direction") == "outbound" and content in starts:
            asked_at, open_question = row.get("created_at") or "", True
        elif row.get("direction") == "inbound" and open_question and content in answers:
            answer, open_question = answers[content], False   # a chip right after THIS key's question
        elif row.get("direction") == "inbound":
            open_question = False                              # a chip that answered another key
    return {"asked_at": asked_at, "answer": answer}


def _recent(iso: str, days: int) -> bool:
    try:
        when = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return _now() - when < timedelta(days=days)


# ─── The flow ────────────────────────────────────────────────────────────────

def _name(key: str) -> str:
    return NAMES.get(key, key)


def _support_link() -> str:
    try:
        from core import admin_service
        from core.client_leads import whatsapp_link
        return whatsapp_link(admin_service.support_whatsapp_number()) or ""
    except Exception:
        return ""


def _human_fallback(client_id: int, key: str, why: str) -> dict:
    """No usable link in the table: a person takes it from here."""
    try:
        from core import chat_fallback
        chat_fallback.alert_once("connection_onboarding", client_id, f"{why}:{key}",
                                 f"client {client_id} wants to open '{key}' but the catalog has no usable "
                                 f"sign-up link ({why}) — fill it on /admin/connections/ and help them", days=3)
    except Exception:
        pass
    link = _support_link()
    text = (f"את הפתיחה של {_name(key)} נעשה איתכם יחד — מישהו מהצוות שלנו יחזור אליכם בקרוב."
            + (f" אפשר גם לכתוב לנו עכשיו בוואטסאפ: {link}" if link else ""))
    return {"messages": [text], "chips": [], "support": True}


# The online store (agents/store_agent, docs/STORE.md): ONE click on the
# client's own store approval screen (WooCommerce's built-in authorization,
# read-only). No steps to follow, no key to create or paste, never a word
# about wp-admin menus. Order totals already come from our purchase
# measurement without it; this is only for deeper order data.
STORE_ONE_CLICK_HE = ("חיבור החנות בקליק אחד: נפתח מסך האישור של החנות שלכם — נכנסים ולוחצים \"אישור\". "
                      "קריאה בלבד (הזמנות, סטטוסים והחזרים), ואפשר לבטל בכל רגע.")


def _store_flow(client_id: int, step: str) -> dict:
    base = {"show": True, "key": "store", "persona": "website", "connect": "", "support": False}
    if step in ("start", "has", "none"):
        # "has" / "none" kept for old buttons still open in a browser
        return {**base, "connect": "store", "chips": [], "messages": [STORE_ONE_CLICK_HE]}
    if step == "done":
        if is_connected(client_id, "store"):
            _clear_reminder(client_id, "store")
            return {**base, "chips": [], "connected": True,
                    "messages": ["החנות מחוברת ✓ — מעכשיו אפשר לשאול אותי גם על הזמנה מסוימת, סטטוסים והחזרים."]}
        _schedule_reminder(client_id, "store")
        return {**base, "connect": "store", "chips": [], "connected": False,
                "messages": ["עוד לא רואה את החנות מחוברת. אפשר לנסות שוב בלחיצה על \"חיבור החנות בקליק אחד\"."]}
    raise ValueError("bad_step")


def flow(client_id: int, key: str, step: str, lang: str = None) -> dict:
    """One turn of the connect conversation. Pure decision from the table row
    (+ the connection status on "done"); the caller posts `messages` into the
    persona's thread. Returns {"show": False} for included / unknown keys —
    except the store, whose steps are on the client's own site (_store_flow).
    `lang`: the question, chips and connect/verify lines in the client's
    language (T); the sign-up steps keep the owner's Hebrew notes.
    Live state first: a connected key never gets the question (see the
    module docstring, NO CONNECT LOOP)."""
    from core import client_language as _cl
    lang = _cl.resolve(client_id, lang)   # the CLIENT's language, never a fixed "he"
    if key == "store":
        return _store_flow(client_id, step)
    if key in TEAM_CONNECT:
        from core import chat_automation
        return {"show": True, "key": key, "persona": "general", "connect": "", "support": False, "chips": [],
                "messages": [chat_automation.pitch(lang)], "card_action": chat_automation.CALL_ACTION}
    row = entry(key)
    if row and row["tier"] == "included":
        return {"show": False}
    if not row and key not in PERSONA:
        return {"show": False}
    persona = PERSONA.get(key, "general")
    base = {"show": True, "key": key, "persona": persona, "connect": "", "support": False}
    name = _name(key)

    # LIVE STATE FIRST (no connect loop): never "do you have an account?" for
    # a connection that exists — it is either working, or one named thing
    # blocks it and the connect opens directly
    if step in ("start", "has", "none"):
        live = live_state(client_id, key)
        if live["state"] == "connected":
            return {**base, "chips": [], "connected": True, "messages": [_t("already", lang, name=name)]}
        if live["state"] == "reconnect":
            line = {"upload_permission": "reconnect_upload", "key_rejected": "reconnect_key"}.get(live["why"],
                                                                                                "reconnect_dead")
            return {**base, "connect": key, "chips": [], "messages": [_t(line, lang, name=name)]}

    if step == "start":
        past = _asked(client_id, key)
        if past["answer"] == "has":
            # They already said they have one and it still isn't connected:
            # never the same question again — what blocks it, and the connect
            line = "stuck_site" if key == "website" else "stuck_key" if key in VENDOR_TOOLS else "stuck_oauth"
            try:
                from core import chat_fallback
                chat_fallback.alert_once("connection_onboarding", client_id, f"stuck:{key}",
                                         f"client {client_id} said they have a '{key}' account, but it is still not "
                                         f"connected and they pressed connect again — check what blocks it", days=7)
            except Exception:
                pass
            return {**base, "connect": key, "chips": [], "stuck": True, "messages": [_t(line, lang, name=name)]}
        chips = [{"label": chip_label("has", lang), "step": "has"},
                 {"label": chip_label("none", lang), "step": "none"}]
        if past["asked_at"] and not past["answer"] and _recent(past["asked_at"], PROMPT_CAP_DAYS):
            # Asked recently and not answered: the two chips again, the question is not posted twice
            return {**base, "messages": [], "chips": chips, "repeat": True}
        return {**base, "messages": [_t("start", lang, name=name)], "chips": chips}

    if step == "has":
        if key == "website":
            return {**base, "connect": key, "chips": [],
                    "messages": ["מעולה. כתבו את כתובת האתר בכרטיס שנפתח — נבדוק בעצמנו במה הוא בנוי ונמשיך מכאן."]}
        return {**base, "connect": key, "chips": [],
                "messages": [_t("has_key" if key in VENDOR_TOOLS else "has_oauth", lang, name=name)]}

    if step == "none":
        if key in BUILD_INSTEAD and row and row["enabled"]:
            note = row["recommended_plan_note"] or row["help_notes"]
            return {**base, "connect": "website_create", "chips": [],
                    "messages": ["אין בעיה — אנחנו מקימים ומארחים לכם את האתר." + (f" {note}" if note else "")]}
        if not row:
            return {**base, **_human_fallback(client_id, key, "missing_row")}
        if not row["enabled"] or not row["signup_url"]:
            return {**base, **_human_fallback(client_id, key, "disabled" if not row["enabled"] else "no_signup_url")}
        lines = [f"בואו נפתח חשבון ב-{name}. ההרשמה כאן: {row['signup_url']}"]
        if row["help_notes"]:
            lines.append(row["help_notes"])
        if row["tier"] == "paid_account":
            plan = (f"המסלולים כאן: {row['pricing_url']}" if row["pricing_url"] else "")
            rec = (f" ההמלצה שלנו: {row['recommended_plan_note']}" if row["recommended_plan_note"] else "")
            lines.append((plan + rec).strip())
            lines.append(f"התשלום על {name} עובר ישירות מכם אליהם — לעולם לא אלינו.")
        lines.append("כשסיימתם, לחצו \"סיימתי\" ואבדוק שהכול מחובר.")
        return {**base, "messages": [l for l in lines if l],
                "chips": [{"label": CHIP_DONE, "step": "done"}]}

    if step == "done":
        if not can_verify(key):
            try:
                from core import chat_fallback
                chat_fallback.alert_once("connection_onboarding", client_id, f"done:{key}",
                                         f"client {client_id} says they opened '{key}' — check and continue with them",
                                         days=1)
            except Exception:
                pass
            return {**base, "chips": [],
                    "messages": [f"תודה! הצוות שלנו יבדוק את {name} וימשיך איתכם מכאן."]}
        if is_connected(client_id, key):
            _clear_reminder(client_id, key)
            return {**base, "chips": [], "connected": True, "messages": [_t("connected", lang, name=name)]}
        _schedule_reminder(client_id, key)
        return {**base, "connect": key, "chips": [], "connected": False,
                "messages": [_t("not_yet", lang, name=name)]}

    raise ValueError("bad_step")


# ─── One gentle reminder, never more ─────────────────────────────────────────

def _settings():
    return _db().table("app_settings")


def _schedule_reminder(client_id: int, key: str):
    marker = f"{REMINDER_PREFIX}{int(client_id)}:{key}"
    try:
        existing = _settings().select("key").eq("key", marker).limit(1).execute().data or []
        if existing:
            return   # one reminder per connection, ever
        _settings().upsert({"key": marker, "value": {"due": (_now() + REMINDER_AFTER).isoformat(), "sent": False},
                            "updated_at": _now().isoformat()}, on_conflict="key").execute()
    except Exception as e:
        print(f"[{SERVICE_NAME}] reminder schedule failed: {type(e).__name__}")


def _clear_reminder(client_id: int, key: str):
    marker = f"{REMINDER_PREFIX}{int(client_id)}:{key}"
    try:
        rows_ = _settings().select("value").eq("key", marker).limit(1).execute().data or []
        if rows_ and not (rows_[0].get("value") or {}).get("sent"):
            _settings().upsert({"key": marker, "value": {"sent": True, "resolved": True},
                                "updated_at": _now().isoformat()}, on_conflict="key").execute()
    except Exception:
        pass


def send_due_reminders() -> dict:
    """Daily job (GET /api/connections/reminders). Each marker sends at most
    one chat line, then stays as sent forever."""
    from core.agent_actions import _post_client_message
    sent = resolved = 0
    try:
        markers = _settings().select("key, value").like("key", f"{REMINDER_PREFIX}%").limit(2000).execute().data or []
    except Exception:
        return {"sent": 0, "resolved": 0}
    now = _now()
    for marker in markers:
        value = marker.get("value") or {}
        if value.get("sent"):
            continue
        try:
            due = datetime.fromisoformat(value.get("due"))
        except (TypeError, ValueError):
            continue
        if due > now:
            continue
        try:
            client_id, key = marker["key"][len(REMINDER_PREFIX):].split(":", 1)
            client_id = int(client_id)
        except ValueError:
            continue
        connected = is_connected(client_id, key)
        if not connected:
            _post_client_message(client_id, PERSONA.get(key, "general"),
                                 f"רק תזכורת קטנה: {_name(key)} עוד לא מחובר. כשנוח לכם, לחצו \"חבר\" בכרטיס ונמשיך משם.")
            sent += 1
        else:
            resolved += 1
        _settings().upsert({"key": marker["key"], "value": {**value, "sent": True, "resolved": connected},
                            "updated_at": now.isoformat()}, on_conflict="key").execute()
    return {"sent": sent, "resolved": resolved}
