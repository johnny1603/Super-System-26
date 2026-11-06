"""uallak's landing-page agent — conversion copy + the client-facing lifecycle.

Division of labor (coordinates with, never duplicates):
- `core/landing_pages.py` owns the data, the 3-page ceiling and the renderer.
  This agent never writes HTML — it produces STRUCTURED copy that the fixed
  template escapes (that separation is a security boundary; see that module).
- `core/landing_domains.py` owns the custom-domain state machine.
- `core/competitor_research.py` supplies the market view, through the SAME
  `"ads"` lens the campaign drafters use — a landing page and an ad are the
  same job (one offer, one action), so they deserve the same research and the
  same cache entry rather than a fourth lens.
- `agents/seo_agent._business_context` supplies who the client is. Reused, not
  reimplemented, exactly as media_agent and website_agent reuse it.

NOTHING here generates a parallel research or copywriting path: the handoff's
question was whether the recently-built capabilities could be reused, and they
could — this agent is the assembly, not a second brain.

CLARIFICATIONS GO THROUGH THE EXISTING CHAT. Choosing the offer for a page,
reviewing generated copy, confirming a DNS record was added — all of it
surfaces as a `dashboard_chat` communication, the same channel
website_agent's self-provisioning already talks on. There is deliberately no
email, no standalone form and no out-of-band channel in this file.

THE FLOW (docs/LANDING_PAGES.md, 2026-11-01): the agent writes (a new
version) → a `publish_landing_page` card with a preview link in the website
agent's (אורי) thread → the client's click publishes exactly that version →
a "live" line with the URL. Nothing goes live without that click; a revert
is one tap.
"""
import json
import os

from core import competitor_research, landing_domains, landing_pages
from core.agent_base import agent_alert, log_step, timed_step
from core.claude_json import ClaudeJSONError, safe_claude_json_call
from core.cost_tracker import cost_action

AGENT_NAME = "landing_page_agent"
# Landing pages are the website agent's (אורי) work: its chat thread carries
# the preview cards, the "live" line and the tracking offer.
PERSONA = "website"


def _log_activity(client_id: int, action_type: str, details: dict, result: dict = None):
    from agents.client_agent import log_activity
    log_activity(client_id, AGENT_NAME, action_type, details, result or {})


def _chat(client_id: int, message: str):
    """The ONE client-facing channel this agent uses (point 7 of the handoff).
    Same helper website_agent's provisioning flow uses, so landing-page updates
    land in the conversation the client already reads."""
    from agents.client_agent import log_communication
    log_communication(client_id, "outbound", "dashboard_chat", message)


# ─── Copy generation ──────────────────────────────────────────────────────────

# One copy call may take at most this long (one transport retry): the edit /
# create card's whole run has a hard deadline (agent_actions.run_deadline),
# and the SDK's default 10 minutes × retries outlived it (proposal 16).
COPY_CALL_TIMEOUT_S = 120

COPY_SYSTEM = f"""You are uallak's conversion copywriter, writing ONE Hebrew landing page
for an Israeli small business. A landing page is NOT a website page: one offer, one action,
no navigation, no company history. Every line either moves the reader toward the form or
gets cut.

You receive the business context, the page's goal/offer, and competitor research for the
niche.

Rules:
- HEBREW throughout, written for a real person, not a brochure. Second person, warm, direct.
- The headline promises ONE specific outcome for the visitor — never the business's name,
  never "ברוכים הבאים".
- Use the competitor research for the offers and objections that recur in this space, and
  prefer its stated GAP — the angle nobody else runs. INFORM, NEVER COPY: never reproduce a
  competitor's wording and never name a competitor on the page.
- NEVER invent facts: no prices, discounts, guarantees, delivery times, years in business,
  certifications, review counts or "מספר 1" claims unless they appear in the business
  context. Israeli consumer-protection rules bite here, and a fabricated claim on a page
  with the client's name on it is the worst failure this agent can produce.
- No urgency theatre ("נותרו 2 מקומות בלבד!"), no fake scarcity, no countdowns.
- benefits are concrete outcomes, not adjectives.

HARD LIMITS (longer output is truncated, so stay inside them):
- headline: max {landing_pages.MAX_HEADLINE_CHARS} characters
- subheadline: max {landing_pages.MAX_SUB_CHARS} characters, one sentence
- benefits: 3-{landing_pages.MAX_BENEFITS} items, max {landing_pages.MAX_BENEFIT_CHARS} chars each
- sections: 1-{landing_pages.MAX_SECTIONS} items; title max {landing_pages.MAX_SECTION_TITLE_CHARS},
  body max {landing_pages.MAX_SECTION_BODY_CHARS} chars
- cta_text: max {landing_pages.MAX_CTA_CHARS} characters, an action ("קבעו שיחה", "לקבלת הצעה")
- form_note: max {landing_pages.MAX_SUB_CHARS} chars — one reassuring line under the form

Return JSON only:
{{"headline": "Hebrew", "subheadline": "Hebrew",
  "benefits": ["Hebrew"],
  "sections": [{{"title": "Hebrew", "body": "Hebrew"}}],
  "cta_text": "Hebrew", "form_note": "Hebrew",
  "notes_for_johnny": "English, max 2 sentences — anything the copy deliberately avoided"}}"""


