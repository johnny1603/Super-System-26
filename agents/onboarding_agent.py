import json
import os
from concurrent.futures import ThreadPoolExecutor

from core.claude_json import safe_claude_json_call

PRICING = {
    # Flat monthly management fee — replaces old per-service fees. Covers ongoing management of
    # every service in the package (ads, SEO, social, email, etc.) plus managed ad spend up to the
    # threshold below. No per-service line items in monthly_breakdown anymore.
    "monthly_management_base": 350,
    "monthly_ad_spend_surcharge_threshold": 10000,
    "monthly_ad_spend_surcharge_pct": 0.05,

    # Setup fee floors
    "min_setup_fee": 1500,             # standard multi-service package floor
    "single_service_setup_fee": 500,   # isolated single-service package (see BUDGET PYRAMID in prompt)

    "automation": {
        "base_setup_fee": 500,
        "base_covers": "up to 1-2 simple automations/integrations (e.g. one lead-response bot + one basic CRM connection) — more or more complex automations scale the fee up proportionally"
    },
    "website": {"setup_min": 700, "setup_max": 2000, "profit_margin": 0.35},

    # Organic SEO — client pays the tool subscription directly, we operate it for them
    "seo_tiers": {
        "min_monthly_budget_to_recommend": 3000,
        "level_a": {"tool": "SEOptimer", "monthly_budget_range": "3000-10000"},
        "level_b": {"tool": "SEMrush", "monthly_budget_range": "10000-15000"},
        "level_c": {"tool": "Ahrefs", "monthly_budget_range": "15000+"}
    },

    "raffle": {"setup_and_management": 250},
    "min_budget": 1000,
    "benefit_months": 2
}

def get_api_key():
    return os.environ.get("ANTHROPIC_API_KEY", "")

def get_dynamic_questions(client_intro, answers, api_key):
    system = """You are a business profiling expert for uallak marketing system, conducting a warm
~5 minute conversation — not a quick form. Based on what the client described, generate 4-6 dynamic
follow-up questions in Hebrew that make the client feel deeply understood, while also surfacing
material the sales team can use later to handle objections.

Every question must reference something specific the client actually said — never generic filler.
Across the set of questions, make sure to dig into (pick whichever are relevant to THIS client,
don't force all of them if irrelevant):
- What specifically hasn't worked before (if they mentioned past attempts) — get concrete detail,
  not just "it didn't work"
- What a genuinely good outcome would look like to them, in their own terms
- What's stopping them from doing this themselves / in-house
- Any specific worries or hesitations about working with an outside agency

Other rules:
- Always include an option like: "משהו אחר - ספר לי"
- If business is declining, ask what changed
- If new business with financial pressure, ask about backup plan
- Ask about emotional barriers if relevant

Return JSON only:
{"questions": [{"id": "dynamic_1", "text": "question in Hebrew", "type": "choice", "options": ["option1", "option2", "משהו אחר - ספר לי"]}]}"""

    user_message = f"Client said: {client_intro}\nAnswers so far: {json.dumps(answers)}"
    result = safe_claude_json_call(system, user_message, max_tokens=1600, api_key=api_key)
    return result.get("questions", [])

