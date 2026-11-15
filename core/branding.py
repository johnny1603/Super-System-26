"""Branding — ליאור shows a client how the business would look under its best
branding (2026-11-15, docs/BRANDING.md).

What this module owns:
- the ATTEMPTS: one model call designs a brand concept (palette, font pair,
  logo mark, a few lines of copy); core/brand_mockups draws the mockups from
  it in code. FREE_ATTEMPTS (5) per client are free.
- the per-attempt COST CAP (`ATTEMPT_COST_CAP_ILS`): exactly ONE model call
  per attempt, on the strongest model whose WORST case fits under the cap.
  Every attempt's measured cost is a client_costs row (category media,
  action `branding_attempt`) and is kept on the attempt itself.
- the APPROVAL: approving a direction saves the client's BRAND KIT (palette,
  fonts, style, logo mark) and closes the attempts — no more attempts after
  that, real production is quoted.
- the BRAND KIT's one reader, `kit()` / `kit_for_prompt()`: landing pages,
  generated media and posts read it there and nowhere else.
- the production QUOTE: staff enter what each item costs us; the client's
  price is cost × QUOTE_MULTIPLIER (about half cost, half margin), shown on
  a card the client approves BEFORE anything is produced.

What it deliberately does NOT do:
- charge for extra attempts. `EXTRA_ATTEMPT_CHARGE_ENABLED` is False: there
  is no per-attempt charge mechanism in the system (PayPal here is a monthly
  subscription plus e-mailed invoices), and adding one is the owner's
  decision. Until then a client past the free attempts is told so plainly and
  staff can grant more (`grant_attempts`).
- generate any image. Mockups are SVG drawn in code.
- build, redesign or touch a website.

Storage: two app_settings rows per client — `branding:<id>` (attempts,
approval, quote) and `brand_kit:<id>` (the kit). No migration. Both prefixes
are cleared by client_deletion and covered by deletion_guard.
"""
import math
import re
from datetime import datetime, timezone

from core import brand_mockups

SERVICE_NAME = "branding"
STATE_PREFIX = "branding:"
KIT_PREFIX = "brand_kit:"

FREE_ATTEMPTS = 5
KEPT_ATTEMPTS = 12                 # concepts kept per client (older ones drop their concept)
ATTEMPT_COST_CAP_ILS = 1.5         # one attempt may never cost us more than this
ATTEMPT_MAX_TOKENS = 1800
# OWNER'S DECISION 2026-11-16: stays OFF until another payment system is in. Past the free
# attempts the team is alerted and the owner opens more by hand (Control Room → the client → מיתוג).
EXTRA_ATTEMPT_CHARGE_ENABLED = False
EXTRA_ATTEMPT_MARGIN = 0.25        # cost + a small margin
EXTRA_ATTEMPT_MIN_ILS = 5          # symbolic floor
QUOTE_MULTIPLIER = 2.0             # price = cost × 2 → about 50% cost / 50% margin
QUOTE_ITEMS = ("logo", "business_card", "signage", "flyer", "poster")   # never "website": its own service
STYLES = ("minimal", "warm", "bold", "elegant", "playful", "natural", "modern", "classic")
MAX_DIRECTION_CHARS = 400
LANGS = ("he", "en", "fr", "ar", "ru")


def _five(he, en, fr, ar, ru):
    return {"he": he, "en": en, "fr": fr, "ar": ar, "ru": ru}


# The ONE line the client reads before starting. Fixed text: it is a promise.
TERMS = _five(
    "לפני שמתחילים: {free} ניסיונות הדמיה הם בחינם. אחריהם, כל ניסיון נוסף עולה סכום סמלי שתראו ותאשרו לפני שהוא רץ. אחרי שאישרתם כיוון ועברנו לייצור, אין ניסיונות נוספים.",
    "Before we start: {free} mockup attempts are free. After that, each extra attempt costs a small symbolic amount you see and approve before it runs. Once you approve a direction and we move to production, there are no more attempts.",
    "Avant de commencer : {free} essais de maquettes sont gratuits. Ensuite, chaque essai supplémentaire coûte une somme symbolique que vous voyez et validez avant qu'il ne démarre. Une fois une direction validée et la production lancée, il n'y a plus d'essais.",
    "قبل أن نبدأ: {free} محاولات للنماذج مجانية. بعدها، كل محاولة إضافية بمبلغ رمزي ترونه وتوافقون عليه قبل تنفيذها. بعد اعتماد اتجاه والانتقال إلى الإنتاج لا توجد محاولات إضافية.",
    "Перед началом: {free} попыток макетов бесплатны. После этого каждая дополнительная попытка стоит символическую сумму, которую вы видите и одобряете заранее. После утверждения направления и перехода к производству новых попыток нет.")