def generate_copy(client_id: int, goal: str, title: str = "") -> dict:
    """Research-grounded landing copy as STRUCTURED fields. Returns
    {"success", "content", "notes"} — writes nothing on its own."""
    from agents.seo_agent import _business_context

    # Same lens the ad drafters use, so a client who had a campaign drafted this
    # week pays nothing extra here — the cache is already warm.
    research = competitor_research.summary_for_prompt(client_id, "ads")
    payload = {
        "business": _business_context(client_id),
        "page_goal": goal or title,
        "page_title": title,
    }
    if research:
        payload["competitor_research"] = research

    log_step(AGENT_NAME, "generate_copy",
             f"client {client_id}: '{(goal or title)[:60]}' "
             f"(research={'yes' if research else 'none'})")
    try:
        drafted = timed_step(
            AGENT_NAME, "copy_llm",
            lambda: safe_claude_json_call(COPY_SYSTEM,
                                          json.dumps(payload, ensure_ascii=False),
                                          max_tokens=1600, client_id=client_id,
                                          cost_category="claude_landing_page",
                                          timeout=COPY_CALL_TIMEOUT_S))
    except ClaudeJSONError as e:
        agent_alert(AGENT_NAME, [f"client {client_id}: landing copy generation failed: {e}"])
        return {"success": False, "errors": [str(e)]}

    content = landing_pages.normalize_content(drafted)
    if not content["headline"]:
        agent_alert(AGENT_NAME, [f"client {client_id}: landing copy came back with no "
                                 f"headline — page left for manual editing"])
    return {"success": True, "content": content,
            "notes": drafted.get("notes_for_johnny", ""),
            "research_grounded": bool(research)}


# ─── Create (the main entry point) ────────────────────────────────────────────

def _default_contact(client_id: int) -> dict:
    """The page's call / WhatsApp buttons from what the client already gave
    us (their account phone) — never typed into a form by them again."""
    try:
        from agents.client_agent import get_client
        client = get_client(client_id) or {}
    except Exception:
        return {}
    phone = client.get("phone") or ""
    return landing_pages.normalize_contact({"phone": phone, "whatsapp": phone})


def propose_publish(client_id: int, page_id: int, intro: str = "", persona: str = PERSONA) -> dict:
    """The approval card for the page's newest version, with one line saying
    what changed — in אורי's chat by default, or in the chat the work was
    asked for in (`persona`: the manager's thread when the client asked the
    manager). The click (agent_actions.publish_landing_page) is the only way
    a page goes live."""
    from core import agent_actions
    persona = persona or PERSONA
    result = agent_actions.propose(client_id, "client_chat", persona,
                                   {"name": "publish_landing_page", "params": {"page_id": page_id}},
                                   by_code=True)
    if intro:
        try:
            agent_actions._post_client_message(client_id, persona, intro)
        except Exception as e:
            log_step(AGENT_NAME, "propose_publish", f"client {client_id}: message not posted ({type(e).__name__})")
    return result


