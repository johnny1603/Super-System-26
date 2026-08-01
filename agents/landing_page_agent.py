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
"""
import json
import os

from core import competitor_research, landing_domains, landing_pages
from core.agent_base import agent_alert, log_step, timed_step
from core.claude_json import ClaudeJSONError, safe_claude_json_call

AGENT_NAME = "landing_page_agent"


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
                                          cost_category="claude_landing_page"))
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

def create_page(client_id: int, title: str, goal: str = "",
                generate: bool = True) -> dict:
    """Create ONE landing page, with generated copy by default.

    The 3-page ceiling is enforced in `landing_pages.create_page` (server-side,
    before anything is generated) — so a client at the limit never burns an LLM
    call to be told no."""
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
    if generate:
        copy_result = generate_copy(client_id, goal, title)
        if copy_result.get("success"):
            landing_pages.update_page(client_id, page["id"],
                                      {"content": copy_result["content"]})
            page = landing_pages.get_page(client_id, page["id"]) or page

    _log_activity(client_id, "landing_page_created",
                  {"page_id": page["id"], "slug": page["slug"], "title": title,
                   "goal": goal, "copy_generated": bool(copy_result.get("success")),
                   "research_grounded": copy_result.get("research_grounded", False)},
                  {"notes_for_johnny": copy_result.get("notes", "")})

    # Copy review happens IN CHAT (point 7) — never a separate review surface.
    if copy_result.get("success"):
        _chat(client_id,
              f'הכנתי עבורכם דף נחיתה חדש: "{page["title"]}" ✍️\n'
              f'הוא שמור כטיוטה — אפשר לראות אותו באזור "דפי נחיתה" בדשבורד. '
              f'תגידו לי כאן מה לשנות בטקסט, וכשזה מוצא חן בעיניכם נעלה אותו לאוויר.')
    return {"success": True, "page": page,
            "copy_generated": bool(copy_result.get("success")),
            "notes_for_johnny": copy_result.get("notes", "")}


def regenerate_copy(client_id: int, page_id: int, feedback: str = "") -> dict:
    """Rewrite one page's copy, optionally with the client's own chat feedback
    folded into the goal — which is how "make it warmer / lead with price"
    arrives, since review happens in conversation."""
    page = landing_pages.get_page(client_id, page_id)
    if not page:
        return {"success": False, "code": "ERR_LANDING_NOT_FOUND",
                "errors": ["page not found"]}
    goal = page.get("goal") or ""
    if feedback:
        goal = f"{goal}\n\nCLIENT FEEDBACK ON THE PREVIOUS VERSION: {feedback}".strip()

    result = generate_copy(client_id, goal, page.get("title", ""))
    if not result.get("success"):
        return result
    landing_pages.update_page(client_id, page_id, {"content": result["content"]})
    _log_activity(client_id, "landing_page_copy_regenerated",
                  {"page_id": page_id, "had_feedback": bool(feedback)})
    return {"success": True, "page": landing_pages.get_page(client_id, page_id)}


def publish_page(client_id: int, page_id: int) -> dict:
    """Draft → live. Explicit, and client-triggered: unlike an article on their
    WordPress site, a landing page is a standalone asset with their name on it,
    and nothing goes live without someone saying so."""
    result = landing_pages.update_page(client_id, page_id, {"status": "published"})
    if not result.get("success"):
        return result
    page = result["page"]
    url = page_url(client_id, page)
    _log_activity(client_id, "landing_page_published",
                  {"page_id": page_id, "slug": page["slug"], "url": url})

    state = landing_domains.get_state(client_id)
    if state["mode"] == "active":
        _chat(client_id, f'דף הנחיתה "{page["title"]}" עלה לאוויר 🚀\n{url}')
    else:
        _chat(client_id,
              f'דף הנחיתה "{page["title"]}" עלה לאוויר 🚀\n{url}\n'
              f'הכתובת הזמנית הזו עובדת מצוין בינתיים — וברגע שנחבר את הדומיין שלכם, '
              f'הדף יעבור לכתובת שלכם אוטומטית, בלי לשנות כלום.')
    return {"success": True, "page": page, "url": url}


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

def request_domain(client_id: int, hostname: str) -> dict:
    """Generate the client's DNS record and tell them, in chat, exactly what to
    forward and to whom. The message is written to be passed on verbatim."""
    result = landing_domains.request_domain(client_id, hostname)
    if not result.get("success"):
        return result

    dns = result["dns"]
    _log_activity(client_id, "landing_domain_requested",
                  {"hostname": result["hostname"],
                   "registered_with_cloudflare": result.get("registered_with_cloudflare"),
                   "register_error": result.get("register_error", "")})
    _chat(client_id,
          f'מעולה — נחבר את דפי הנחיתה לכתובת {result["hostname"]} 🌐\n\n'
          f'צריך דבר אחד: להעביר את ההודעה הבאה למי שמנהל לכם את הדומיין '
          f'(המעצב/מפתח/חברת האחסון). אין צורך להבין אותה — רק להעביר כמו שהיא:\n\n'
          f'━━━━━━━━━━━━━━\n{dns["forward_text"]}\n━━━━━━━━━━━━━━\n\n'
          f'ברגע שהם מאשרים שזה בוצע — כתבו לי כאן, ואני אבדוק ואעדכן אתכם. '
          f'עד אז הדפים ממשיכים לעבוד רגיל בכתובת הזמנית.')
    return result


def verify_domain(client_id: int) -> dict:
    """Check whether the client's DNS is live, and report the result in chat.
    Triggered by the dashboard's "בדקו עכשיו" button or by the client saying in
    chat that the record was added."""
    result = landing_domains.verify_domain(client_id)
    if not result.get("success"):
        return result

    _log_activity(client_id, "landing_domain_verified" if result["verified"]
                  else "landing_domain_verify_failed",
                  {"hostname": result.get("hostname", ""),
                   "reason": result.get("reason", "")})
    if result["verified"]:
        _chat(client_id,
              f'הדומיין שלכם מחובר! ✅ דפי הנחיתה עברו לכתובת '
              f'{result["hostname"]} — הקישורים בדשבורד כבר מעודכנים.')
    elif not result.get("uallak_side_ready", True):
        # Our fault, and the client must not be sent to chase their DNS provider
        _chat(client_id,
              'בדקתי — הרשומה שלכם בסדר גמור, ההשלמה בצד שלנו עוד רצה. '
              'אין מה לעשות מצדכם, אעדכן אתכם כאן ברגע שזה מוכן 🙏')
        agent_alert(AGENT_NAME, [
            f"client {client_id}: landing domain {result.get('hostname')} is waiting on "
            f"UALLAK-side setup (CLOUDFLARE_API_TOKEN / CLOUDFLARE_ZONE_ID / the Host "
            f"rewrite Worker). The client has been told it is on us, not on them."])
    else:
        _chat(client_id,
              'בדקתי ועדיין לא רואה את החיבור. שינויי DNS יכולים לקחת כמה שעות, '
              'אז נבדוק שוב מאוחר יותר — ואם עבר יותר מיום, שווה לוודא מול מי '
              'שמנהל את הדומיין שהרשומה נוספה בדיוק כפי שנשלחה.')
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
    counts = landing_pages.lead_counts_by_page(client_id)
    state = landing_domains.get_state(client_id)  # read once, reused per page
    pages = []
    for page in landing_pages.list_pages(client_id):
        pages.append({
            "id": page["id"], "slug": page["slug"], "title": page.get("title", ""),
            "goal": page.get("goal", ""), "status": page.get("status", "draft"),
            "url": page_url(client_id, page, business_name, state),
            "leads": counts.get(page["slug"], 0),
            "created_at": page.get("created_at", ""),
        })
    used = len(pages)
    return {
        "pages": pages,
        "used": used,
        "included": landing_pages.MAX_PAGES_PER_CLIENT,
        "can_create": used < landing_pages.MAX_PAGES_PER_CLIENT,
        "domain": state,
    }