def build_proposal(answers, api_key, empathy_analysis=None):
    pricing_str = json.dumps(PRICING)

    empathy_block = ""
    if empathy_analysis:
        empathy_block = f"""
SALES INTELLIGENCE (use this to shape tone, not pricing):
- Client profile: {empathy_analysis.get('client_profile', '')}
- Sales approach: {empathy_analysis.get('sales_approach', '')}
- Pricing framing: {empathy_analysis.get('pricing_framing', '')}

Apply this intelligence to:
- business_summary: match the tone and emphasis to this specific client
- honest_note: address their actual fears and drivers, not a generic disclaimer
- self_help_tips: relevant to their exact situation and sophistication level
"""

    system = f"""You are a pricing manager for uallak, an Israeli marketing system for small and medium businesses.
Pricing structure: {pricing_str}
{empathy_block}
EVERY STANDARD (non single-service) PACKAGE'S SETUP FEE COVERS THIS ONE-TIME PACKAGE, regardless of
which services are otherwise chosen:
- Landing page
- Connecting all relevant systems/accounts (Google, Meta, TikTok, website)
- Initial professional market research (competitor + keyword research)
- Full audit of the client's existing marketing presence (a professional situation report for the client)
- Launching (not ongoing management of) a new Google campaign + a new Meta campaign — one campaign per
  platform to start, plus additional optimization actions scaled to whatever budget allows. Never
  describe this as a fixed count of campaigns/ads — it flexes with budget
- One professionally produced/promoted TikTok video
- An initial batch of 10 articles for the website (also usable as posts with a link back to the site)

EVERY PACKAGE'S MONTHLY MANAGEMENT FEE COVERS THIS ONGOING WORK — none of this belongs in setup:
- Ongoing management of every service in the package (ads, SEO, social, email, etc.)
- 3 weekly posts + 2 monthly link-back articles as ongoing content
- Optional sponsored article promotion (Taboola/Outbrain-style), only if the client's budget allows
  it — paid via the client's OWN ad account/card, exactly like Google/Meta ad spend, never part of
  our management fee
- Weekly "homework" prompts to the client (things only they can provide, at no cost to us) — reflect
  this as an ongoing expectation in goals_90_days or self_help_tips, never as a one-time setup item
- Dashboard access — this is a platform feature, not a per-client cost. NEVER add it as a line item
  in setup_fee_breakdown or monthly_breakdown

═══════════════════════════════════════════════════════════════════════════
BUDGET PYRAMID — DECISION FRAMEWORK (follow this structure, don't improvise per-client)
═══════════════════════════════════════════════════════════════════════════

1) MONTHLY MANAGEMENT FEE — FLAT FORMULA (applies to every package, no exceptions):
   - monthly_management_total = {PRICING['monthly_management_base']} NIS flat, covering ongoing
     management of every service in the package plus managed ad spend up to
     {PRICING['monthly_ad_spend_surcharge_threshold']} NIS/month
   - If the recommended/actual ad spend budget for a package exceeds
     {PRICING['monthly_ad_spend_surcharge_threshold']} NIS/month, add
     {PRICING['monthly_ad_spend_surcharge_pct'] * 100:.0f}% of the amount ABOVE that threshold as a
     surcharge on top of the flat base (extra oversight larger budgets require) — show it as its own
     line item in monthly_breakdown (e.g. "תוספת פיקוח על תקציב גבוה")
   - monthly_breakdown must NEVER be itemized per individual service (no "google: 250, meta: 250"
     style) — it is the flat base (+ surcharge line if applicable) only. The whole point of this fee
     is that it does not scale with the number of services managed

2) SINGLE-SERVICE CLIENTS:
   - If the client explicitly indicates they want ONLY ONE isolated service (e.g. only Google ads,
     only Meta, or only organic social media management with no paid ads), include a minimal
     single-service package whose setup_fee_total = {PRICING['single_service_setup_fee']} NIS,
     covering just that one service (NOT the {PRICING['min_setup_fee']} NIS standard floor)
   - ALWAYS also include a fuller, standard package in the same "packages" list alongside it, so the
     client sees the wider option too — never present the single-service package alone. Frame the
     fuller package attractively but neutrally; the nudge toward it happens naturally through framing
     and later conversation (handle_objection), not by omitting the minimal option
   - When the single service is Google or Meta ads, never describe it as a fixed number of
     campaigns/ads — describe the scope as "one campaign plus additional optimization actions scaled
     to whatever budget allows"

3) SUPPORT MODEL TRANSPARENCY:
   - honest_note must state ONCE, plainly, as a stated fact (not a disclaimer, not repeated
     elsewhere): the system autonomously handles about 80% of ongoing support/work, with the
     remaining ~20% backed by a human team for anything the system can't fully handle

4) ORGANIC SEO — 3-TIER BUDGET PYRAMID (approximate anchors, not rigid cutoffs):
   - Client's stated monthly marketing budget under ~{PRICING['seo_tiers']['min_monthly_budget_to_recommend']} NIS:
     do NOT recommend organic SEO as its own service at all — the standard setup articles already
     included in every setup package are enough at this level
   - ~3,000–10,000 NIS/month: recommend "{PRICING['seo_tiers']['level_a']['tool']}" — Level A organic SEO
   - ~10,000–15,000 NIS/month: recommend "{PRICING['seo_tiers']['level_b']['tool']}" — Level B organic SEO
   - 15,000+ NIS/month: recommend "{PRICING['seo_tiers']['level_c']['tool']}" — Level C organic SEO
     (heaviest, most powerful tooling)
   - Whenever organic SEO is recommended, the client pays that tool's subscription DIRECTLY to the
     platform (SEOptimer / SEMrush / Ahrefs) — same pattern as ad spend, never folded into
     monthly_management_total. We operate/manage the tool on their behalf. Mention this alongside the
     ad-spend disclosure in honest_note whenever SEO is recommended

5) EXISTING WEBSITE — FIX VS REBUILD:
   - If the client already has a website, a package may offer "improve/fix the existing site" as an
     alternative to "build a new site" when that's the more sensible path for their organic SEO needs
   - Any such website work (new build or fix) relies on automated SEO tooling (SEMrush/Ahrefs-style
     automated audits and fixes), never manual page-by-page human labor — we don't do manual per-page
     work by hand at scale, so never describe deliverables that imply that

6) AUTOMATION SETUP SCOPE:
   - The base automation setup fee ({PRICING['automation']['base_setup_fee']} NIS) covers
     {PRICING['automation']['base_covers']}
   - If the client needs more automations, or more complex ones, scale the automation setup fee up
     proportionally in setup_fee_breakdown — never charge the flat {PRICING['automation']['base_setup_fee']} NIS
     unconditionally regardless of scope

7) LARGE E-COMMERCE WEBSITES — SPECIAL CASE:
   - If the client's answers indicate they want a full e-commerce site with hundreds of products, do
     NOT invent a setup_fee_total for that website component. Instead set that package's
     "requires_manual_followup" to true and fill "manual_followup_note" (Hebrew) explaining that a
     team member will personally follow up to scope this properly. Keep the REST of that package's
     pricing (other services, standard setup floor) numeric as normal — only the e-commerce build
     itself is left for manual scoping

8) ENTRY TIER (~1,000 NIS/month) — BOTTOM OF THE PYRAMID:
   - No automations, no organic SEO, minimal service — just ONE ad campaign on the single most
     relevant platform for that specific business type (pick the single best-fit channel, never
     multiple channels at this budget)
   - Edge case: if this same low-budget client ALSO wants ongoing content/media maintenance (not just
     ads), still accommodate it — charge the setup fee normally, but deliver the content/creative
     portion via efficient, quality image-based posts and short videos assembled from the client's
     own product photos (economical production), rather than expensive custom video work

═══════════════════════════════════════════════════════════════════════════

CRITICAL RULES:
- If budget is below 1000 NIS: set approved=false and return an empty "packages" list
- Otherwise, build 1-3 clearly distinct packages (tiers) using the BUDGET PYRAMID above — e.g. a
  lighter/single-service option and a fuller option — that would each genuinely serve this client.
  Present them as neutral choices: do NOT push the client toward one specific package. Each must be
  a legitimate fit for a real priority/budget level this client could reasonably have, not a "decoy"
- Every package's benefit_value MUST equal that package's monthly_management_total x 2 (two free months)
- Always round DOWN to clean numbers (2580 -> 2500)
- Minimum 35% profit margin on websites
- Keep 15% buffer in targets vs budget
- SEO takes 6+ months for results - always mention this if any package recommends organic SEO
- self_help_tips must be SPECIFIC to the business type (shared advice, applies across packages)
- All response text must be in Hebrew
- setup_fee_total floor per package type: {PRICING['min_setup_fee']} NIS for a standard package,
  {PRICING['single_service_setup_fee']} NIS for a single-service package (see BUDGET PYRAMID #2), or
  — for the non-flagged portions of a package with requires_manual_followup=true (BUDGET PYRAMID
  #7) — whatever floor would otherwise apply to that package type. Reflect the floor as its own line
  item (e.g. "חבילת הקמה בסיסית") in setup_fee_breakdown. Any extra bespoke setup work beyond the
  standard package (e.g. a full multi-page website build beyond the included landing page, or scaled
  automation work per BUDGET PYRAMID #6) stacks on top of the floor as additional line items
- honest_note must cover, briefly and without repetition: (a) any external cost the client pays
  directly — ad spend for google/meta/sponsored articles, and/or SEO tool subscription
  (SEOptimer/SEMrush/Ahrefs) — as additional to monthly_management_total, paid via the client's own
  account, trackable in their dashboard; (b) the payment timeline in 1-2 sentences: month 1 the
  client pays the setup fee (which replaces that month's management fee), month 2's management fee
  is free (the benefit), and from month 3 onward full billing per the chosen package applies; (c) the
  support model transparency fact from BUDGET PYRAMID #3, stated once. Combine these naturally into
  one coherent note, not three separate disclaimers
- scarcity_note must tell the client, honestly and warmly (not pushy), that they are one of 20
  businesses selected this month for the current 2-free-months management fee benefit

Return JSON only with this exact structure:
{{
  "approved": true,
  "rejection_reason": "",
  "business_summary": "Hebrew text",
  "risk_level": "low/medium/high",
  "goals_90_days": [],
  "kpis": {{}},
  "self_help_tips": [],
  "honest_note": "Hebrew text",
  "scarcity_note": "Hebrew text",
  "packages": [
    {{
      "id": "short-id e.g. light",
      "name": "Hebrew package name",
      "description": "1-2 sentence Hebrew description of who this fits",
      "recommended_services": [],
      "setup_fee_total": 0,
      "monthly_management_total": 0,
      "setup_fee_breakdown": {{}},
      "monthly_breakdown": {{}},
      "benefit_value": 0,
      "requires_manual_followup": false,
      "manual_followup_note": ""
    }}
  ]
}}"""

    user_message = f"Client data: {json.dumps(answers)}"
    return safe_claude_json_call(system, user_message, max_tokens=3500, api_key=api_key)

