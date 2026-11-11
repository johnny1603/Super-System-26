"""uallak's landing-page agent — conversion copy + the client-facing lifecycle.

Division of labor (coordinates with, never duplicates):
- `core/landing_pages.py` owns the data, the 3-page ceiling, the renderers
  and THE rail: every page is served inside a CSP sandbox (page_csp), which
  is what lets the model write the page's HTML (see that module).
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

DESIGNED PAGES (2026-11-07): after the structured copy, the model designs
the whole page (core/landing_creative) as the next version; the fixed
template is the automatic fallback when that fails or the kill switch
LANDING_CREATIVE_HTML_ENABLED is off. The copy call keeps its old role as the
page's FACT base (the designer may restructure it, never add facts to it).

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
COPY_CALL_TIMEOUT_S = 240
# No tight cap on the copy (2026-11-07, freedom first): billed by what is used
COPY_MAX_TOKENS = 8000

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


def generate_copy(client_id: int, goal: str, title: str = "", current: dict = None) -> dict:
    """Research-grounded landing copy as STRUCTURED fields. Returns
    {"success", "content", "notes"} — writes nothing on its own.
    `current`: a rewrite's present copy (it may hold the client's own edits),
    kept except where the feedback asks otherwise. A first build has none."""
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
    if current:
        payload["current_copy"] = current
        payload["rewrite_rule"] = ("current_copy is the page as it is now, including the client's own edits: "
                                   "keep it except where the feedback asks for a change.")

    log_step(AGENT_NAME, "generate_copy",
             f"client {client_id}: '{(goal or title)[:60]}' "
             f"(research={'yes' if research else 'none'})")
    try:
        drafted = timed_step(
            AGENT_NAME, "copy_llm",
            lambda: safe_claude_json_call(COPY_SYSTEM,
                                          json.dumps(payload, ensure_ascii=False),
                                          max_tokens=COPY_MAX_TOKENS, client_id=client_id,
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


# ─── The designed page (core/landing_creative) ──────────────────────────────

def _creative_html(client_id: int, page: dict, content: dict, contact: dict, feedback: str = "") -> dict:
    """The model's own page for this copy: {"html", "record"} on success,
    {"record"} on a failure (the admin is alerted; the fixed template shows),
    {} when the kill switch is off. Never raises."""
    from core import landing_creative
    if not landing_creative.enabled():
        return {}
    try:
        from agents.seo_agent import _business_context
        business = _business_context(client_id)
    except Exception:
        business = {}
    # Real landing pages in the niche (search + fetch, cached) and the ad
    # lens the copy already used (a cache hit). Enrichment only: '' on failure.
    landing_research = competitor_research.summary_for_prompt(client_id, "landing")
    niche = competitor_research.summary_for_prompt(client_id, "ads")
    result = timed_step(AGENT_NAME, "creative_llm", lambda: landing_creative.generate(
        client_id, page, content, contact, content.get("images") or [], business,
        landing_research=landing_research, niche_research=niche, feedback=feedback))
    if not result.get("success"):
        record = result.get("record") or {}
        landing_creative.record_generation(client_id, record, agent=AGENT_NAME)
        agent_alert(AGENT_NAME, [f"client {client_id}: designed landing page for page {page.get('id')} failed "
                                 f"({record.get('outcome')}: {record.get('error') or record.get('stop_reason')}) — "
                                 f"the fixed template is used for this version"])
        return {"record": record}
    return result


def _design(client_id: int, page_id: int, feedback: str = "") -> dict:
    """Design the page's NEWEST version (its copy, photos and contact) as a
    new version. {"designed": bool, "version": int}."""
    page = landing_pages.get_page(client_id, page_id)
    if not page:
        return {"designed": False}
    content = landing_pages.normalize_content(page.get("content") or {})
    content.pop("html", None)
    content.pop("copy_stale", None)      # a new design is made FROM this copy
    contact = landing_pages.normalize_contact(page.get("contact") or {})
    with cost_action("landing_page_generate", ref=f"lp:{page_id}"):
        creative = _creative_html(client_id, page, content, contact, feedback)
    if not creative.get("html"):
        return {"designed": False}
    from core import landing_creative
    updated = landing_pages.update_page(client_id, page_id, {"content": {**content, "html": creative["html"]}},
                                        source="designed")
    if not updated.get("success"):
        record = {**creative["record"], "outcome": "not_stored", "error": str(updated.get("code"))}
        landing_creative.record_generation(client_id, record, agent=AGENT_NAME)
        agent_alert(AGENT_NAME, [f"client {client_id}: designed page {page_id} not stored ({updated.get('code')})"])
        return {"designed": False}
    version = updated["page"].get("current_version")
    landing_creative.record_generation(client_id, creative["record"], version=version, agent=AGENT_NAME)
    _log_activity(client_id, "landing_page_designed",
                  {"page_id": page_id, "version": version, "content_hash": creative["record"].get("content_hash"),
                   "exemplar": creative["record"].get("exemplar")})
    return {"designed": True, "version": version, "page": updated["page"]}


def _design_then_propose(client_id: int, page_id: int, feedback: str, persona: str, intro: str):
    """The dashboard buttons' background step: design, then the card (for the
    designed version, or for the copy version when designing failed)."""
    try:
        _design(client_id, page_id, feedback)
    except Exception as e:
        agent_alert(AGENT_NAME, [f"client {client_id}: designing page {page_id} crashed ({type(e).__name__})"])
    propose_publish(client_id, page_id, intro, persona=persona)


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
                images: list = None, defer=None) -> dict:
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
    opened — a refused photo opens nothing.
    The copy is one version; the designed page (core/landing_creative) the
    next, and the card offers the newest. `defer` (a scheduler such as
    BackgroundTasks.add_task — the dashboard's synchronous buttons): the
    design step and the card run after the response, so a request never
    waits the minutes a designed page takes."""
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

    card, designed, designing = {}, False, False
    if copy_result.get("success"):
        intro = (f'הכנתי דף נחיתה חדש: "{page["title"]}" ✍️ '
                 f'בכרטיס יש תצוגה מקדימה. מאשרים, והוא עולה לאוויר. '
                 f'רוצים לשנות משהו? כתבו לי כאן.') if announce else ""
        from core import landing_creative
        if defer is not None and landing_creative.enabled():
            defer(_design_then_propose, client_id, page["id"], "", persona, intro)
            designing = True
        else:
            result = _design(client_id, page["id"])
            designed = result.get("designed", False)
            page = result.get("page") or page
            card = propose_publish(client_id, page["id"], intro, persona=persona)
    return {"success": True, "page": page,
            "copy_generated": bool(copy_result.get("success")),
            "designed": designed, "designing": designing,
            "card": card.get("card"),
            "notes_for_johnny": copy_result.get("notes", "")}


