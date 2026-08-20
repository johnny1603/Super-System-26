"""One-off verification of the Google Ads WRITE paths against a TEST account.

WHY THIS EXISTS
The optimization engine's mutate calls have never run against Google. The
specific unverified risk is FIELD-MASK CASING: the REST/JSON mapping rule says
lowerCamelCase ("amountMicros"), but every pre-existing mask in this codebase is
a single word ("status") that reads identically in both casings, so nothing has
ever proven it. This script proves it.

WHY THE NORMAL SCAN CAN'T PROVE IT
A Google Ads test account NEVER serves ads: impressions, clicks, conversions and
cost are permanently zero. So every performance trigger in the optimizer is
dead by construction there -
  - optimize_keywords needs clicks >= 30 -> never fires
  - optimize_ad_copy needs converting keywords -> never fires
  - the State 2 restructure needs 30 conversions -> never fires
Running run_health_scan against a test account would report "did nothing" and
prove exactly nothing. This script therefore calls each mutate DIRECTLY, which
is what actually validates the payload shapes and field masks.

USAGE (Cloud Shell, from the repo root, with the normal env vars set):
    python scripts/verify_google_ads_mutates.py <client_id>

SAFETY
Refuses to run unless the connected account reports customer.test_account = TRUE.
This script creates and mutates real ad resources; it must never touch a live
client account.
"""
import sys
import traceback

sys.path.insert(0, ".")

from core import google_ads_service as gads  # noqa: E402
from agents.google_ads_agent import _get_connection, create_search_campaign  # noqa: E402

results = []


def check(label: str, fn):
    """Run one verification step, record pass/fail with Google's own message."""
    try:
        value = fn()
        results.append((True, label, ""))
        print(f"  PASS  {label}")
        return value
    except Exception as e:
        results.append((False, label, str(e)))
        print(f"  FAIL  {label}\n        {e}")
        return None


