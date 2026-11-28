"""The CLIENT approves the brand brief (2026-11-28, knowledge/client-governance.md §1).

The characterisation is what buys the system the right to decide. That right is
granted by the client, never taken on their behalf by default:

  - `approve_by_client` — the client's click on their branding page. THE grant.
  - `approve_on_behalf` — the owner, ONLY for a client who was asked and stayed
    silent `SILENT_HOURS` (the same rule as the weekly sync, §4). Recorded as
    such on the brief's version, alerted, and SAID to the client on their page.
  - a brief approved before this rule (by the manager, or a migrated file whose
    status line says approved) carries no grant: its status stays "approved"
    and it stays protected, but authority is "ask" until the client approves.

A draft is INFORMATION from the first day: every agent reads it. The approval
changes only AUTHORITY (core/governance.authority), never what may be read, and
work is never blocked on it.

`present_due()` rides the intake sweep (core/intake_sla): once the deep
characterisation is done and a brief exists, the client is asked ONCE — one line
in the manager's chat, and the approval block on the branding page.
"""
from datetime import datetime, timezone

from core import brand_brief

SERVICE_NAME = "brief_approval"
SILENT_HOURS = 24


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


def pick(table: dict, lang: str) -> str:
    return table.get(lang) or table.get("en") or next(iter(table.values()))