def fulfil_team_request(client_id: int, request_id: int, title: str, goal: str = "",
                        dry_run: bool = False, defer=None) -> dict:
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
    created = create_page(client_id, title, goal or request_text, persona=view["persona"], defer=defer)
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
            "preview_card": bool(created.get("card")) or created.get("designing", False),
            "request_closed": closed}


def _import_images(client_id: int, images: list, title: str) -> dict:
    """The card's photo selection → stored refs, or a refusal code."""
    from core import landing_images
    try:
        return {"refs": landing_images.import_selection(client_id, images, title)}
    except landing_images.ImageError as e:
        log_step(AGENT_NAME, "images", f"client {client_id}: refused ({e.code})")
        return {"code": f"ERR_LANDING_IMAGES_{e.code.upper()}"}


def regenerate_copy(client_id: int, page_id: int, feedback: str = "", persona: str = PERSONA,
                    announce: bool = True, images: list = None, defer=None) -> dict:
    """Rewrite one page's copy, optionally with the client's own chat feedback
    folded into the goal — which is how "make it warmer / lead with price"
    arrives, since review happens in conversation. The rewrite is a NEW
    version; the live one stays live until the client approves the card.

    `images`: the card's photo selection (landing_images.clean_selection) —
    the FULL set the new version shows; copied into our store first (before
    any paid call), so a refused photo stops the run with nothing changed.
    No feedback + images = only the photos change, the text stays as is.
    Without `images`, the page keeps the photos it has. Either way the page
    is then designed again (its photos and copy are part of the design);
    `defer` as in create_page."""
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
    # The old design shows the old copy and photos: never carried over
    content.pop("html", None)
    stale = content.pop("copy_stale", False)
    history = landing_pages.normalize_revisions(content.get("revisions"))
    if feedback or not images:
        goal = page.get("goal") or ""
        if feedback:
            goal = f"{goal}\n\nCLIENT FEEDBACK ON THE PREVIOUS VERSION: {feedback}".strip()
        goal = _with_history(goal, history)
        current = None if stale else _copy_of(content)
        with cost_action("landing_page_generate", ref=f"lp:{page_id}"):
            result = (generate_copy(client_id, goal, page.get("title", ""), current=current) if current
                      else generate_copy(client_id, goal, page.get("title", "")))
        if not result.get("success"):
            return result
        content = {**result["content"], "images": (page.get("content") or {}).get("images") or []}
    if refs is not None:
        content["images"] = refs
    content["revisions"] = history
    if feedback:
        content = landing_pages.add_revision(content, feedback, "rewrite")
    updated = landing_pages.update_page(client_id, page_id, {"content": content},
                                        source="images" if images and not feedback else "rewrite")
    if not updated.get("success"):
        return updated
    _log_activity(client_id, "landing_page_copy_regenerated",
                  {"page_id": page_id, "had_feedback": bool(feedback), "images": len(refs or [])})
    intro = (f'כתבתי מחדש את "{page.get("title", "")}". הגרסה החדשה בכרטיס — '
             f'מה שבאוויר עכשיו לא משתנה עד שתאשרו.') if announce else ""
    from core import landing_creative
    design_note = _with_history(feedback, history) if feedback else ""
    if defer is not None and landing_creative.enabled():
        defer(_design_then_propose, client_id, page_id, design_note, persona, intro)
        return {"success": True, "page": updated["page"], "card": None, "designing": True}
    result = _design(client_id, page_id, design_note)
    page_now = result.get("page") or updated["page"]
    card = propose_publish(client_id, page_id, intro, persona=persona)
    return {"success": True, "page": page_now, "card": card.get("card"), "designed": result.get("designed", False)}