def create_page(client_id: int, title: str, goal: str = "",
                generate: bool = True, persona: str = PERSONA, announce: bool = True,
                images: list = None) -> dict:
    """Create ONE landing page, with generated copy by default, and propose
    publishing it (preview card in the website agent's chat, or in `persona`'s
    — the chat the client asked in). THE one builder: the dashboard's
    "+ דף נחיתה חדש" button, the chat's create_landing_page card and the
    admin path all call this.

    The page ceiling is enforced in `landing_pages.create_page` (server-side,
    before anything is generated) — so a client at the limit never burns an LLM
    call to be told no.

    `announce`: post the "I wrote a new page" line with the card. A chat
    card's run passes False — its own done line says it, once.
    `images`: the card's photo selection, copied in BEFORE the page is
    opened — a refused photo opens nothing."""
    refs = []
    if images:
        if landing_pages.count_pages(client_id) >= landing_pages.MAX_PAGES_PER_CLIENT:
            return {"success": False, "code": "ERR_LANDING_PAGE_LIMIT"}
        imported = _import_images(client_id, images, title)
        if imported.get("code"):
            return {"success": False, "code": imported["code"]}
        refs = imported["refs"]
    created = landing_pages.create_page(client_id, title, goal, copy_source="")
    if not created.get("success"):
        if created.get("code") == "ERR_LANDING_PAGE_LIMIT":
            # Not an error to alert on — it is the product working. The client
            # is told, in chat, what their options are.
            _chat(client_id, f'הגעתם ל-{landing_pages.MAX_PAGES_PER_CLIENT} דפי הנחיתה '
                             f'הכלולים בחבילה שלכם 🎉 רוצים עוד אחד? כתבו לי כאן ואבדוק '
                             f'את זה מול הצוות — נחזור אליכם עם תשובה.')
        return created

    page = created["page"]
    copy_result = {}
    # Every AI cost of this page is labelled with it — the admin cost view
    # sums client_costs by action_ref "lp:<id>" (core/landing_admin).
    with cost_action("landing_page_generate", ref=f"lp:{page['id']}"):
        if generate:
            copy_result = generate_copy(client_id, goal, title)
    fields = {"contact": _default_contact(client_id)}
    if copy_result.get("success"):
        fields["content"] = {**copy_result["content"], "images": refs}
    elif refs:
        fields["content"] = {"images": refs}
    updated = landing_pages.update_page(client_id, page["id"], fields, source="generated")
    if updated.get("success"):
        page = updated["page"]

    _log_activity(client_id, "landing_page_created",
                  {"page_id": page["id"], "slug": page["slug"], "title": title,
                   "goal": goal, "copy_generated": bool(copy_result.get("success")),
                   "research_grounded": copy_result.get("research_grounded", False)},
                  {"notes_for_johnny": copy_result.get("notes", "")})

    card = {}
    if copy_result.get("success"):
        intro = (f'הכנתי דף נחיתה חדש: "{page["title"]}" ✍️ '
                 f'בכרטיס יש תצוגה מקדימה. מאשרים, והוא עולה לאוויר. '
                 f'רוצים לשנות משהו? כתבו לי כאן.') if announce else ""
        card = propose_publish(client_id, page["id"], intro, persona=persona)
    return {"success": True, "page": page,
            "copy_generated": bool(copy_result.get("success")),
            "card": card.get("card"),
            "notes_for_johnny": copy_result.get("notes", "")}


