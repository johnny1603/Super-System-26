"""The COPY step of a paid campaign (2026-11-28, docs/PAID-MEDIA.md §3.2).

Headlines, long headlines and descriptions are TEXT ASSETS — copy, not media.
They are written here, against Google's character limits
(core/copy_budget.ads_budget, read from core/google_ads_formats), and checked
as written (copy_budget.ads_fit). The media agent never writes them and this
step never sees an image brief: `write` takes media_order.copy_brief(order),
which carries no media row at all. Image copy and ad copy coming out of one
step is how the carousel bug happened (social-craft §2.2).

One draft, one repair round that feeds the named violations back (the pattern
google_ads_agent.draft_campaign_spec uses), then CopyFailed — copy that does
not fit is never cut to fit and never handed on.
"""
import json

from core import copy_budget, media_order

SERVICE_NAME = "ad_copy"
MEDIA_FIELDS = ("required_assets", "palette", "concepts", "visual_direction", "assets")

SYSTEM = """You are the ad copywriter of uallak, an Israeli marketing agency. You write the TEXT ASSETS of one
Google Ads campaign: headlines, long headlines, descriptions. Google renders them next to images you never see
and in combinations you do not control, so every item must make sense alone and beside any other.

You are given the campaign (type, objective, audience, what is advertised, the landing page), the business, the
client's brand brief, and ad_copy_budget — Google's limits for this campaign type.

HARD RULES (breaking one is a rejected asset):
- Write in `language`. Hebrew must read as spoken Hebrew, never translated.
- Every item within its max_chars. COUNT the characters. Never end an item mid-word to fit.
- Give the number of items the budget's `write` says for each field.
- Where the budget has `short`, at least that many items must be that short.
- No two items the same, and no two that say the same thing in other words.
- business_name is the business's real name as given; shorten it only if it is over the limit.
- Never name a competitor. Never invent a price, a discount, a guarantee, an award or a "#1" claim — only facts
  the campaign or the brand brief state (Google policy and Israeli consumer-protection law both apply).
- Nothing the brand brief forbids. No emoji. No ALL CAPS. No exclamation mark in a headline.
- Do not describe an image, and do not write anything meant to be placed on an image.

Return JSON only: {"texts": {<field>: [strings] — a single string for a field whose max is 1}}"""


class CopyFailed(RuntimeError):
    """The copy could not be written within the limits: .lines says what was over."""

    def __init__(self, lines: list):
        self.lines = list(lines)
        super().__init__("ad copy does not fit: " + "; ".join(self.lines))


def targets(campaign_type: str) -> dict:
    """How many items to write per field: the ceiling where the table has one
    (coverage beats polish — craft §2.4), never fewer than the floor."""
    out = {}
    for field, spec in copy_budget.ads_budget(campaign_type)["fields"].items():
        if field == "path":
            continue                                   # a display path is built from the landing page, not written
        out[field] = spec["max"] or max(spec["min"], 5)
    return out


def _payload(brief: dict, business_name: str, context: dict, lang: str) -> dict:
    leaked = [k for k in MEDIA_FIELDS if k in brief]
    if leaked:
        raise ValueError(f"the copy step was handed media fields ({', '.join(leaked)}) — it takes copy_brief only")
    budget = copy_budget.ads_budget(brief["campaign_type"])
    write = targets(brief["campaign_type"])
    return {"language": lang, "campaign": {k: brief[k] for k in ("campaign_type", "objective", "audience_segment",
                                                                  "offer", "landing_page_url")},
            "business_name": business_name,
            "ad_copy_budget": {"rule": budget["rule"],
                               "fields": {f: {**spec, "write": write[f]} for f, spec in budget["fields"].items()
                                          if f in write}},
            "brand_brief": (context or {}).get("brand", ""),
            "client_governance": (context or {}).get("governance", "")}


def _clean(raw, campaign_type: str) -> dict:
    fields = copy_budget.ads_budget(campaign_type)["fields"]
    out = {}
    for field in fields:
        value = (raw or {}).get(field)
        if value is None:
            continue
        items = [" ".join(str(v).split()) for v in (value if isinstance(value, list) else [value])]
        out[field] = [i for i in items if i]
    return out


def write(client_id: int, brief: dict, business_name: str = "", context: dict = None, lang: str = "he",
          call=None) -> dict:
    """The text assets for one order: {"texts": {field: [str]}, "fit",
    "rounds"}. `brief` = media_order.copy_brief(order). `call` = the JSON
    model call (tests); the default is safe_claude_json_call, which also
    appends the client's never-say rule (core/brand_voice)."""
    if call is None:
        from core.claude_json import safe_claude_json_call

        def call(system, user):
            return safe_claude_json_call(system, user, max_tokens=1800, client_id=client_id,
                                         cost_category="claude_ads_copy")
    payload = _payload(brief, business_name, context, lang)
    texts, fit = {}, {"fits": False, "lines": []}
    for attempt in (1, 2):
        answer = call(SYSTEM, json.dumps(payload, ensure_ascii=False))
        texts = _clean((answer or {}).get("texts"), brief["campaign_type"])
        fit = copy_budget.ads_fit(brief["campaign_type"], texts, "en")
        if fit["fits"]:
            print(f"[{SERVICE_NAME}] client {client_id}: order {brief.get('id')} copy fits (round {attempt})")
            return {"texts": texts, "fit": fit, "rounds": attempt}
        payload = {**payload, "previous_texts": texts, "violations": fit["lines"],
                   "fix": "Rewrite ONLY what the violations name; keep every item that already fits."}
    print(f"[{SERVICE_NAME}] client {client_id}: order {brief.get('id')} copy does NOT fit: {fit['lines']}")
    raise CopyFailed(copy_budget.ads_fit(brief["campaign_type"], texts, lang)["lines"])
