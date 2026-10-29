"""Google Places API (New) — HTTP only, with an API KEY (no user consent, no
OAuth scope: no effect on the Google OAuth verification). Design and the
terms it follows: docs/PROSPECTING.md §2-§4.

THE TERMS, as code:
- Nothing returned here is stored by this module or by its callers, except
  `id` (place_id), which Google allows to be stored indefinitely (Places API
  Policies, read 2026-10-22). Names, addresses, coordinates, websites, phones
  are used for the request at hand and dropped.
- Wherever Places data is shown without a Google Map, the screen shows the
  Google attribution (ATTRIBUTION) plus any third-party attribution returned.

THE COST, as code:
- A request is billed at the HIGHEST tier of any field in its field mask, so
  every request names its fields and `sku_for` prices it from the verified
  table below (Place Data Fields (New), read 2026-10-22) and the prices in
  core/third_party_pricing (THIRD_PARTY_PRICING["google_places"]).
- Before the request: the daily brakes and the client's monthly cost budget
  (core/gmail_agent_plan). After a 200: the cost is recorded against the
  client at list price, tagged with the package feature through the caller's
  cost_action.
"""
import math

import httpx

BASE = "https://places.googleapis.com/v1"
TIMEOUT = 20
MAX_RADIUS_M = 50_000           # Places' own maximum for a circle
MAX_RESULTS = 20                # Places' own maximum per Nearby / Text Search page

ATTRIBUTION = {"provider": "Google Maps", "required": "Show the Google logo / 'Google Maps' wherever "
                                                      "this data is shown without a Google Map"}

TIERS = ("essentials_ids", "essentials", "pro", "enterprise", "enterprise_atmosphere")

# field -> tier, per endpoint (VERIFIED, Place Data Fields (New), 2026-10-22)
FIELD_TIER = {
    "search": {"id": "essentials_ids", "displayName": "pro", "formattedAddress": "pro", "location": "pro",
               "types": "pro", "primaryType": "pro", "businessStatus": "pro", "googleMapsUri": "pro",
               "nationalPhoneNumber": "enterprise", "internationalPhoneNumber": "enterprise",
               "websiteUri": "enterprise", "rating": "enterprise", "userRatingCount": "enterprise",
               "regularOpeningHours": "enterprise", "priceLevel": "enterprise",
               "editorialSummary": "enterprise_atmosphere", "reviews": "enterprise_atmosphere"},
    "details": {"id": "essentials_ids", "displayName": "pro", "formattedAddress": "essentials",
                "location": "essentials", "types": "essentials", "primaryType": "pro",
                "businessStatus": "pro", "googleMapsUri": "pro",
                "nationalPhoneNumber": "enterprise", "internationalPhoneNumber": "enterprise",
                "websiteUri": "enterprise", "rating": "enterprise", "userRatingCount": "enterprise",
                "regularOpeningHours": "enterprise", "priceLevel": "enterprise",
                "editorialSummary": "enterprise_atmosphere", "reviews": "enterprise_atmosphere"},
}
# Nearby Search bills even a bare id at Pro (no IDs-only SKU)
NEARBY_MIN_TIER = "pro"

SKU = {("text_search", "essentials_ids"): "text_search_ids_only",
       ("text_search", "essentials"): "text_search_pro", ("text_search", "pro"): "text_search_pro",
       ("text_search", "enterprise"): "text_search_enterprise",
       ("nearby_search", "essentials_ids"): "nearby_search_pro", ("nearby_search", "essentials"): "nearby_search_pro",
       ("nearby_search", "pro"): "nearby_search_pro", ("nearby_search", "enterprise"): "nearby_search_enterprise",
       ("place_details", "essentials_ids"): "place_details_essentials",
       ("place_details", "essentials"): "place_details_essentials",
       ("place_details", "pro"): "place_details_pro", ("place_details", "enterprise"): "place_details_enterprise"}

# Discovery needs only Pro fields; Enterprise (website, phone…) only for one
# shortlisted business at a time
DISCOVERY_FIELDS = ("id", "displayName", "primaryType", "types", "formattedAddress", "location",
                    "businessStatus")


class PlacesError(Exception):
    pass


def _key() -> str:
    from agents.keys_agent import get_key
    return get_key("GOOGLE_PLACES_API_KEY")