def fulfil_team_request(client_id: int, request_id: int, title: str, goal: str = "",
                        dry_run: bool = False) -> dict:
    """A team request that asked for a landing page before the chat could
    build one (request #6, client 27, 2026-11-02): build the DRAFT now with
    THE builder, in the thread the request came from, and mark the request
    handled by the system (kept, never deleted). The client still publishes
    with their own click on the preview card. `goal` defaults to the
    request's own words. `dry_run`: show the request, change nothing."""
    from core import agent_actions
    row = agent_actions.open_team_request_row(client_id, request_id)
    if not row:
        return {"success": False, "code": "ERR_REQUEST_NOT_FOUND"}
    request_text = (row.get("params") or {}).get("request", "")
    view = {"request_id": request_id, "topic": (row.get("params") or {}).get("topic", ""),
            "request": request_text, "persona": row.get("persona") or "general"}
    if dry_run:
        return {"success": True, "dry_run": True, **view, "pages_used": landing_pages.count_pages(client_id),
                "pages_included": landing_pages.MAX_PAGES_PER_CLIENT}
    created = create_page(client_id, title, goal or request_text, persona=view["persona"])
    if not created.get("success"):
        return {"success": False, "code": created.get("code", "ERR_LANDING_CREATE_FAILED"), **view}
    if not created.get("copy_generated"):
        # The request stays open: nothing the client can approve exists yet
        return {"success": False, "code": "ERR_LANDING_COPY_FAILED", "page_id": created["page"]["id"], **view}
    closed = agent_actions.close_team_request_by_system(
        client_id, request_id,
        f"landing page draft {created['page']['id']} built by the chat's landing builder; "
        f"the client publishes it from the preview card")
    _log_activity(client_id, "team_request_fulfilled_by_system",
                  {"request_id": request_id, "page_id": created["page"]["id"]})
    return {"success": True, **view, "page_id": created["page"]["id"], "status": created["page"].get("status"),
            "preview_card": bool(created.get("card")), "request_closed": closed}


def _import_images(client_id: int, images: list, title: str) -> dict:
    """The card's photo selection → stored refs, or a refusal code."""
    from core import landing_images
    try:
        return {"refs": landing_images.import_selection(client_id, images, title)}
    except landing_images.ImageError as e:
        log_step(AGENT_NAME, "images", f"client {client_id}: refused ({e.code})")
        return {"code": f"ERR_LANDING_IMAGES_{e.code.upper()}"}


def regenerate_copy(client_id: int, page_id: int, feedback: str = "", persona: str = PERSONA,
                    announce: bool = True, images: list = None) -> dict:
    """Rewrite one page's copy, optionally with the client's own chat feedback
    folded into the goal — which is how "make it warmer / lead with price"
    arrives, since review happens in conversation. The rewrite is a NEW
    version; the live one stays live until the client approves the card.

    `images`: the card's photo selection (landing_images.clean_selection) —
    the FULL set the new version shows; copied into our store first (before
    any paid call), so a refused photo stops the run with nothing changed.
    No feedback + images = only the photos change, the text stays as is.
    Without `images`, the page keeps the photos it has."""
    page = landing_pages.get_page(client_id, page_id)
    if not page:
        return {"success": False, "code": "ERR_LANDING_NOT_FOUND",
                "errors": ["page not found"]}
    refs = None
    if images:
        imported = _import_images(client_id, images, page.get("title", ""))
        if imported.get("code"):
            return {"success": False, "code": imported["code"]}
        refs = imported["refs"]
    content = dict(page.get("content") or {})
    if feedback or not images:
        goal = page.get("goal") or ""
        if feedback:
            goal = f"{goal}\n\nCLIENT FEEDBACK ON THE PREVIOUS VERSION: {feedback}".strip()
        with cost_action("landing_page_generate", ref=f"lp:{page_id}"):
            result = generate_copy(client_id, goal, page.get("title", ""))
        if not result.get("success"):
            return result
        content = {**result["content"], "images": (page.get("content") or {}).get("images") or []}
    if refs is not None:
        content["images"] = refs
    updated = landing_pages.update_page(client_id, page_id, {"content": content},
                                        source="images" if images and not feedback else "rewrite")
    if not updated.get("success"):
        return updated
    _log_activity(client_id, "landing_page_copy_regenerated",
                  {"page_id": page_id, "had_feedback": bool(feedback), "images": len(refs or [])})
    intro = (f'כתבתי מחדש את "{page.get("title", "")}". הגרסה החדשה בכרטיס — '
             f'מה שבאוויר עכשיו לא משתנה עד שתאשרו.') if announce else ""
    card = propose_publish(client_id, page_id, intro, persona=persona)
    return {"success": True, "page": updated["page"], "card": card.get("card")}


def edited(client_id: int, page_id: int) -> dict:
    """After a client's own edit (a new version): offer it for publishing."""
    page = landing_pages.get_page(client_id, page_id)
    if not page:
        return {}
    if page.get("status") == "published" and page.get("current_version") == page.get("published_version"):
        return {}
    return propose_publish(client_id, page_id)