# ─── Change requests: surgical edit first (2026-11-10) ─────────────────────
#
# A change in words to a page that already has a design edits THAT page
# (core/landing_creative.surgical_edit — one cheap call, find-and-replace
# pairs, strict checks). It falls back to ONE full rebuild when the edit
# can't be applied; an explicit "new design" goes straight to the rebuild.
# Every request is kept on the page (content.revisions) so the next one
# knows it. Pages without a design, and photo changes, keep regenerate_copy.

EDIT_HISTORY_IN_PROMPT = 10

_REDESIGN_WORDS = ("עיצוב חדש", "עיצוב אחר", "לעצב מחדש", "תעצב מחדש", "תעצבו מחדש", "מעצב מחדש", "מחדש לגמרי",
                   "מאפס", "מההתחלה", "דף חדש לגמרי", "לבנות מחדש", "תבנה מחדש", "new design", "redesign",
                   "from scratch", "start over", "rebuild")
_VAGUE_WORDS = ("יותר יפה", "יפה יותר", "יותר מקצועי", "מקצועי יותר", "לשדרג", "תשדרג", "שדרג", "שדרוג",
                "לשפר את הדף", "תשפר את הדף", "יותר מודרני", "מודרני יותר", "יותר טוב", "טוב יותר",
                "יותר מרשים", "יותר מושך", "make it nicer", "nicer", "more professional", "upgrade", "improve it",
                "look better", "more modern")
# Something concrete on the page: a request naming one of these is specific
_SPECIFIC_WORDS = ("כותרת", "כפתור", "צבע", "ירוק", "כחול", "אדום", "שחור", "לבן", "צהוב", "כתום", "סגול", "ורוד",
                   "אפור", "טקסט", "משפט", "מילה", "פסקה", "סעיף", "חלק", "טופס", "תמונה", "לוגו", "גופן", "פונט",
                   "מחיר", "טלפון", "וואטסאפ", "שאלות", "להסיר", "תסיר", "תוריד", "להוריד", "למחוק", "תמחק",
                   "להוסיף", "תוסיף", "לשנות את", "תשנה את", "headline", "button", "color", "colour", "section",
                   "remove", "font", "text", "form", "logo", "price")


def classify_request(text: str) -> str:
    """'redesign' (an explicit new design), 'unclear' (overall look only:
    "make it nicer") or 'small' (anything concrete). Deterministic — used when
    the chat model gave no scope."""
    low = " ".join(str(text or "").lower().split())
    if any(w in low for w in _REDESIGN_WORDS):
        return "redesign"
    if any(w in low for w in _VAGUE_WORDS) and not any(w in low for w in _SPECIFIC_WORDS):
        return "unclear"
    return "small"


def _copy_of(content: dict) -> dict:
    """The structured copy as it is now (it may hold the client's own edits)."""
    from core.landing_creative import COPY_FIELDS
    return {k: content.get(k) for k in COPY_FIELDS if content.get(k)}


def _with_history(text: str, history: list) -> str:
    earlier = [h["request"] for h in history or []][-EDIT_HISTORY_IN_PROMPT:]
    if not earlier:
        return text
    lines = "\n".join(f"- {r}" for r in earlier)
    return f"{text}\n\nEARLIER CHANGE REQUESTS ON THIS PAGE (already made — keep them):\n{lines}".strip()