# What it is worth — and not one word more than that.
VALUE = _five(
    "בחוץ, תהליך כזה של לראות ולהחליט לפני שמתחייבים עולה אלפי שקלים. כאן הוא בחינם עד לנקודת האישור.",
    "Outside, a see-it-before-you-decide process like this costs thousands of shekels. Here it is free up to the approval point.",
    "Ailleurs, ce genre de processus pour voir avant de décider coûte des milliers de shekels. Ici, il est gratuit jusqu'au moment de la validation.",
    "في الخارج، عملية كهذه لرؤية النتيجة قبل القرار تكلّف آلاف الشواكل. هنا هي مجانية حتى نقطة الموافقة.",
    "На стороне такой процесс «увидеть до решения» стоит тысячи шекелей. Здесь он бесплатен до момента утверждения.")


def text(table: dict, lang: str) -> str:
    return table.get(lang) or table.get("en") or table.get("he") or ""


def terms_line(lang: str) -> str:
    return text(TERMS, lang).replace("{free}", str(FREE_ATTEMPTS))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db():
    from agents.client_agent import _db as db
    return db()


def _alert(lines: list):
    from core.agent_base import agent_alert
    agent_alert(SERVICE_NAME, lines)


def _read(key: str) -> dict:
    rows = _db().table("app_settings").select("value").eq("key", key).limit(1).execute().data or []
    value = rows[0].get("value") if rows else None
    return value if isinstance(value, dict) else {}


def _write(key: str, value: dict):
    _db().table("app_settings").upsert({"key": key, "value": value, "updated_at": _now()},
                                       on_conflict="key").execute()


# ─── State ───────────────────────────────────────────────────────────────────

def state(client_id: int) -> dict:
    """The stored branding record. Raises when storage is unreadable — an
    attempt must never run (or be counted as free) on a guess."""
    value = _read(f"{STATE_PREFIX}{int(client_id)}")
    value.setdefault("attempts", [])
    value.setdefault("granted", 0)
    return value


def _save(client_id: int, value: dict):
    attempts = value.get("attempts") or []
    for old in attempts[:-KEPT_ATTEMPTS]:
        old.pop("concept", None)
    _write(f"{STATE_PREFIX}{int(client_id)}", value)
    _block_cache.pop(int(client_id), None)


def attempts_used(value: dict) -> int:
    return len(value.get("attempts") or [])


def attempts_left(value: dict) -> int:
    """Attempts that can still run WITHOUT a charge: the free ones plus any
    staff granted."""
    return max(0, FREE_ATTEMPTS + int(value.get("granted") or 0) - attempts_used(value))


def extra_attempt_price_ils(value: dict) -> int:
    """What one extra attempt would cost the client: our measured cost per
    attempt plus a small margin, never under the symbolic floor."""
    costs = [float(a.get("cost_ils") or 0) for a in value.get("attempts") or [] if a.get("cost_ils")]
    average = sum(costs) / len(costs) if costs else ATTEMPT_COST_CAP_ILS
    return max(EXTRA_ATTEMPT_MIN_ILS, int(math.ceil(average * (1 + EXTRA_ATTEMPT_MARGIN))))


def can_attempt(value: dict) -> str:
    """"" when another attempt may run now, else the refusal code."""
    if value.get("approved"):
        return "already_approved"
    if attempts_left(value) > 0:
        return ""
    # Past the free attempts. A charge is the only way on — and it is not
    # switched on (EXTRA_ATTEMPT_CHARGE_ENABLED), so nothing runs.
    return "attempts_used"