TEXT = {
    "title": _five("מאשרים את מה שהבנו עליכם?", "Do you approve what we understood about you?",
                   "Validez-vous ce que nous avons compris de vous ?", "هل توافقون على ما فهمناه عنكم؟",
                   "Подтверждаете то, что мы о вас поняли?"),
    "text": _five(
        "העמוד הזה הוא מה שלמדנו על העסק ועל המותג שלכם. כשתאשרו אותו, הצוות יחליט לבד בתוך המסגרת הזאת ויעדכן אתכם, "
        "במקום לשאול על כל פרט. עד אז אנחנו ממשיכים לעבוד, ושואלים לפני כל החלטה גדולה. אפשר לשנות כל דבר בעמוד גם אחרי האישור.",
        "This page is what we learned about your business and your brand. Once you approve it, the team decides within it "
        "and keeps you informed, instead of asking about every detail. Until then we keep working, and ask before every "
        "larger decision. Anything on the page can still be changed after you approve.",
        "Cette page est ce que nous avons appris sur votre entreprise et votre marque. Une fois validée, l'équipe décide "
        "dans ce cadre et vous tient informés, au lieu de demander pour chaque détail. D'ici là nous continuons à "
        "travailler et demandons avant chaque décision importante. Tout peut encore être modifié après la validation.",
        "هذه الصفحة هي ما تعلمناه عن عملكم وعلامتكم. بعد موافقتكم يقرر الفريق ضمن هذا الإطار ويبلغكم، بدل السؤال عن كل "
        "تفصيل. حتى ذلك الحين نواصل العمل ونسأل قبل كل قرار كبير. يمكن تغيير أي شيء في الصفحة بعد الموافقة أيضًا.",
        "Эта страница — то, что мы узнали о вашем бизнесе и бренде. После вашего подтверждения команда решает в этих "
        "рамках и сообщает вам, а не спрашивает о каждой детали. До тех пор мы продолжаем работать и спрашиваем перед "
        "каждым крупным решением. Всё на странице можно изменить и после подтверждения."),
    "button": _five("מאשרים", "We approve", "Nous validons", "نوافق", "Подтверждаем"),
    "granted": _five("אישרתם את האפיון ב-{d}. הצוות מחליט בתוך המסגרת הזאת ומעדכן אתכם.",
                     "You approved this on {d}. The team decides within it and keeps you informed.",
                     "Vous avez validé ceci le {d}. L'équipe décide dans ce cadre et vous tient informés.",
                     "وافقتم على هذا في {d}. يقرر الفريق ضمن هذا الإطار ويبلغكم.",
                     "Вы подтвердили это {d}. Команда решает в этих рамках и сообщает вам."),
    "behalf": _five("האפיון אושר בשמכם על ידי הצוות ב-{d}, כי לא קיבלנו מכם תשובה. אם משהו לא מדויק, כתבו לנו ונתקן.",
                    "This was approved on your behalf by the team on {d}, because we did not hear back from you. "
                    "If anything is not accurate, write to us and we will correct it.",
                    "Ceci a été validé en votre nom par l'équipe le {d}, faute de réponse de votre part. "
                    "Si quelque chose n'est pas exact, écrivez-nous et nous corrigerons.",
                    "تمت الموافقة على هذا باسمكم من قبل الفريق في {d} لأننا لم نتلقَّ ردًّا منكم. إن كان شيء غير دقيق فاكتبوا لنا وسنصححه.",
                    "Это подтверждено от вашего имени командой {d}, так как мы не получили от вас ответа. "
                    "Если что-то неточно, напишите нам, и мы исправим."),
    "chat": _five("סיימנו לסכם את מה שלמדנו על העסק שלכם, והוא מחכה לאישור שלכם בעמוד המיתוג. אחרי האישור נחליט לבד בתוך "
                  "המסגרת הזאת ונעדכן אתכם, במקום לשאול על כל פרט.",
                  "We finished summing up what we learned about your business, and it is waiting for your approval on the "
                  "branding page. After you approve, we decide within it and keep you informed, instead of asking about every detail.",
                  "Nous avons fini de résumer ce que nous avons appris sur votre entreprise ; cela attend votre validation sur la "
                  "page de marque. Ensuite nous décidons dans ce cadre et vous tenons informés, au lieu de demander pour chaque détail.",
                  "انتهينا من تلخيص ما تعلمناه عن عملكم، وهو بانتظار موافقتكم في صفحة العلامة. بعد الموافقة نقرر ضمن هذا الإطار "
                  "ونبلغكم، بدل السؤال عن كل تفصيل.",
                  "Мы закончили описание того, что узнали о вашем бизнесе; оно ждёт вашего подтверждения на странице бренда. "
                  "После этого мы решаем в этих рамках и сообщаем вам, а не спрашиваем о каждой детали."),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _when(text):
    try:
        out = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
        return out if out.tzinfo else out.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


# ─── Doors (patched in tests) ────────────────────────────────────────────────

def _alert(lines: list):
    from core.agent_base import agent_alert
    agent_alert(SERVICE_NAME, lines)


def _post_chat(client_id: int, body: str) -> bool:
    from agents.client_agent import log_communication
    return bool(log_communication(client_id, "outbound", "dashboard_chat", body))


def _lang(client_id: int) -> str:
    try:
        from core import client_language
        return client_language.of(client_id)
    except Exception:
        return "he"


def _characterised(client_id: int) -> bool:
    from agents.interview_agent import is_completed
    return bool(is_completed(client_id))


def _brief_clients() -> list:
    rows = (brand_brief._db().table("app_settings").select("key").like("key", f"{brand_brief.PREFIX}%").limit(2000).execute().data or [])
    out = []
    for row in rows:
        try:
            out.append(int(str(row["key"])[len(brand_brief.PREFIX):]))
        except ValueError:
            pass
    return out


# ─── State ───────────────────────────────────────────────────────────────────

def state(client_id: int) -> dict:
    """{"state": "none" | "waiting" | "granted", "by", "at", "presented_at"}.
    "none" = no brief to approve yet."""
    grant = brand_brief.granted(client_id)
    presented = str(brand_brief.page_state(client_id).get("approval_presented_at") or "")
    if grant["granted"]:
        return {"state": "granted", "by": grant["by"], "at": grant["at"], "presented_at": presented}
    if not grant["exists"]:
        return {"state": "none", "by": "", "at": "", "presented_at": ""}
    return {"state": "waiting", "by": "", "at": "", "presented_at": presented}


def view(client_id: int, lang: str) -> dict:
    """The approval block of the client's branding page, in their language."""
    try:
        now = state(client_id)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: approval state unreadable ({type(e).__name__})")
        return {"state": "none"}
    if now["state"] == "none":
        return {"state": "none"}
    if now["state"] == "granted":
        key = "behalf" if now["by"] == "owner_on_behalf" else "granted"
        return {"state": "granted", "by": now["by"], "line": pick(TEXT[key], lang).replace("{d}", str(now["at"])[:10])}
    return {"state": "waiting", "title": pick(TEXT["title"], lang), "text": pick(TEXT["text"], lang),
            "button": pick(TEXT["button"], lang)}


# ─── The two approvals ───────────────────────────────────────────────────────

def approve_by_client(client_id: int) -> dict:
    if brand_brief.granted(client_id)["granted"]:
        return state(client_id)
    brand_brief.approve(client_id, by="client", grant="client")
    return state(client_id)


def owner_rule(client_id: int, now: datetime = None) -> dict:
    """May the owner approve on this client's behalf right now? Only a client
    who was asked and has been silent SILENT_HOURS (§4)."""
    now = now or _now()
    current = state(client_id)
    if current["state"] == "none":
        return {"allowed": False, "reason": "no_brief"}
    if current["state"] == "granted":
        return {"allowed": False, "reason": "already_approved", "by": current["by"]}
    asked = _when(current["presented_at"])
    if not asked:
        return {"allowed": False, "reason": "client_not_asked"}
    hours = (now - asked).total_seconds() / 3600
    if hours < SILENT_HOURS:
        return {"allowed": False, "reason": "client_has_time", "hours_left": round(SILENT_HOURS - hours, 1),
                "presented_at": current["presented_at"]}
    return {"allowed": True, "presented_at": current["presented_at"], "silent_hours": round(hours, 1)}


def approve_on_behalf(client_id: int, name: str = "") -> dict:
    """The owner's approval for a silent client. Refused (BriefError with the
    reason) in every other case. Recorded as on-behalf, alerted."""
    rule = owner_rule(client_id)
    if not rule["allowed"]:
        raise brand_brief.BriefError(rule["reason"])
    who = " ".join(str(name or "").split())[:30]
    brand_brief.approve(client_id, by=f"owner:{who}" if who else "owner", grant="owner_on_behalf")
    try:
        _alert([f"client {client_id}: the brand brief was approved ON THE CLIENT'S BEHALF by {who or 'the owner'} after "
                f"{rule['silent_hours']} hours of silence. The client's branding page says so. The client can still change it."])
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: on-behalf approval not alerted ({type(e).__name__})")
    return state(client_id)


# ─── Asking the client (once) ────────────────────────────────────────────────

def present(client_id: int) -> bool:
    """Ask the client once: stamp first (never two messages), then the line in
    the manager's chat. True when the client was asked now."""
    current = state(client_id)
    if current["state"] != "waiting" or current["presented_at"]:
        return False
    brand_brief.set_page_state(client_id, approval_presented_at=_now().isoformat())
    try:
        _post_chat(client_id, pick(TEXT["chat"], _lang(client_id)))
    except Exception as e:
        _alert([f"client {client_id}: the brand brief is waiting for the client's approval on the branding page, but the chat "
                f"line asking for it was not posted ({type(e).__name__})."])
    return True


def present_due() -> dict:
    """Every client with a brief that nobody was asked to approve yet, whose
    deep characterisation is done. Never raises."""
    asked, errors = [], 0
    try:
        clients = _brief_clients()
    except Exception as e:
        return {"error": type(e).__name__}
    for client_id in clients:
        try:
            current = state(client_id)
            if current["state"] == "waiting" and not current["presented_at"] and _characterised(client_id):
                if present(client_id):
                    asked.append(client_id)
        except Exception as e:
            errors += 1
            print(f"[{SERVICE_NAME}] client {client_id}: not presented ({type(e).__name__})")
    return {"asked": asked, "errors": errors}


def admin_state(client_id: int) -> dict:
    """For the Control Room's brief panel."""
    try:
        return {**state(client_id), "owner": owner_rule(client_id)}
    except Exception as e:
        return {"state": "unknown", "owner": {"allowed": False, "reason": type(e).__name__}}