def publish_page(client_id: int, page_id: int, version: int = None, notify: bool = True) -> dict:
    """Draft → live, at `version` (default the newest). Called by the client's
    approval of the publish card (notify=False: the card posts its own done
    line), by a revert, or by the admin.

    After it is live: the admin hears once when the client reached the
    live-page limit, and a client without Tag Manager gets the connect card."""
    result = landing_pages.publish(client_id, page_id, version)
    if not result.get("success"):
        return result
    page = result["page"]
    state = landing_domains.get_state(client_id)
    url = page_url(client_id, page, state=state)
    _log_activity(client_id, "landing_page_published",
                  {"page_id": page_id, "slug": page["slug"], "url": url, "version": result.get("version")})
    try:
        from core import client_state
        client_state.forget(client_id)
    except Exception:
        pass

    _limit_alert(client_id)
    tracking_offered = _offer_tracking(client_id)
    if notify:
        line = f'דף הנחיתה "{page["title"]}" באוויר 🚀\n{url}'
        if state["mode"] != "active":
            line += '\nהכתובת הזו עובדת מצוין. כשנחבר את הדומיין שלכם, הדף יעבור אליו לבד.'
        _chat(client_id, line)
    return {"success": True, "page": page, "url": url, "version": result.get("version"),
            "shared_url": state["mode"] != "active", "tracking_offered": tracking_offered}


def revert_page(client_id: int, page_id: int, version: int) -> dict:
    """One tap back to an earlier version (it comes back as a new version; a
    live page goes live with it at once — the tap is the approval)."""
    result = landing_pages.revert(client_id, page_id, version)
    if not result.get("success"):
        return result
    _log_activity(client_id, "landing_page_reverted", {"page_id": page_id, "to_version": version,
                                                       "new_version": result.get("version")})
    try:
        from core import client_state
        client_state.forget(client_id)
    except Exception:
        pass
    return result


def unpublish_page(client_id: int, page_id: int, by: str = "client", reason: str = "") -> dict:
    """Instant: the next visit answers 'not found'. `by` admin = the team took
    it down (the client is told plainly, in the website agent's chat)."""
    page = landing_pages.get_page(client_id, page_id)
    result = landing_pages.unpublish(client_id, page_id, reason or by)
    if not result.get("success"):
        return result
    _log_activity(client_id, "landing_page_unpublished", {"page_id": page_id, "by": by})
    if by == "admin" and page:
        try:
            from core import agent_actions
            agent_actions._post_client_message(
                client_id, PERSONA, f'הורדנו זמנית את דף הנחיתה "{page.get("title", "")}" מהאוויר. '
                                    f'נחזור אליכם כאן עם הפרטים.')
        except Exception:
            pass
    try:
        from core import client_state
        client_state.forget(client_id)
    except Exception:
        pass
    return result


def _limit_alert(client_id: int):
    """ONE admin alert when a client reaches the live-page limit (the client
    never sees a counter)."""
    try:
        active = landing_pages.count_active(client_id)
        if active >= landing_pages.MAX_ACTIVE_PAGES:
            from core import chat_fallback
            chat_fallback.alert_once(AGENT_NAME, client_id, "landing_active_limit",
                                     f"client {client_id}: {active}/{landing_pages.MAX_ACTIVE_PAGES} landing "
                                     f"pages live — at the included limit. A further page is a pricing decision.",
                                     days=30)
    except Exception as e:
        log_step(AGENT_NAME, "limit_alert", f"client {client_id}: check failed ({type(e).__name__})")


def _offer_tracking(client_id: int) -> bool:
    """No Tag Manager connected → the connect card in this chat (once per open card)."""
    try:
        from core import gtm_connection, agent_actions
        if gtm_connection._connection(client_id).get("access_token"):
            return False
        proposed = agent_actions.propose(client_id, "client_chat", PERSONA,
                                         {"name": "connect_tag_manager", "params": {}})
        return bool(proposed.get("card"))
    except Exception as e:
        log_step(AGENT_NAME, "offer_tracking", f"client {client_id}: skipped ({type(e).__name__})")
        return False


def ad_destination(client_id: int, page_id: int = None) -> str:
    """A LIVE landing page's public URL for an ad's final URL — the named page,
    else the newest live one; '' when none is live."""
    try:
        pages = [p for p in landing_pages.list_pages(client_id) if p.get("status") == "published"]
    except Exception:
        return ""
    if page_id:
        pages = [p for p in pages if p.get("id") == page_id]
    if not pages:
        return ""
    return page_url(client_id, pages[0])


