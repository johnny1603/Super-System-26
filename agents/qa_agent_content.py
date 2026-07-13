import json
import re

from core.claude_json import ClaudeJSONError, safe_claude_json_call

SYSTEM = """You are the quality reviewer and content fixer for uallak, an Israeli marketing agency.
You receive a marketing proposal (which contains a "packages" list of 1-2 pricing tiers) and the
client's original answers. Evaluate the proposal against these criteria, then return a corrected
version that fixes anything that fails:

1. Professional and warm tone — not robotic, feels human and trustworthy
2. No unrealistic promises — no "results within a week", guaranteed ROI, vague superlatives,
   or SEO results promised before 6 months
3. self_help_tips are genuinely specific to this business type — not generic advice that could
   apply to any business
4. No contradictions between business_summary and any package's recommended_services — they must
   all tell the same story
5. risk_level is logical given the client's financial status, budget size, and situation in their answers
6. honest_note reflects genuine honesty — not sales talk, empty reassurance, or reworded marketing copy
7. Packages are genuinely distinct, neutral choices — not one package obviously positioned as
   the "real" option with others as filler/decoys
8. For EACH package, numbers are internally consistent:
   - that package's benefit_value must equal its monthly_management_total x 2
   - that package's setup_fee_total must match the sum of its setup_fee_breakdown, and must never be 0
   - that package's monthly_management_total must match the sum of its monthly_breakdown
9. If ANY package's recommended_services includes "google" or "meta" (paid advertising), honest_note
   must clearly state that the client's actual ad spend/budget is an ADDITIONAL monthly cost on top
   of monthly_management_total, and that it will be trackable in their dashboard
10. ORGANIC SEO MUST NOT BE SILENTLY DROPPED — check the client's answers for "organic_interest" and
    "organic_budget". If organic_interest is affirmative, the proposal must address organic SEO
    somewhere: either a package recommends a tier (SEOptimer/SEMrush/Ahrefs) when organic_budget
    meets the ~3,000 NIS/month threshold, OR honest_note explicitly explains their organic budget is
    below the recommended minimum and states what that minimum is. If neither is present, this is a
    failure — fix it by adding the missing tier recommendation or the missing honest_note disclosure
11. RECOMMENDED_SERVICES CONSISTENCY — recommended_services lists ONLY the package's ONGOING
    managed services (what the monthly fee is computed from: "google", "meta", "tiktok", organic
    SEO tier, automation). Check strict two-way agreement between monthly_breakdown's
    platform-management lines and recommended_services: a "ניהול טיקטוק"-style monthly line whose
    platform is missing from recommended_services (or vice versa) is a failure — fix whichever
    side is wrong per the package's actual story. IMPORTANT EXEMPTION: one-time setup deliverables
    included in every standard setup package (landing page, market research, audit, campaign
    launches, one TikTok video, initial articles) are covered by the setup floor and are NOT
    inconsistencies — NEVER add "tiktok"/content to recommended_services just because a TikTok
    video or articles appear in setup_fee_breakdown; that would wrongly imply an extra 350
    NIS/month management fee. If such standard deliverables are itemized as separate
    setup_fee_breakdown lines beyond the floor line, fold them back into the floor line instead
    (keeping setup_fee_total unchanged)
12. HONEST_NOTE VS SCARCITY_NOTE SEPARATION — honest_note must contain ONLY factual/operational
    disclosures (ad spend/SEO tool cost transparency, payment timeline, support model, organic SEO
    shortfall). The payment timeline legitimately includes the FACT that month 2 carries no
    management fee — that sentence BELONGS in honest_note and must stay there, but phrased
    neutrally ("בחודש השני לא נגבים דמי ניהול"), never with gift/benefit words (חינם, מתנה, הטבה,
    בונוס) or offer framing. What honest_note must NEVER contain is promotional/incentive language:
    gift framing of the free month, "1 of 20 businesses", limited-time framing, or any selling
    language — that belongs exclusively in scarcity_note. Fix by REPHRASING the timeline fact
    neutrally in place (do not delete it) and moving any genuinely promotional sentence to
    scarcity_note
13. GOALS ARE ESTIMATES, NOT COMMITMENTS — every numeric target in goals_90_days and kpis
    (financial, lead counts, follower counts, search rankings) must read as an estimate or range
    ("כ-40-55 לידים", "התקרבות לעמוד הראשון"), never as an exact guaranteed number or a promised
    ranking position. Rewrite any bare exact-number target into a reasonable range around it
14. MARKET_REALITY SANITY — if the proposal has a market_reality field: its benchmark numbers must
    be plausible round ranges (not suspicious false precision like "87.3 ש"ח"), any budget-vs-goal
    math in it must be arithmetically sensible, its tone must be confident and professional (not
    apologetic, not hedged into vagueness), and goals_90_days/kpis/packages must not contradict it
    (e.g. market_reality says 300 leads is unrealistic while a goal still promises 300 leads).
    Keep the field in the returned proposal — never drop it

Do not change the proposal's own "approved" field (business eligibility) — that is unrelated to
this quality review. Only touch it if it is clearly inconsistent with the rest of the proposal.

Return JSON only, with this exact shape:
{
  "review_approved": true or false,
  "issues": ["specific issue description", ...],
  "proposal": { ...the full proposal object including its "packages" list, corrected if needed,
                same fields as the input... }
}

Set review_approved=false if ANY criterion failed in the ORIGINAL proposal (before your fixes).
issues must be empty when review_approved=true. Always return a complete, corrected "proposal"
object regardless of review_approved — the corrected proposal is what actually gets sent to the client."""