def summary(client_id: int) -> dict:
    """What the agents and the dashboard read. Never raises: unreadable
    storage is `available: False`, which hides the feature for this turn."""
    try:
        value = state(client_id)
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: state unreadable ({type(e).__name__})")
        return {"available": False}
    attempts = value.get("attempts") or []
    approved = value.get("approved") or {}
    quote = value.get("quote") or {}
    return {
        "available": True,
        "free_attempts": FREE_ATTEMPTS,
        "attempts_used": len(attempts),
        "attempts_left_free": attempts_left(value),
        "approved": bool(approved),
        "approved_attempt": approved.get("n"),
        "can_try_now": not can_attempt(value),
        "extra_attempts_open": EXTRA_ATTEMPT_CHARGE_ENABLED,
        "latest_attempt": attempts[-1]["n"] if attempts else None,
        "latest_direction": (attempts[-1].get("direction") or "") if attempts else "",
        "human_designer_requested": bool(value.get("designer_requested")),
        "quote_status": quote.get("status") or "",
        "has_brand_kit": bool(approved),
    }


_block_cache = {}   # client_id -> (at, summary) — a chat turn must not wait on this read
BLOCK_CACHE_SECONDS = 60


def prompt_block(client_id: int, lang: str = "he") -> dict:
    """`branding` in every agent's payload: where the client stands, and the
    two FIXED lines the agent must say word for word before the first
    attempt. {} when unreadable (the agent then simply has no branding data)."""
    import time
    hit = _block_cache.get(int(client_id))
    if hit and hit[0] > time.time() - BLOCK_CACHE_SECONDS:
        data = hit[1]
    else:
        data = summary(client_id)
        if data.get("available"):
            _block_cache[int(client_id)] = (time.time(), data)
    if not data.get("available"):
        return {}
    out = {k: v for k, v in data.items() if k != "available"}
    if not data.get("approved"):
        out["terms_line"] = terms_line(lang)
        out["value_line"] = text(VALUE, lang)
    return out


# ─── The concept ─────────────────────────────────────────────────────────────

CONCEPT_SYSTEM = f"""You are a senior brand designer. Design ONE brand direction for a small Israeli business.
You do NOT draw: your JSON is rendered by code into mockups (logo, business card, shop sign, flyer, poster and
a website mockup), so every choice must come from the lists below.

Design it for THIS business — its field, its customers and the direction the owner reacted to. A direction
that would fit any business is a failure. If earlier attempts are listed, this one must look clearly
different from each of them (different palette family, different mark, different font feeling), unless the
owner asked for a small change to one of them.

Rules:
- palette: four #RRGGBB colours. "ink" = the dark anchor colour, "paper" = the light background, "accent" =
  the brand colour, "secondary" = a supporting colour. ink on paper must be clearly readable.
- fonts.display and fonts.text: one key each from {list(brand_mockups.FONTS)}.
- logo.mark: one of {list(brand_mockups.MARKS)}. logo.layout: one of {list(brand_mockups.LAYOUTS)}.
- style: one of {list(STYLES)}.
- All copy in the language named in "language". Never invent facts: no prices, no years in business, no
  awards, no phone numbers, no addresses, no discounts. Never use another brand's name, slogan or look.
- Hard limits: tagline ≤ 40 characters; flyer_headline ≤ 40; flyer_body ≤ 140; poster_headline ≤ 28;
  cta ≤ 22; site_headline ≤ 40; site_sub ≤ 80; exactly 3 services of ≤ 22 characters each, taken from what
  the business really offers; rationale ≤ 220 characters, one or two plain sentences TO THE OWNER on why
  this direction fits their business.

Return JSON only:
{{"tagline": "", "style": "", "palette": {{"ink": "#", "paper": "#", "accent": "#", "secondary": "#"}},
"fonts": {{"display": "", "text": ""}}, "logo": {{"mark": "", "layout": ""}},
"copy": {{"flyer_headline": "", "flyer_body": "", "poster_headline": "", "cta": "", "site_headline": "",
"site_sub": "", "services": ["", "", ""]}}, "rationale": ""}}"""