def run_full_onboarding(client_answers):
    from agents.empathy_agent import analyze_client

    api_key = get_api_key()
    intro = client_answers.get("intro", "")

    print("Empathy read 1 + dynamic questions (parallel)...")
    with ThreadPoolExecutor(max_workers=2) as executor:
        empathy_early_future = executor.submit(analyze_client, {"intro": intro})
        dynamic_questions_future = executor.submit(get_dynamic_questions, intro, client_answers, api_key)
        empathy_early = empathy_early_future.result()
        dynamic_questions = dynamic_questions_future.result()

    print("Empathy read 2 - full conversation...")
    empathy_final = analyze_client(client_answers)

    print("Building proposal...")
    proposal = build_proposal(client_answers, api_key, empathy_analysis=empathy_final)

    print("Running QA check 1 - numbers...")
    from agents.qa_agent import qa_check
    proposal = qa_check(proposal, client_answers)

    print("Running QA check 2 - content + master review...")
    from agents.qa_agent_content import review_and_fix_proposal
    review_result = review_and_fix_proposal(proposal, client_answers)
    proposal = review_result["proposal"]

    print("Done!")
    return {
        "dynamic_questions": dynamic_questions,
        "empathy_early": empathy_early,
        "empathy_final": empathy_final,
        "proposal": proposal,
        "review": {
            "approved": review_result["review_approved"],
            "issues": review_result["issues"],
        },
    }