# ─── Deterministic invariants ─────────────────────────────────────────────────
# Criteria 11 and 12 kept recurring in production because the LLM reviewer
# flagging a problem doesn't guarantee its returned proposal actually fixed it
# (and on a ClaudeJSONError the review used to silently no-op). The parts of
# those criteria that need no judgment are enforced here in code, on every
# proposal that ships - after a successful LLM review AND on the failure path.

_PLATFORM_KEYWORDS = {
    "meta": ["מטא", "פייסבוק", "אינסטגרם", "meta", "facebook", "instagram"],
    "google": ["גוגל", "google"],
    "tiktok": ["טיקטוק", "טיק טוק", "tiktok"],
}

# Words the build prompt bans from honest_note outright - the neutral month-2
# timeline sentence is phrased "לא נגבים דמי ניהול", so none of these can appear
# in a compliant honest_note.
_PROMO_SENTENCE_MARKERS = ["מתוך 20", "20 עסקים", "הטבה", "מתנה", "בונוס", "מהרו"]


def _contains_any(text: str, keywords: list) -> bool:
    text = str(text).lower()
    return any(k in text for k in keywords)


def _enforce_invariants(proposal: dict) -> dict:
    fixes = []

    # Criterion 12 (mechanical part): neutralize gift wording of the month-2 fact
    # in place (never delete the mandated timeline sentence), drop sentences that
    # are outright scarcity/incentive copy (scarcity_note already carries the offer).
    note = proposal.get("honest_note") or ""
    if note:
        if "חינם" in note:
            note = note.replace("חינם", "ללא חיוב")
            fixes.append("honest_note: 'חינם' -> 'ללא חיוב'")
        sentences = re.split(r"(?<=[.!?])\s+", note)
        kept = [s for s in sentences if not _contains_any(s, _PROMO_SENTENCE_MARKERS)]
        if len(kept) != len(sentences):
            fixes.append(f"honest_note: dropped {len(sentences) - len(kept)} promotional sentence(s)")
        proposal["honest_note"] = " ".join(kept).strip()

    # Criterion 11 (mechanical part): monthly_breakdown platform lines and
    # recommended_services must agree. Adding the missing service slug is safe
    # (the monthly fee is already priced in); the reverse direction can't be
    # auto-fixed without inventing a price line, so it's only surfaced loudly.
    for pkg in proposal.get("packages", []):
        services = pkg.get("recommended_services") or []
        monthly = pkg.get("monthly_breakdown") or {}
        for platform, keywords in _PLATFORM_KEYWORDS.items():
            in_monthly = any(_contains_any(line, keywords) for line in monthly)
            in_services = any(_contains_any(s, keywords) for s in services)
            if in_monthly and not in_services:
                services.append(platform)
                fixes.append(f"[{pkg.get('id', '?')}] recommended_services: added '{platform}' to match monthly_breakdown")
            elif in_services and not in_monthly:
                print(f"QA/Review WARNING [{pkg.get('id', '?')}]: '{platform}' is in recommended_services "
                      f"but has no monthly_breakdown line - possible mispricing, not auto-fixable")
        pkg["recommended_services"] = services

    if fixes:
        print(f"QA/Review deterministic fixes applied: {fixes}")
    return proposal


def review_and_fix_proposal(proposal: dict, answers: dict) -> dict:
    user_message = (
        f"Proposal:\n{json.dumps(proposal, ensure_ascii=False, indent=2)}\n\n"
        f"Client answers:\n{json.dumps(answers, ensure_ascii=False, indent=2)}"
    )

    try:
        result = safe_claude_json_call(SYSTEM, user_message, max_tokens=7000)
        print(f"QA/Review: approved={result.get('review_approved')} issues={result.get('issues', [])}")
        return {
            "proposal": _enforce_invariants(result.get("proposal") or proposal),
            "review_approved": result.get("review_approved", True),
            "issues": result.get("issues", []),
        }
    except ClaudeJSONError as e:
        # The LLM review failing must not block the client's proposal, but it also
        # must not be silent (review_approved=True here used to mask every skipped
        # review): ship the proposal with the deterministic checks applied, and
        # report the failure so api_server raises a master-agent alert.
        print(f"QA/Review: LLM review FAILED ({e}) - shipping with deterministic checks only")
        return {
            "proposal": _enforce_invariants(proposal),
            "review_approved": False,
            "issues": [f"content review LLM call failed ({e}) - proposal shipped with deterministic checks only"],
        }