def _cut(value, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def clean_concept(raw: dict, name: str, contact: dict = None) -> dict:
    """Whatever the model returned, forced into what the mockups can draw:
    every colour a real hex, every key from the lists, every text inside its
    limit, the NAME and contact details from our own record — never the
    model's. Raises ValueError when there is no usable palette at all."""
    raw = raw if isinstance(raw, dict) else {}
    palette = raw.get("palette") if isinstance(raw.get("palette"), dict) else {}
    colors = {k: str(palette.get(k) or "").strip() for k in ("ink", "paper", "accent", "secondary")}
    if not all(brand_mockups.is_hex(colors[k]) for k in ("ink", "paper", "accent")):
        raise ValueError("no usable palette")
    if not brand_mockups.is_hex(colors["secondary"]):
        colors["secondary"] = colors["accent"]
    colors = {k: v.upper() for k, v in colors.items()}
    if brand_mockups.luminance(colors["ink"]) > brand_mockups.luminance(colors["paper"]):
        colors["ink"], colors["paper"] = colors["paper"], colors["ink"]
    if brand_mockups.contrast(colors["ink"], colors["paper"]) < 4.5:
        colors["ink"] = "#16161A"
        if brand_mockups.contrast(colors["ink"], colors["paper"]) < 4.5:
            colors["paper"] = "#FAF8F4"
    fonts = raw.get("fonts") if isinstance(raw.get("fonts"), dict) else {}
    logo = raw.get("logo") if isinstance(raw.get("logo"), dict) else {}
    copy = raw.get("copy") if isinstance(raw.get("copy"), dict) else {}
    services = [_cut(s, 22) for s in (copy.get("services") if isinstance(copy.get("services"), list) else [])]
    contact = contact or {}
    return {
        "name": _cut(name, 40),
        "tagline": _cut(raw.get("tagline"), 40),
        "style": raw.get("style") if raw.get("style") in STYLES else "modern",
        "palette": colors,
        "fonts": {"display": fonts.get("display") if fonts.get("display") in brand_mockups.FONTS else "heebo",
                  "text": fonts.get("text") if fonts.get("text") in brand_mockups.FONTS else "assistant"},
        "logo": {"mark": logo.get("mark") if logo.get("mark") in brand_mockups.MARKS else "circle",
                 "layout": logo.get("layout") if logo.get("layout") in brand_mockups.LAYOUTS else "stacked"},
        "copy": {"flyer_headline": _cut(copy.get("flyer_headline"), 40), "flyer_body": _cut(copy.get("flyer_body"), 140),
                 "poster_headline": _cut(copy.get("poster_headline"), 28), "cta": _cut(copy.get("cta"), 22),
                 "site_headline": _cut(copy.get("site_headline"), 40), "site_sub": _cut(copy.get("site_sub"), 80),
                 "services": [s for s in services if s][:3]},
        "contact": {k: _cut(contact.get(k), 60) for k in ("phone", "site", "address") if contact.get(k)},
        "rationale": _cut(raw.get("rationale"), 220),
    }


def _client_facts(client_id: int) -> tuple:
    """(display name, contact dict, business context) from OUR records."""
    from agents.client_agent import get_client
    client = get_client(client_id) or {}
    name = client.get("business_name") or client.get("name") or ""
    contact = {"phone": client.get("phone") or "", "address": client.get("address") or ""}
    context = {}
    try:
        from agents.seo_agent import _business_context
        context = _business_context(client_id)
        context["sales_chat_answers"] = {k: v for k, v in (context.get("sales_chat_answers") or {}).items()
                                         if "budget" not in str(k).lower()}
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: business context unreadable ({type(e).__name__})")
    try:
        from agents.seo_agent import _client_domain
        contact["site"] = _client_domain(client_id)
    except Exception:
        pass
    return name, contact, context


def pick_model(prompt_chars: int) -> str:
    """The strongest model whose WORST case for one attempt fits under the
    cap ("" when none does) — the cap is enforced before the call, not
    discovered after it."""
    from core.cost_tracker import claude_cost_ils
    from core.model_routing import TIER_MODELS
    estimate_in = int(prompt_chars / 2) + 200          # Hebrew runs about 2 characters a token
    for tier in ("heavy", "standard", "simple"):
        model = TIER_MODELS[tier]
        if claude_cost_ils(estimate_in, ATTEMPT_MAX_TOKENS, model) <= ATTEMPT_COST_CAP_ILS:
            return model
    return ""


class BrandingError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def create_attempt(client_id: int, direction: str, lang: str = "he") -> dict:
    """ONE attempt: refuse when none is allowed, design the concept with one
    capped model call, store it. Returns {"n", "concept", "left"}. A failed
    call stores nothing and uses no attempt."""
    import json
    from core import claude_json
    from core.cost_tracker import cost_action
    value = state(client_id)
    refusal = can_attempt(value)
    if refusal:
        raise BrandingError(refusal)
    name, contact, context = _client_facts(client_id)
    earlier = [{"attempt": a.get("n"), "direction": a.get("direction", ""),
                "palette": (a.get("concept") or {}).get("palette"), "mark": ((a.get("concept") or {}).get("logo") or {}).get("mark"),
                "fonts": (a.get("concept") or {}).get("fonts"), "style": (a.get("concept") or {}).get("style")}
               for a in (value.get("attempts") or [])[-FREE_ATTEMPTS:] if a.get("concept")]
    user = json.dumps({"language": lang if lang in LANGS else "he", "business_name": name, "business": context,
                       "direction_the_owner_reacted_to": _cut(direction, MAX_DIRECTION_CHARS),
                       "earlier_attempts": earlier}, ensure_ascii=False)
    model = pick_model(len(CONCEPT_SYSTEM) + len(user))
    if not model:
        raise BrandingError("cost_cap", "one attempt would pass the per-attempt cost cap")
    n = attempts_used(value) + 1
    with cost_action("branding_attempt", f"branding-{int(client_id)}-{n}"), claude_json.collect_usage() as used:
        try:
            # ONE call: no empty retry, no repair retry — the cap is one call
            raw = claude_json.safe_claude_json_call(CONCEPT_SYSTEM, user, max_tokens=ATTEMPT_MAX_TOKENS, model=model,
                                                    client_id=client_id, cost_category="media", retry_empty=False,
                                                    json_retry=False)
            concept = clean_concept(raw, name, contact)
        except BrandingError:
            raise
        except Exception as e:
            raise BrandingError("design_failed", f"{type(e).__name__}: {e}")
    cost = round(sum(float(u.get("ils") or 0) for u in used), 4)
    if cost > ATTEMPT_COST_CAP_ILS:
        _alert([f"client {client_id}: branding attempt {n} cost {cost} ILS, over the "
                                   f"{ATTEMPT_COST_CAP_ILS} ILS cap — check core/branding.pick_model"])
    value = state(client_id)                          # re-read: a second tab may have added one
    refusal = can_attempt(value)
    if refusal:
        raise BrandingError(refusal)
    n = attempts_used(value) + 1
    value["attempts"].append({"n": n, "at": _now(), "direction": _cut(direction, MAX_DIRECTION_CHARS),
                              "concept": concept, "cost_ils": cost, "model": model, "language": lang})
    _save(client_id, value)
    return {"n": n, "concept": concept, "left": attempts_left(value), "cost_ils": cost}


def attempt(client_id: int, n: int) -> dict:
    for item in state(client_id).get("attempts") or []:
        if int(item.get("n") or 0) == int(n) and item.get("concept"):
            return item
    return {}


def view_path(n: int) -> str:
    return f"/api/client/branding/view/{int(n)}"


def page(client_id: int, n: int, lang: str = "he") -> str:
    """The mockup page of one stored attempt ("" when there is none)."""
    item = attempt(client_id, n)
    return brand_mockups.page_html(item["concept"], lang, int(n)) if item else ""


def grant_attempts(client_id: int, count: int, who: str = "admin") -> dict:
    """Staff add free attempts for one client (the manual answer while the
    extra-attempt charge is off)."""
    count = max(0, min(int(count), 10))
    value = state(client_id)
    value["granted"] = int(value.get("granted") or 0) + count
    value.setdefault("grants", []).append({"at": _now(), "count": count, "by": who})
    _save(client_id, value)
    return {"granted": value["granted"], "left": attempts_left(value)}


# ─── Approval → the brand kit ────────────────────────────────────────────────

def approve(client_id: int, n: int) -> dict:
    """The client approved attempt `n`: its palette, fonts, style and mark
    become the brand kit and the attempts close."""
    value = state(client_id)
    if value.get("approved"):
        raise BrandingError("already_approved")
    item = next((a for a in value.get("attempts") or [] if int(a.get("n") or 0) == int(n) and a.get("concept")), None)
    if not item:
        raise BrandingError("attempt_missing")
    concept = item["concept"]
    kit_value = {"palette": concept["palette"], "fonts": concept["fonts"], "style": concept["style"],
                 "logo": concept["logo"], "name": concept["name"], "tagline": concept["tagline"],
                 "approved_at": _now(), "attempt": int(n), "source": "branding"}
    _write(f"{KIT_PREFIX}{int(client_id)}", kit_value)
    value["approved"] = {"n": int(n), "at": kit_value["approved_at"]}
    _save(client_id, value)
    _kit_cache.pop(int(client_id), None)
    return kit_value


_kit_cache = {}   # client_id -> (at, kit)
KIT_CACHE_SECONDS = 120


def kit(client_id: int) -> dict:
    """The client's approved brand kit, or {} — never raises (a failed read
    means "no kit known", and the caller designs as it did before)."""
    import time
    hit = _kit_cache.get(int(client_id))
    if hit and hit[0] > time.time() - KIT_CACHE_SECONDS:
        return hit[1]
    try:
        value = _read(f"{KIT_PREFIX}{int(client_id)}")
    except Exception as e:
        print(f"[{SERVICE_NAME}] client {client_id}: brand kit unreadable ({type(e).__name__})")
        return {}
    value = value if value.get("palette") else {}
    _kit_cache[int(client_id)] = (time.time(), value)
    return value


def kit_for_prompt(client_id: int) -> dict:
    """The kit as a prompt block for anything that designs for this client
    (landing pages, generated media, posts): the approved colours, the font
    families by their real names, the style word. {} when there is no kit."""
    value = kit(client_id)
    if not value:
        return {}
    fonts = value.get("fonts") or {}
    return {"approved_by_client": True, "palette": value.get("palette"),
            "fonts": {"display": brand_mockups.FONTS.get(fonts.get("display"), ""),
                      "text": brand_mockups.FONTS.get(fonts.get("text"), "")},
            "style": value.get("style", ""), "tagline": value.get("tagline", "")}


def kit_palette_list(client_id: int) -> list:
    """The kit's colours as a plain list (accent first) — the shape
    media_agent's image prompts already take."""
    palette = (kit(client_id) or {}).get("palette") or {}
    return [palette[k] for k in ("accent", "secondary", "ink", "paper") if palette.get(k)]


# ─── A human designer for the logo ───────────────────────────────────────────

def request_designer(client_id: int) -> bool:
    """Marks (once) that the client wants the logo designed with our human
    designer, and tells the team. True when this call was the first."""
    value = state(client_id)
    first = not value.get("designer_requested")
    if first:
        value["designer_requested"] = _now()
        _save(client_id, value)
        _alert([f"client {client_id}: asked to design the logo with the human designer "
                                   "(branding). Contact them; the price is given by a person, before any work."])
    return first


# ─── The production quote ────────────────────────────────────────────────────

def build_quote(costs: dict, note: str = "") -> dict:
    """{item: our cost in ILS} → the quote the client approves. Price per
    item = cost × QUOTE_MULTIPLIER, rounded up to a whole shekel."""
    items = []
    for item in QUOTE_ITEMS:
        cost = costs.get(item)
        if cost in (None, ""):
            continue
        cost = float(cost)
        if cost <= 0 or cost > 100000:
            raise ValueError(f"bad cost for {item}")
        items.append({"item": item, "cost_ils": round(cost, 2), "price_ils": int(math.ceil(cost * QUOTE_MULTIPLIER))})
    if not items:
        raise ValueError("no items")
    return {"items": items, "total_ils": sum(i["price_ils"] for i in items),
            "cost_ils": round(sum(i["cost_ils"] for i in items), 2), "note": _cut(note, 300),
            "status": "sent", "at": _now()}


def store_quote(client_id: int, quote: dict):
    value = state(client_id)
    if not value.get("approved"):
        raise BrandingError("not_approved")
    value["quote"] = quote
    _save(client_id, value)


def quote(client_id: int) -> dict:
    return state(client_id).get("quote") or {}


def approve_quote(client_id: int) -> dict:
    value = state(client_id)
    current = value.get("quote") or {}
    if current.get("status") != "sent":
        raise BrandingError("no_open_quote")
    current["status"] = "approved"
    current["approved_at"] = _now()
    value["quote"] = current
    _save(client_id, value)
    _alert([f"client {client_id}: APPROVED the branding production quote — "
                               f"{current.get('total_ils')} ILS for "
                               f"{', '.join(i['item'] for i in current.get('items') or [])}. "
                               "Send the payment request and start production once it is paid."])
    return current


def quote_lines(current: dict, lang: str) -> str:
    """The quote as the card shows it: one line per item (client price only —
    never our cost), then the total."""
    names = brand_mockups.ITEM_TEXT
    lines = [f"{text(names[i['item']]['name'], lang)} — ₪{i['price_ils']}" for i in current.get("items") or []
             if i.get("item") in names]
    return "\n".join(lines)