def sku_for(endpoint: str, fields) -> str:
    """The SKU a request is billed at: the highest tier among its fields.
    Atmosphere fields are refused (not used, and not priced in this module)."""
    table = FIELD_TIER["details" if endpoint == "place_details" else "search"]
    tiers = []
    for field in fields:
        tier = table.get(field.split(".")[-1])
        if tier is None:
            raise PlacesError(f"field not in the verified tier table: {field}")
        if tier == "enterprise_atmosphere":
            raise PlacesError(f"Atmosphere fields are not used: {field}")
        tiers.append(tier)
    top = max(tiers, key=TIERS.index) if tiers else "essentials_ids"
    if endpoint == "nearby_search" and TIERS.index(top) < TIERS.index(NEARBY_MIN_TIER):
        top = NEARBY_MIN_TIER
    return SKU[(endpoint, top)]


def request_cost(sku: str) -> tuple:
    """(nis, usd) for ONE request at list price (first paid tier)."""
    from core.cost_tracker import usd_to_ils
    from core.third_party_pricing import THIRD_PARTY_PRICING
    usd = THIRD_PARTY_PRICING["google_places"]["skus"][sku]["usd_per_1000"] / 1000
    return usd_to_ils(usd), round(usd, 6)


def _post(path: str, body: dict, field_mask: str):
    return httpx.post(f"{BASE}/{path}", json=body, timeout=TIMEOUT,
                      headers={"X-Goog-Api-Key": _key(), "X-Goog-FieldMask": field_mask,
                               "Content-Type": "application/json"})


def _get(path: str, field_mask: str):
    return httpx.get(f"{BASE}/{path}", timeout=TIMEOUT,
                     headers={"X-Goog-Api-Key": _key(), "X-Goog-FieldMask": field_mask})


def _paid(client_id: int, endpoint: str, fields, send, admin_view: bool = False) -> dict:
    """Brakes + budget BEFORE, the request, cost AFTER a 200. `send(mask)`
    performs the HTTP call."""
    from core import gmail_agent_plan as plan
    from core.cost_tracker import record_cost
    sku = sku_for(endpoint, fields)
    nis, usd = request_cost(sku)
    plan.take_places_request(client_id, admin_view=admin_view)
    if not admin_view:
        plan.require_budget(client_id, nis)
    mask = ",".join(fields)
    response = send(mask)
    if response.status_code != 200:
        raise PlacesError(f"Places {endpoint} HTTP {response.status_code}: {response.text[:300]}")
    record_cost("places_api", nis, client_id,
                {"sku": sku, "endpoint": endpoint,
                 "feature": plan.ADMIN_FEATURE if admin_view else plan.FEATURE}, usd=usd)
    return response.json()


def search_text(client_id: int, query: str, center: dict = None, radius_m: int = None,
                fields=DISCOVERY_FIELDS, language: str = "he") -> list:
    body = {"textQuery": query[:200], "languageCode": language, "regionCode": "IL",
            "pageSize": MAX_RESULTS}
    if center and radius_m:
        body["locationBias"] = {"circle": {"center": center, "radius": float(min(radius_m, MAX_RADIUS_M))}}
    data = _paid(client_id, "text_search", [f"places.{f}" for f in fields],
                 lambda mask: _post("places:searchText", body, mask))
    return data.get("places") or []


def search_nearby(client_id: int, center: dict, radius_m: int, included_types: list,
                  fields=DISCOVERY_FIELDS, language: str = "he") -> list:
    body = {"includedTypes": list(included_types)[:50], "maxResultCount": MAX_RESULTS,
            "languageCode": language, "regionCode": "IL",
            "locationRestriction": {"circle": {"center": center,
                                               "radius": float(min(radius_m, MAX_RADIUS_M))}}}
    data = _paid(client_id, "nearby_search", [f"places.{f}" for f in fields],
                 lambda mask: _post("places:searchNearby", body, mask))
    return data.get("places") or []


def place_details(client_id: int, place_id: str, fields, admin_view: bool = False,
                  language: str = "he") -> dict:
    if not place_id or "/" in place_id:
        raise PlacesError("bad place_id")
    return _paid(client_id, "place_details", list(fields),
                 lambda mask: _get(f"places/{place_id}?languageCode={language}", mask),
                 admin_view=admin_view)


def display_name(place: dict) -> str:
    return ((place or {}).get("displayName") or {}).get("text", "")


def distance_km(a: dict, b: dict) -> float:
    """Great-circle distance between two {latitude, longitude} — computed in
    memory for this request, never stored."""
    if not a or not b:
        return float("nan")
    lat1, lon1, lat2, lon2 = map(math.radians, (a["latitude"], a["longitude"], b["latitude"], b["longitude"]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return round(6371 * 2 * math.asin(math.sqrt(h)), 2)