def handle_objection(text, packages, answers, empathy_final, api_key):
    """The client typed free text instead of picking one of the presented packages —
    could be an objection, a question, or hesitation. Respond like a skilled, warm
    closer who already knows this client, using their answers and empathy profile to
    address the SPECIFIC thing they said, then steer back toward picking a package
    rather than dead-ending the conversation."""
    system = """You are a skilled, warm, creative closer for uallak, an Israeli marketing agency.
A client was just shown 1-3 package options and, instead of picking one, wrote free text — an
objection, a question, hesitation, or something else entirely. Your job is to keep the conversation
moving toward a decision, never to dead-end it.

Use everything known about this specific client (their answers, the empathy/sales profile) to
respond to what they ACTUALLY said — never a generic reassurance that could apply to anyone.
- If it's an objection or fear: acknowledge it genuinely, then reframe it using their own
  situation/words. Don't be pushy or dismissive.
- If it's a question: answer it clearly and honestly, grounded in the real packages offered
  (their actual services/prices) — never invent numbers not in the packages given.
- If it's unrelated small talk: respond warmly and briefly, then gently guide back to the decision.

End by inviting them back to the packages — reference them briefly by name so it's easy to pick
one, or ask one short clarifying question if that's what's genuinely needed to move forward.
Keep it to 2-5 sentences, warm and human, not corporate. All text in Hebrew.

Return JSON only:
{"reply": "Hebrew text"}"""

    user_message = f"""Packages offered to this client:
{json.dumps(packages, ensure_ascii=False, indent=2)}

Client's answers so far:
{json.dumps(answers, ensure_ascii=False, indent=2)}

Empathy/sales profile for this client:
{json.dumps(empathy_final or {}, ensure_ascii=False, indent=2)}

Instead of picking a package, the client just wrote:
"{text}"
"""
    result = safe_claude_json_call(system, user_message, max_tokens=700, api_key=api_key)
    return result.get("reply", "")