# ─── The 4th page: blocked, and priced by a HUMAN ─────────────────────────────

def request_extra_page(client_id: int, reason: str = "") -> dict:
    """A client wants more than the included pages.

    This function deliberately does NOT price, approve, or auto-create
    anything. Pricing is a business decision that lives with Johnny — the same
    principle as the ads agents refusing to infer a budget. All this does is
    record the request, alert him, and tell the client honestly that a human is
    looking at it."""
    used = landing_pages.count_pages(client_id)
    _log_activity(client_id, "landing_page_extra_requested",
                  {"current_pages": used, "reason": reason[:500]})
    agent_alert(AGENT_NAME, [
        f"client {client_id} has {used}/{landing_pages.MAX_PAGES_PER_CLIENT} landing pages "
        f"and asked for another. Reason: {reason[:200] or '(none given)'}. "
        f"THIS IS A PRICING DECISION — nothing was created or quoted. Decide, then either "
        f"create it via POST /api/landing-pages/admin/create or reply to the client."])
    _chat(client_id,
          'קיבלתי את הבקשה לדף נחיתה נוסף 🙏 החבילה שלכם כוללת '
          f'{landing_pages.MAX_PAGES_PER_CLIENT} דפים, אז אני מעביר את זה לצוות '
          'לבדיקה ונחזור אליכם עם תשובה כאן בצ׳אט.')
    return {"success": True, "status": "forwarded_to_team",
            "current_pages": used, "included": landing_pages.MAX_PAGES_PER_CLIENT}


# ─── Domain flow (all client contact via chat) ────────────────────────────────

def domain_request_message(result: dict) -> str:
    """The plain message for a requested domain — written to be forwarded
    verbatim. ONE text for the dashboard button and the chat card."""
    return (f'מעולה — נחבר את דפי הנחיתה לכתובת {result["hostname"]} 🌐\n\n'
            f'צריך דבר אחד: להעביר את ההודעה הבאה למי שמנהל לכם את הדומיין '
            f'(המעצב/מפתח/חברת האחסון). אין צורך להבין אותה — רק להעביר כמו שהיא:\n\n'
            f'━━━━━━━━━━━━━━\n{result["dns"]["forward_text"]}\n━━━━━━━━━━━━━━\n\n'
            f'ברגע שהם מאשרים שזה בוצע — כתבו לי כאן, ואני אבדוק ואעדכן אתכם. '
            f'עד אז הדפים ממשיכים לעבוד רגיל בכתובת הזמנית.')


def domain_verify_message(result: dict) -> str:
    """The plain result of a domain check (dashboard button and chat card)."""
    if result.get("verified"):
        return (f'הדומיין שלכם מחובר! ✅ דפי הנחיתה עברו לכתובת '
                f'{result["hostname"]} — הקישורים בדשבורד כבר מעודכנים.')
    if not result.get("uallak_side_ready", True):
        # Our fault, and the client must not be sent to chase their DNS provider
        return ('בדקתי — הרשומה שלכם בסדר גמור, ההשלמה בצד שלנו עוד רצה. '
                'אין מה לעשות מצדכם, אעדכן אתכם כאן ברגע שזה מוכן 🙏')
    return ('בדקתי ועדיין לא רואה את החיבור. שינויי DNS יכולים לקחת כמה שעות, '
            'אז נבדוק שוב מאוחר יותר — ואם עבר יותר מיום, שווה לוודא מול מי '
            'שמנהל את הדומיין שהרשומה נוספה בדיוק כפי שנשלחה.')


def request_domain(client_id: int, hostname: str, notify: bool = True) -> dict:
    """Generate the client's DNS record and tell them, in chat, exactly what to
    forward and to whom. `notify=False`: the chat card's done line says it."""
    result = landing_domains.request_domain(client_id, hostname)
    if not result.get("success"):
        return result

    _log_activity(client_id, "landing_domain_requested",
                  {"hostname": result["hostname"],
                   "registered_with_cloudflare": result.get("registered_with_cloudflare"),
                   "register_error": result.get("register_error", "")})
    if notify:
        _chat(client_id, domain_request_message(result))
    return result