def _surgical(client_id: int, page: dict, request: str, persona: str, intro: str) -> dict:
    """One surgical edit of the page's CURRENT saved version (it already holds
    every earlier fix and the client's own edits). {"success", "page"} or
    {"success": False, "needs_redesign"}; stores nothing on a failure."""
    from core import landing_creative
    page_id = page["id"]
    content = landing_pages.normalize_content(page.get("content") or {})
    history = content.get("revisions") or []
    current = {} if content.get("copy_stale") else _copy_of(content)
    with cost_action("landing_page_edit", ref=f"lp:{page_id}"):
        result = timed_step(AGENT_NAME, "surgical_llm", lambda: landing_creative.surgical_edit(
            client_id, page_id, content["html"], request,
            [h["request"] for h in history][-EDIT_HISTORY_IN_PROMPT:], current))
    record = result.get("record") or {}
    if not result.get("success"):
        landing_creative.record_generation(client_id, record, agent=AGENT_NAME)
        return {"success": False, "needs_redesign": result.get("needs_redesign", False),
                "reason": record.get("error") or record.get("outcome")}
    new_content, stale = landing_creative.sync_copy(content, content["html"], result["html"], result["pairs"],
                                                    result["copy_updates"])
    new_content["html"] = result["html"]
    new_content["copy_stale"] = stale
    new_content = landing_pages.add_revision(new_content, request, "edit")
    updated = landing_pages.update_page(client_id, page_id, {"content": new_content}, source="edit")
    if not updated.get("success"):
        landing_creative.record_generation(client_id, {**record, "outcome": "not_stored",
                                                       "error": str(updated.get("code"))}, agent=AGENT_NAME)
        return {"success": False, "reason": "not_stored"}
    version = updated["page"].get("current_version")
    landing_creative.record_generation(client_id, record, version=version, agent=AGENT_NAME)
    _log_activity(client_id, "landing_page_edited",
                  {"page_id": page_id, "version": version, "pairs": record.get("pairs"), "copy_stale": stale,
                   "cost_ils": record.get("cost_ils"), "retries": record.get("retries")})
    propose_publish(client_id, page_id, intro, persona=persona)
    return {"success": True, "page": updated["page"], "mode": "edit"}


def _rebuild(client_id: int, page: dict, request: str, persona: str, intro: str) -> dict:
    """The full rebuild of a designed page — new copy, then a new design —
    stored as ONE version only when the design came out (a failure never
    leaves a plain-template version behind). Runs at most once per request."""
    page_id = page["id"]
    content = landing_pages.normalize_content(page.get("content") or {})
    stale = content.get("copy_stale")
    history = content.get("revisions") or []
    goal = f"{page.get('goal') or ''}\n\nCLIENT FEEDBACK ON THE PREVIOUS VERSION: {request}".strip()
    goal = _with_history(goal, history)
    current = None if stale else _copy_of(content)
    with cost_action("landing_page_generate", ref=f"lp:{page_id}"):
        copy = (generate_copy(client_id, goal, page.get("title", ""), current=current) if current
                else generate_copy(client_id, goal, page.get("title", "")))
        if not copy.get("success"):
            return {"success": False, "reason": "copy_failed"}
        new_content = {**copy["content"], "images": content.get("images") or []}
        contact = landing_pages.normalize_contact(page.get("contact") or {})
        creative = _creative_html(client_id, page, new_content, contact, _with_history(request, history))
    if not creative.get("html"):
        return {"success": False, "reason": "design_failed"}
    from core import landing_creative
    new_content = landing_pages.add_revision({**new_content, "html": creative["html"], "revisions": history},
                                             request, "redesign")
    updated = landing_pages.update_page(client_id, page_id, {"content": new_content}, source="designed")
    if not updated.get("success"):
        landing_creative.record_generation(client_id, {**creative["record"], "outcome": "not_stored",
                                                       "error": str(updated.get("code"))}, agent=AGENT_NAME)
        return {"success": False, "reason": "not_stored"}
    version = updated["page"].get("current_version")
    landing_creative.record_generation(client_id, creative["record"], version=version, agent=AGENT_NAME)
    _log_activity(client_id, "landing_page_designed",
                  {"page_id": page_id, "version": version, "content_hash": creative["record"].get("content_hash"),
                   "exemplar": creative["record"].get("exemplar"), "rebuild_for_change": True})
    propose_publish(client_id, page_id, intro, persona=persona)
    return {"success": True, "page": updated["page"], "mode": "redesign"}


_WHATSAPP_WORDS = ("וואטסאפ", "ווטסאפ", "וואצאפ", "ווצאפ", "whatsapp")