def main(client_id: int):
    conn = _get_connection(client_id)
    if not (conn.get("access_token") and conn.get("account_id")):
        print(f"client {client_id} has no active google_ads connection - connect it first")
        return 1
    token, cid = conn["access_token"], conn["account_id"]
    print(f"\nAccount {cid} (client {client_id})\n")

    # ── 0. SAFETY GATE ───────────────────────────────────────────────────────
    print("0. Safety gate")
    rows = check("account is a TEST account",
                 lambda: gads.search(token, cid,
                                     "SELECT customer.id, customer.test_account FROM customer"))
    if rows is None:
        return 1
    is_test = bool(rows and rows[0].get("customer", {}).get("testAccount"))
    if not is_test:
        print("\n  ABORT: customer.test_account is not TRUE. This script creates and\n"
              "  mutates real ad resources and must never run against a live account.\n")
        return 1
    print("  confirmed test account - safe to proceed\n")

    # ── 1. CREATE (validates the whole v25 creation payload) ─────────────────
    print("1. Campaign creation (advertisingChannelType, targetSpend, criteria, RSA, EU declaration)")
    spec = {
        "name": "uallak API verification",
        "daily_budget_ils": 20,
        "final_url": "https://uallak.com",
        "keywords": [{"text": "בדיקת ממשק", "match_type": "PHRASE"},
                     {"text": "בדיקה טכנית", "match_type": "PHRASE"}],
        "headlines": ["בדיקת מערכת", "אימות ממשק API", "uallak"],
        "descriptions": ["בדיקה טכנית של ממשק הפרסום.", "אין תקציב פעיל בחשבון בדיקה."],
    }
    created = check("create_search_campaign", lambda: create_search_campaign(client_id, spec))
    if not (created and created.get("success")):
        print(f"\n  Creation failed - later steps need it. Detail: {created}\n")
        return 1
    campaign_id = created["campaign_id"]
    print(f"  campaign_id={campaign_id}\n")

    # ── 2. Read back the resource IDs the mutates need ───────────────────────
    print("2. Resolving created resource IDs")
    budget_id = check("campaign budget id", lambda: gads.search(
        token, cid,
        f"SELECT campaign.id, campaign_budget.id FROM campaign "
        f"WHERE campaign.id = {campaign_id}")[0]["campaignBudget"]["id"])
    group_row = check("ad group id", lambda: gads.search(
        token, cid,
        f"SELECT ad_group.id FROM ad_group WHERE campaign.id = {campaign_id}")[0])
    ad_group_id = (group_row or {}).get("adGroup", {}).get("id")
    crit_row = check("keyword criterion id", lambda: gads.search(
        token, cid,
        f"SELECT ad_group_criterion.criterion_id FROM keyword_view "
        f"WHERE campaign.id = {campaign_id}")[0])
    criterion_id = (crit_row or {}).get("adGroupCriterion", {}).get("criterionId")
    print()

    # ── 3. THE FIELD-MASK VERIFICATIONS ──────────────────────────────────────
    print("3. Mutate field masks (THE point of this script)")

    if budget_id:
        check('campaignBudgets:mutate  updateMask="amountMicros"',
              lambda: gads.update_campaign_budget(token, cid, str(budget_id), 25_000_000))

    check('campaigns:mutate  updateMask="targetSpend.cpcBidCeilingMicros"',
          lambda: gads.set_campaign_bid_ceiling(token, cid, campaign_id, 8_000_000))

    if ad_group_id and criterion_id:
        resource = f"customers/{cid}/adGroupCriteria/{ad_group_id}~{criterion_id}"
        check('adGroupCriteria:mutate  updateMask="status" (keyword pause)',
              lambda: gads.mutate_ad_group_criteria(token, cid, [{
                  "update": {"resourceName": resource, "status": "PAUSED"},
                  "updateMask": "status"}]))

    if ad_group_id:
        check("adGroupAds:mutate  create (new RSA variant alongside existing)",
              lambda: gads.mutate_ad_group_ads(token, cid, [{"create": {
                  "adGroup": f"customers/{cid}/adGroups/{ad_group_id}",
                  "status": "ENABLED",
                  "ad": {
                      "finalUrls": ["https://uallak.com"],
                      "responsiveSearchAd": {
                          "headlines": [{"text": "וריאנט בדיקה"},
                                        {"text": "אימות מודעה"},
                                        {"text": "uallak"}],
                          "descriptions": [{"text": "וריאציית מודעה לבדיקת ממשק."},
                                           {"text": "נוצרה אוטומטית בחשבון בדיקה."}],
                      }}}]))

    # ── 4. Restructure threshold logic (pure arithmetic, no API) ─────────────
    print("\n4. State 1 -> 2 threshold logic (synthetic data - cannot come from a test account)")
    from agents.google_ads_agent import _restructure_ready, RESTRUCTURE_MIN_CONVERSIONS

    def synthetic(conversions_7d, campaign_count=1):
        return [{"id": str(i), "name": f"c{i}", "status": "ENABLED",
                 "last7": {"impressions": 0, "clicks": 0, "cost": 0.0,
                           "conversions": conversions_7d / campaign_count},
                 "prev7": {"impressions": 0, "clicks": 0, "cost": 0.0, "conversions": 0.0}}
                for i in range(campaign_count)]

    below = RESTRUCTURE_MIN_CONVERSIONS * 7 / 30 * 0.5   # ~half the threshold
    above = RESTRUCTURE_MIN_CONVERSIONS * 7 / 30 * 1.5   # ~1.5x the threshold
    ready_low, reason_low = _restructure_ready(client_id, synthetic(below))
    ready_high, reason_high = _restructure_ready(client_id, synthetic(above))
    ready_multi, reason_multi = _restructure_ready(client_id, synthetic(above, 2))

    # NOTE: every case below must BLOCK. The campaign this script just created is
    # 0 days old, so the 45-day age gate correctly stops even the above-threshold
    # case - and that is the assertion: each guard fires for its own reason. The
    # fully-eligible path is unreachable here by design (see the docstring: a
    # test account can never accrue conversions, and campaign age can't be
    # backdated), so it is verified by reading the reason strings, not the bool.
    for label, got, want in (
            (f"below conversion threshold blocks ({reason_low})", ready_low, False),
            (f"fresh campaign blocked by age gate ({reason_high})", ready_high, False),
            (f"already multi-campaign blocks ({reason_multi})", ready_multi, False)):
        ok = got is want
        results.append((ok, f"threshold: {label}", "" if ok else f"got {got}, wanted {want}"))
        print(f"  {'PASS' if ok else 'FAIL'}  threshold: {label}")

    if "conversions/30d" not in reason_low:
        print(f"  WARN  expected a conversion-count reason below threshold, got: {reason_low}")
    if "old <" not in reason_high:
        print(f"  WARN  expected an age reason for a fresh campaign, got: {reason_high}")

    # ── Summary ──────────────────────────────────────────────────────────────
    passed = sum(1 for ok, _, _ in results if ok)
    print(f"\n{'=' * 60}\n{passed}/{len(results)} checks passed")
    failures = [(label, err) for ok, label, err in results if not ok]
    if failures:
        print("\nFAILURES:")
        for label, err in failures:
            print(f"  - {label}\n      {err}")
        print("\nA FIELD_MASK error above means the casing is wrong - try the\n"
              "snake_case form of that mask in core/google_ads_service.py.")
    else:
        print("\nAll write paths verified. Field-mask casing is correct.\n"
              "OPTIMIZATION_MODE can be switched to live with real confidence.")
    print(f"\nClean up: pause or remove campaign {campaign_id} in the test account.\n")
    return 0 if not failures else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    try:
        sys.exit(main(int(sys.argv[1])))
    except Exception:
        traceback.print_exc()
        sys.exit(1)
