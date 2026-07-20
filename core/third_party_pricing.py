"""Reference pricing for third-party vendors uallak's business model depends
on or discloses to clients — this is NOT our own pricing (see PRICING in
agents/onboarding_agent.py for what uallak actually charges). Every entry
here is a real number checked against the vendor's own public pricing page,
dated and sourced so it can be re-verified by hand — and re-checked
automatically twice a month by agents/price_monitor_agent.py, which alerts
Johnny when a live page looks like it no longer matches what's stored here.

SINGLE SOURCE OF TRUTH: nothing else in the codebase should hardcode one of
these numbers a second time. agents/budget_agent.py and
core/admin_service.get_pricing_reference() both import from here.

These are LIST/reference prices, not necessarily what any given client
actually pays (currency, discounts, grandfathered plans can differ) — see
the relevant skill (avatar, media, website, budget) for how each is used.
"""

THIRD_PARTY_PRICING = {
    "higgsfield": {
        "vendor": "Higgsfield",
        "what_for": ("Client-paid image/video generation (media_agent) — the client's own "
                     "account and card; we never bill for this."),
        "pricing_url": "https://higgsfield.ai/pricing",
        "checked_at": "2026-07-21",
        "currency": "USD",
        "plans": {"starter": 15, "plus": 39, "ultra": 99},
    },
    "elevenlabs": {
        "vendor": "ElevenLabs",
        "what_for": ("Client-paid voice cloning (avatar_agent) — the client's own account; we "
                     "never bill for this. avatar_agent uses Instant Voice Cloning, available "
                     "from the Starter plan up — NOT the pricier 'Professional Voice Cloning' "
                     "feature, which needs Creator or above."),
        "pricing_url": "https://elevenlabs.io/pricing",
        "checked_at": "2026-07-21",
        "currency": "USD",
        "plans": {"free": 0, "starter": 6, "creator": 22, "pro": 99, "scale": 299, "business": 990},
    },
    "heygen": {
        "vendor": "HeyGen",
        "what_for": ("Client-paid avatar creation/video generation (avatar_agent) — pay-as-you-go "
                     "API wallet, no required subscription for one avatar (web-UI twin creation "
                     "works on every HeyGen plan including Free)."),
        "pricing_url": "https://www.heygen.com/api-pricing",
        "checked_at": "2026-07-20",
        "currency": "USD",
        "generation_usd_per_min_range": [1.0, 4.0],
    },
    "seoptimer": {
        "vendor": "SEOptimer",
        "what_for": "Client-paid organic SEO tool, Level A tier (onboarding_agent PRICING['seo_tiers']).",
        "pricing_url": "https://www.seoptimer.com/pricing/",
        "checked_at": "2026-07-20",
        "currency": "USD",
        "plans": {"diy_seo": 29},
    },
    "semrush": {
        "vendor": "SEMrush",
        "what_for": "Client-paid organic SEO tool, Level B tier (onboarding_agent PRICING['seo_tiers']).",
        "pricing_url": "https://www.semrush.com/pricing/",
        "checked_at": "2026-07-20",
        "currency": "USD",
        "plans": {"pro": 139.95},
    },
    "ahrefs": {
        "vendor": "Ahrefs",
        "what_for": "Client-paid organic SEO tool, Level C tier (onboarding_agent PRICING['seo_tiers']).",
        "pricing_url": "https://ahrefs.com/pricing",
        "checked_at": "2026-07-20",
        "currency": "USD",
        "plans": {"lite": 129},
    },
    "instawp": {
        "vendor": "InstaWP",
        "what_for": ("OUR internal hosting cost for Phase-2 provisioned client sites — the basis "
                     "for PRICING['website']['new_site_hosting']['cost_monthly_ils']. The one "
                     "entry here that's OUR cost, not the client's."),
        "pricing_url": "https://instawp.com/pricing/",
        "checked_at": "2026-07-21",
        "currency": "USD",
        "plans": {"starter": 5, "plus": 9, "pro": 15, "turbo": 25, "elite": 45},
    },
}


def entry_price(vendor: str, plan: str = None):
    """The single reference number most callers want: a named plan's monthly
    price, or (with no plan given) the first/entry plan on file. Returns
    None for pay-as-you-go vendors with no flat plan (HeyGen) — use
    generation_usd_per_min_range for those instead."""
    plans = (THIRD_PARTY_PRICING.get(vendor) or {}).get("plans") or {}
    if plan:
        return plans.get(plan)
    return next(iter(plans.values()), None)