def _whatsapp_number_in(request: str) -> str:
    """The new WhatsApp number when the request is about the WhatsApp number and
    names exactly ONE usable phone number; else ''. The number on a page lives
    in its `contact` ({{WHATSAPP_URL}} is filled at render time), so no model
    edit of the HTML can change it (2026-11-11, card 40)."""
    import re
    text = request or ""
    if not any(w in text.lower() for w in _WHATSAPP_WORDS):
        return ""
    found = {landing_pages.whatsapp_number(m) for m in re.findall(r"\+?\d[\d\s-]{7,}\d", text)}
    found.discard("")
    return found.pop() if len(found) == 1 else ""


def _change_whatsapp(client_id: int, page: dict, number: str, persona: str) -> dict:
    """A new version with only the WhatsApp number changed, then the publish card."""
    contact = {**landing_pages.normalize_contact(page.get("contact") or {}), "whatsapp": number}
    updated = landing_pages.update_page(client_id, page["id"], {"contact": contact}, source="edit")
    if not updated.get("success"):
        return {"success": False, "reason": str(updated.get("code"))}
    _log_activity(client_id, "landing_page_edited", {"page_id": page["id"],
                                                     "version": updated["page"].get("current_version"),
                                                     "contact": "whatsapp"})
    propose_publish(client_id, page["id"], "", persona=persona)
    return {"success": True, "page": updated["page"], "mode": "contact"}


def change_page(client_id: int, page_id: int, change_request: str = "", mode: str = "edit",
                persona: str = PERSONA, images: list = None) -> dict:
    """THE entry point for a client's change request (the edit card).

    mode "edit": a surgical edit of the designed page; if it can't be applied
    (after its one retry) → ONE full rebuild; if that fails too → the team is
    alerted and the client reads a calm line (ERR_LANDING_IN_HAND).
    mode "redesign": straight to the full rebuild.
    A page with no design, a photo change, or the kill switch off: the
    existing rewrite (regenerate_copy)."""
    from core import landing_creative
    page = landing_pages.get_page(client_id, page_id)
    if not page:
        return {"success": False, "code": "ERR_LANDING_NOT_FOUND", "errors": ["page not found"]}
    number = "" if images else _whatsapp_number_in(change_request)
    if number:
        changed = _change_whatsapp(client_id, page, number, persona)
        if changed.get("success"):
            return changed
    designed = bool((page.get("content") or {}).get("html"))
    if images or not change_request or not designed or not landing_creative.enabled():
        return regenerate_copy(client_id, page_id, change_request, persona=persona, announce=False,
                               images=images or None)
    if mode == "redesign":
        result = _rebuild(client_id, page, change_request, persona, "")
        if result.get("success"):
            return result
        agent_alert(AGENT_NAME, [f"client {client_id}: requested redesign of page {page_id} failed "
                                 f"({result.get('reason')}) — nothing stored, client told it is in hand"])
        return {"success": False, "code": "ERR_LANDING_IN_HAND"}
    edited_now = _surgical(client_id, page, change_request, persona, "")
    if edited_now.get("success"):
        return edited_now
    log_step(AGENT_NAME, "change_page", f"client {client_id}: surgical edit of page {page_id} not applied "
                                        f"({edited_now.get('reason') or 'needs redesign'}) — one full rebuild")
    rebuilt = _rebuild(client_id, page, change_request, persona, "")
    if rebuilt.get("success"):
        return {**rebuilt, "fell_back": True}
    agent_alert(AGENT_NAME, [f"client {client_id}: change to page {page_id} failed — surgical edit "
                             f"({edited_now.get('reason') or 'needs redesign'}) and the one rebuild "
                             f"({rebuilt.get('reason')}); nothing stored, the client was told it is in hand. "
                             f"Request: {change_request[:200]}"])
    return {"success": False, "code": "ERR_LANDING_IN_HAND"}


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
    # Which exact content went live: version + hash, in the log and the row
    live = landing_pages.visible_of(client_id, page, result.get("version") or None)
    from core.landing_creative import content_hash
    digest = content_hash(json.dumps(live["content"], ensure_ascii=False, sort_keys=True))
    print(f"[{AGENT_NAME}] client {client_id} page {page_id} published v{result.get('version')} "
          f"designed={bool(live['content'].get('html'))} hash={digest}")
    _log_activity(client_id, "landing_page_published",
                  {"page_id": page_id, "slug": page["slug"], "url": url, "version": result.get("version"),
                   "content_hash": digest, "designed": bool(live["content"].get("html"))})
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