def verify_domain(client_id: int, notify: bool = True) -> dict:
    """Check whether the client's DNS is live, and report the result in chat.
    Triggered by the dashboard's "בדקו עכשיו" button or by the chat card
    (`notify=False`: the card's done line says it)."""
    result = landing_domains.verify_domain(client_id)
    if not result.get("success"):
        return result

    _log_activity(client_id, "landing_domain_verified" if result["verified"]
                  else "landing_domain_verify_failed",
                  {"hostname": result.get("hostname", ""),
                   "reason": result.get("reason", "")})
    if not result["verified"] and not result.get("uallak_side_ready", True):
        agent_alert(AGENT_NAME, [
            f"client {client_id}: landing domain {result.get('hostname')} is waiting on "
            f"UALLAK-side setup (CLOUDFLARE_API_TOKEN / CLOUDFLARE_ZONE_ID / the Host "
            f"rewrite Worker). The client has been told it is on us, not on them."])
    if notify:
        _chat(client_id, domain_verify_message(result))
    return result


# ─── Public link building + dashboard payload ─────────────────────────────────

def page_url(client_id: int, page: dict, business_name: str = "",
             state: dict = None) -> str:
    """A page's public URL, on the client's own domain once verified and on the
    shared waiting URL until then. Built in ONE place so every surface agrees.

    `state` is an already-fetched landing_domains.get_state result — the list
    view passes it so rendering N pages costs one domain read, not 2N."""
    state = state or landing_domains.get_state(client_id)
    slug = page.get("slug", "")
    if state["mode"] == "active":
        # On their own hostname the client segment is redundant — the hostname
        # already identifies them (the Worker maps /{slug} to this client).
        return f"https://{state['hostname']}/{slug}"
    base = os.environ.get("PUBLIC_APP_URL", "https://app.uallak.com").rstrip("/")
    return f"{base}/lp/{landing_pages.client_slug(client_id, business_name)}/{slug}"


def dashboard_payload(client_id: int) -> dict:
    """Everything the Landing Pages section renders: the pages, their live URLs
    or pending state, per-page lead counts, and the domain card."""
    from agents.client_agent import get_client
    business_name = (get_client(client_id) or {}).get("business_name", "")
    from core import landing_tracking
    counts = landing_pages.lead_counts_by_page(client_id)
    stats = landing_tracking.stats_by_page(client_id, days=30)
    state = landing_domains.get_state(client_id)  # read once, reused per page
    pages = []
    for page in landing_pages.list_pages(client_id):
        # Counting on → no row really is zero; counting off (pre-migration) → None
        numbers = stats.get(page["id"]) or ({"views": 0, "calls": 0, "whatsapp": 0}
                                            if landing_tracking._stats_ok["value"] else {})
        pages.append({
            "id": page["id"], "slug": page["slug"], "title": page.get("title", ""),
            "goal": page.get("goal", ""), "status": page.get("status", "draft"),
            "url": page_url(client_id, page, business_name, state),
            "leads": counts.get(page["slug"], 0),
            # Last 30 days, our own counters (None = not counted yet, never a fake 0)
            "views": numbers.get("views") if numbers else None,
            "calls": numbers.get("calls") if numbers else None,
            "whatsapp": numbers.get("whatsapp") if numbers else None,
            "current_version": page.get("current_version") or 0,
            "published_version": page.get("published_version"),
            "has_unpublished_changes": bool(page.get("current_version")) and
                page.get("current_version") != page.get("published_version"),
            "created_at": page.get("created_at", ""),
            # The photos the newest version shows: where each sits and the
            # media-folder photo it was copied from (what a chat edit names
            # to keep it)
            "images": [{"placement": r["placement"], "section": r.get("section"),
                        "drive_file_id": r.get("source_file_id", "")}
                       for r in (landing_pages.normalize_content(page.get("content") or {}).get("images") or [])],
        })
    used = len(pages)
    return {
        "pages": pages,
        "used": used,
        "included": landing_pages.MAX_PAGES_PER_CLIENT,
        "can_create": used < landing_pages.MAX_PAGES_PER_CLIENT,
        "domain": state,
    }
