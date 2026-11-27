"""ליאור's side of a paid-media order: the files (2026-11-28,
docs/PAID-MEDIA.md §3.3, knowledge/paid-media-craft.md).

`produce(client_id, brief)` takes media_order.media_brief(order) — an order
with no text slot in it — and returns (delivery manifest, {key: bytes}). It
decides HOW the creative looks and nothing else: the campaign type, the
ratios, the sizes and the caps all arrive in the order's rows.

Per concept (a distinct picture; craft §2.4 — a full set, not a hero image):

  1. ONE prompt, crafted from the offer, the objective, the brand brief and
     the segment's palette variant — never from ad copy (this module is not
     given any) and never with Hebrew or text in the scene (craft §2.2);
  2. the widest slot of the order is generated on the client's own tool;
  3. every other ratio is DERIVED from that picture by a centre crop when the
     crop is big enough and keeps the subject (measured: core/crop_survival
     on the picture, for that ratio) — the client's credits are not spent on
     a second generation that a crop already gives. Otherwise that ratio is
     generated in its own right;
  4. each file is brought to its slot (ratio, size), compressed to the row's
     cap (core/ad_compress — fails loudly) and run through the crop-survival
     check. A generated file that fails the check is regenerated, within
     RETRIES for the whole order.

Logos are the CLIENT'S logo file, fitted to each logo slot — never generated,
redrawn or "improved". No logo file = no logo row in the manifest, and the
handover check names it.

A VIDEO row (2026-11-28) is a YouTube URL, never a file (`video_asset`): the
URL the order already carries (a video on the client's channel), or one short
clip made on the client's tool, uploaded to THEIR channel as UNLISTED through
agents/youtube_content_agent.publish — the Social-tab gate included: a clip
the owner has not approved there is queued and the row says it is waiting.
The row records the privacy status YouTube ANSWERED, never the one asked for.

Uploaded banners are NOT produced here (docs/PAID-MEDIA.md §Not built): their
rows stay empty and the handover check names them.

Nothing here decides whether the set is complete. That is
media_order.check_delivery, from the files — the producer does not approve
its own work.
"""
import io
import json

from core import ad_compress, crop_survival, image_fit, media_order, render_manifest
from core import google_ads_formats as gaf

SERVICE_NAME = "paid_media_production"
RETRIES = 2                 # regenerations for failed crop checks, per ORDER (the client's credits)
JPEG_QUALITY = 92
LOGO_PADDING = 0.08         # clear space around a logo, share of the canvas
TEXT_KEYS = ("required_text", "texts", "copy", "headlines", "descriptions")

PROMPT_SYSTEM = """You are the media agent of uallak, an Israeli marketing agency. You own the EXECUTION of the
images of one Google Ads campaign. The paid-media agent decided the campaign; you decide how the creative looks.
You have the paid-media craft library and the client's brand brief in full — follow them.

Write ONE excellent English generation prompt for concept number `concept` of `concepts`.

IRON RULES:
- Google crops this picture from the CENTRE into a square and into a wide strip. ONE clear subject, exactly in the
  middle of the frame, occupying no more than the central third of the width and the central half of the height.
  Everything outside that is plain, uncluttered backdrop that can be cut away.
- ABSOLUTELY NO text, letters, words, numbers, signs with writing, logos, badges or captions in the scene. The
  message is carried by the ad's text assets, which you are not given and must not invent.
- Use ONLY the palette given (one audience variant of the brand). Never another colour scheme. The backdrop that
  fills the frame edge to edge is the palette's `bg` colour, stated in the prompt by its hex code: the finished file's
  background colour is MEASURED and compared to the palette, and a different one fails the order.
- Never depict a specific product as if it were a real item the customer can order, unless the brand brief says
  it is one. Atmosphere, studio and workspace scenes, hands at work, blank items, textures are fine.
- No celebrity likenesses, no trademarked or licensed characters, no brand logos.
- This concept must be a DIFFERENT picture from `earlier_prompts` — another scene or angle, not a variation.
- Professional photography language; one clear scene; max 110 words.

Return JSON only: {"generation_prompt": "English prompt", "rationale": "1 English sentence"}"""


class ProductionBlocked(RuntimeError):
    """Nothing can be produced at all: .code (missing_tool | context_missing | stopped)."""

    def __init__(self, code: str, detail: str = ""):
        self.code, self.detail = code, detail
        super().__init__(f"{code}: {detail}" if detail else code)


# ─── Defaults: the real tools (never reached by the tests) ───────────────────

def _default_generate(client_id: int):
    from agents import media_agent
    from core import media_gen_service as gen
    api_key = media_agent._generation_key(client_id)
    if not api_key:
        raise ProductionBlocked("missing_tool")

    def generate(prompt: str, aspect: str) -> bytes:
        try:
            out = gen.generate_image(api_key, prompt, aspect)       # the model's nearest ratio, cropped to `aspect`
        except Exception as e:
            media_agent._key_outcome(client_id, e)
            raise
        media_agent._key_outcome(client_id)
        return out["content"]
    return generate


def _default_craft(client_id: int):
    from core import post_brief
    from core.claude_json import safe_claude_json_call
    craft_text = post_brief._read(CRAFT_PATH()).strip()
    if not craft_text:
        raise ProductionBlocked("context_missing", "knowledge/paid-media-craft.md")
    context = post_brief.load_context(client_id)

    def craft(brief: dict, concept: int, earlier: list) -> str:
        payload = {"craft_library": craft_text, "brand_brief": context["brand"],
                   "client_governance": context.get("governance", ""),
                   "campaign": {k: brief[k] for k in ("campaign_type", "objective", "audience_segment", "offer")},
                   "palette": brief["palette"], "concept": concept, "concepts": brief["concepts"],
                   "earlier_prompts": earlier}
        got = safe_claude_json_call(PROMPT_SYSTEM, json.dumps(payload, ensure_ascii=False), max_tokens=500,
                                    client_id=client_id, cost_category="claude_media")
        return str(got.get("generation_prompt") or "")
    return craft


def CRAFT_PATH() -> str:
    import os
    from core import post_brief
    return os.path.join(post_brief.KNOWLEDGE_DIR, "paid-media-craft.md")


def _default_logos(client_id: int) -> dict:
    """{version: bytes} of the client's raster logo files (core/brand_page)."""
    from core import brand_package, drive_service
    out = {}
    for version, file_id in brand_package._logo_files(client_id).items():
        try:
            out[version] = drive_service.download_file(file_id)
        except Exception as e:
            print(f"[{SERVICE_NAME}] client {client_id}: logo {version} unreadable ({type(e).__name__})")
    return out


def _default_store(client_id: int):
    from agents import media_agent
    from core import drive_service, drive_storage

    @drive_storage.scoped
    def store(client_id: int, order_id: str, name: str, content: bytes, mime: str) -> dict:
        folder = media_agent._subfolder(client_id, "ads", order_id)
        uploaded = drive_service.upload_bytes(folder, name, content, mime)
        return {"id": uploaded.get("id"), "link": uploaded.get("webViewLink", "")}
    return lambda order_id, name, content, mime: store(client_id, order_id, name, content, mime)


# ─── The video row ───────────────────────────────────────────────────────────

def _default_make_video(client_id: int, brief: dict) -> dict:
    from agents import media_agent
    ask = (f"A short video ad. What is advertised: {brief['offer']}. Objective: {brief['objective']}. "
           f"Audience: {brief['audience_segment']}. No on-screen text.")
    out = media_agent.generate_video(client_id, ask, "youtube")
    if out.get("success") and out.get("file_id"):
        return {"file_id": out["file_id"], "link": out.get("link", "")}
    if media_agent._NO_ACCOUNT_ERROR in (out.get("errors") or []):
        raise ProductionBlocked("missing_tool")
    return {"error": "; ".join(str(e) for e in out.get("errors") or [])[:200] or "the video was not produced"}


def _default_upload_video(client_id: int, file_id: str, brief: dict) -> dict:
    """The clip → the client's channel, UNLISTED, through the one publisher
    (and its Social-tab gate). Not approved there yet = queued for the owner."""
    from agents import youtube_content_agent
    from core import social_queue
    out = youtube_content_agent.publish(client_id, {
        "drive_file_id": file_id, "title": brief["offer"][:90], "description": brief["landing_page_url"],
        "purpose": "ad"})
    if out.get("not_approved"):
        if not any(i.get("file_id") == file_id for i in social_queue.items(client_id)):
            social_queue.add_safely(client_id, file_id, "video", ["youtube"], producer="media", source="chat",
                                    title=brief["offer"][:120], lane="client")
        return {"waiting_approval": True}
    if not out.get("success") or not out.get("url"):
        return {"error": "; ".join(str(e) for e in out.get("errors") or [])[:200] or "no video id came back"}
    return {"url": out["url"], "privacy_status": out.get("privacy_status"),
            "privacy_requested": out.get("privacy_requested"), "api_status": out.get("api_status")}


def video_asset(client_id: int, brief: dict, make=None, upload=None) -> dict:
    """ONE manifest row for the order's video: always a YouTube URL when it
    succeeds. make(client_id, brief) -> {"file_id"} | {"error"};
    upload(client_id, file_id, brief) -> {"url", "privacy_status", ...} |
    {"waiting_approval": True} | {"error"}."""
    if brief.get("youtube_url"):
        return media_order.asset("video", 1, youtube_url=brief["youtube_url"], source="client")
    file_id = brief.get("video_file_id") or ""
    if not file_id:
        made = (make or _default_make_video)(client_id, brief)
        file_id = made.get("file_id") or ""
        if not file_id:
            return media_order.asset("video", 1, source="generated", upload_error=made.get("error") or "not produced")
    out = (upload or _default_upload_video)(client_id, file_id, brief)
    if out.get("waiting_approval"):
        return media_order.asset("video", 1, source="generated", file_id=file_id, waiting_approval=True)
    if not out.get("url"):
        return media_order.asset("video", 1, source="generated", file_id=file_id,
                                 upload_error=out.get("error") or "upload failed")
    print(f"[{SERVICE_NAME}] client {client_id}: order {brief['id']} video uploaded — asked "
          f"{out.get('privacy_requested')}, YouTube answered {out.get('privacy_status')}")
    return media_order.asset("video", 1, source="generated", file_id=file_id, youtube_url=out["url"],
                             privacy_status=out.get("privacy_status"), privacy_requested=out.get("privacy_requested"),
                             api_status=out.get("api_status"))


# ─── One file to its slot ────────────────────────────────────────────────────

def _open(content: bytes):
    from PIL import Image
    with Image.open(io.BytesIO(content)) as opened:
        opened.load()
        return opened.copy()


def _jpeg(img) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=JPEG_QUALITY, optimize=True)
    return buf.getvalue()


def to_slot(row: dict, content: bytes) -> bytes:
    """A picture at the row's exact ratio, no larger than its recommended
    size, as JPEG, within the row's cap (ad_compress — may raise CannotReachCap)."""
    from PIL import Image
    fitted = image_fit.fit_aspect(content, row["aspect"])["content"]
    img = _open(fitted)
    if img.size[0] > row["width"]:
        img = img.resize((row["width"], row["height"]), Image.LANCZOS)
    out = ad_compress.to_cap(_jpeg(img), row["max_bytes"], tuple(row["file_types"]), row["slot"],
                             floor=(row["min_width"], row["min_height"]))
    if out["changed"]:
        print(ad_compress.line(row["slot"], out))
    return out["content"]


def derivable(master: bytes, master_slot: str, row: dict) -> bool:
    """Can `row`'s ratio be cut from the master? Big enough after the centre
    crop, and the subject (measured on the master) still inside it."""
    w, h = gaf.measure(master)
    if not w:
        return False
    box = crop_survival.centre_box(w, h, row["aspect"])
    if box[2] - box[0] < row["min_width"] or box[3] - box[1] < row["min_height"]:
        return False
    return crop_survival.check(master, master_slot, targets=(row["aspect"],))["ok"]


def fit_logo(row: dict, content: bytes) -> bytes:
    """The client's logo, whole and unaltered, centred on the slot's canvas
    with clear space. Transparent stays transparent (PNG); an opaque logo's
    canvas continues the logo's own corner colour — never a colour of ours."""
    from PIL import Image
    logo = _open(content).convert("RGBA")
    W, H = row["width"], row["height"]
    transparent = logo.getchannel("A").getextrema()[0] < 255
    canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0) if transparent else logo.getpixel((0, 0)))
    room = (round(W * (1 - 2 * LOGO_PADDING)), round(H * (1 - 2 * LOGO_PADDING)))
    scale = min(room[0] / logo.size[0], room[1] / logo.size[1])
    sized = logo.resize((max(1, round(logo.size[0] * scale)), max(1, round(logo.size[1] * scale))), Image.LANCZOS)
    canvas.paste(sized, ((W - sized.size[0]) // 2, (H - sized.size[1]) // 2), sized)
    buf = io.BytesIO()
    (canvas if transparent else canvas.convert("RGB")).save(buf, "PNG", optimize=True)
    out = ad_compress.to_cap(buf.getvalue(), row["max_bytes"], tuple(row["file_types"]), row["slot"],
                             floor=(row["min_width"], row["min_height"]))
    if out["changed"]:
        print(ad_compress.line(row["slot"], out))
    return out["content"]


# ─── The order ───────────────────────────────────────────────────────────────

def produce(client_id: int, brief: dict, generate=None, craft=None, load_logos=None, store=None,
            stopped=None, make_video=None, upload_video=None) -> tuple:
    """(manifest, files) for one order. The four callables are the real tools
    by default and fakes in the tests: generate(prompt, aspect) -> bytes;
    craft(brief, concept, earlier_prompts) -> prompt; load_logos(client_id) ->
    {version: bytes}; store(order_id, name, bytes, mime) -> {"id", "link"}.
    Raises ProductionBlocked only when NOTHING can be made (no tool, no craft
    library, stopped); a file that cannot be made is simply absent or flawed,
    and the handover check names it."""
    leaked = [k for k in TEXT_KEYS if k in (brief or {})]
    if leaked:
        raise ValueError(f"the media agent was handed text fields ({', '.join(leaked)}) — it takes media_brief only")
    from core import post_brief
    rows = brief.get("required_assets") or []
    images = [r for r in rows if r["kind"] == "image"]
    variant = (brief.get("palette") or {}).get("variant") or ""
    roles = (brief.get("palette") or {}).get("roles") or {}
    backdrop = roles.get("bg") or ""
    assets, files, notes, prompts = [], {}, [], []
    retries = RETRIES
    stopped = stopped or (lambda: False)

    def keep(row, n, content, source, **extra):
        name = f"{row['slot']}-{n}.{gaf.file_type(content) or 'bin'}"
        saved = store(brief["id"], name, content, "image/" + ("jpeg" if name.endswith("jpg") else "png")) if store else {}
        files[media_order.key(row["slot"], n)] = content
        drawn = None
        if row["kind"] == "image":
            # What the FILE holds, measured (the same measurement a slide gets) — recorded, and
            # re-measured from the bytes by the handover check
            drawn = render_manifest.for_asset("image", {})
            render_manifest.measured(drawn, content)
        assets.append(media_order.asset(row["slot"], n, file_id=saved.get("id"), link=saved.get("link"),
                                        source=source, palette_variant=variant, drawn=drawn, **extra))

    if images:
        generate = generate or _default_generate(client_id)
        craft = craft or _default_craft(client_id)
        if store is None:
            store = _default_store(client_id)
        master_row = max(images, key=lambda r: r["width"] / r["height"])        # the widest: every other ratio fits in it
        for n in range(1, max(r["count"] for r in images) + 1):
            if stopped():
                raise ProductionBlocked("stopped")
            prompt = craft(brief, n, list(prompts))
            post_brief.assert_no_hebrew(prompt)                # Hebrew is never sent to a model to be drawn
            prompts.append(prompt)

            def made(row, source_bytes=None):
                """(bytes, source) for one row of this concept; regenerates on a failed crop check."""
                nonlocal retries
                source = "derived" if source_bytes else "generated"
                raw = source_bytes or generate(prompt, row["aspect"])
                while True:
                    final = to_slot(row, raw)
                    result = crop_survival.check(final, row["slot"], targets=tuple(row["crop_targets"]))
                    print(crop_survival.line(result) + f" (order {brief['id']}, concept {n}, {source})")
                    off = render_manifest.off_palette(render_manifest.measure_image(final), roles)
                    if off:
                        print(f"[{SERVICE_NAME}] {row['slot']} background measured {off['background']}, nearest brand "
                              f"colour {off['nearest']} (order {brief['id']}, concept {n})")
                    if (result["ok"] and not off) or retries <= 0 or stopped():
                        return final, source, raw
                    retries -= 1
                    source = "generated"
                    raw = generate(prompt + " The subject is small and exactly centred, with wide empty backdrop "
                                            "on every side." + (f" The backdrop is the flat colour {backdrop}."
                                                                if off and backdrop else ""), row["aspect"])

            master = None
            if n <= master_row["count"]:
                try:
                    content, source, master = made(master_row)      # master = the picture as generated (full size)
                    keep(master_row, n, content, source, prompt=prompt[:300])
                except ProductionBlocked:
                    raise
                except Exception as e:
                    notes.append(f"{master_row['slot']} {n}: not produced ({type(e).__name__}: {str(e)[:120]})")
            for row in images:
                if row is master_row or n > row["count"]:
                    continue
                try:
                    cut = master if master and derivable(master, master_row["slot"], row) else None
                    content, source, _ = made(row, cut)
                    keep(row, n, content, source)
                except ProductionBlocked:
                    raise
                except Exception as e:
                    notes.append(f"{row['slot']} {n}: not produced ({type(e).__name__}: {str(e)[:120]})")

    logo_rows = [r for r in rows if r["kind"] == "logo"]
    if logo_rows:
        if store is None:
            store = _default_store(client_id)
        logos = (load_logos or _default_logos)(client_id) or {}
        source = logos.get("primary") or next(iter(logos.values()), None)
        if not source:
            notes.append("logo: the client has no logo file in the brand folder — a logo is never generated")
        for row in logo_rows if source else []:
            try:
                keep(row, 1, fit_logo(row, source), "logo")
            except Exception as e:
                notes.append(f"{row['slot']}: not produced ({type(e).__name__}: {str(e)[:120]})")
    for row in rows:
        if row["kind"] == "video":
            if stopped():
                raise ProductionBlocked("stopped")
            assets.append(video_asset(client_id, brief, make_video, upload_video))
        elif row["kind"] == "banner":
            notes.append(f"{row['slot']}: uploaded banners are not produced automatically")
    return media_order.manifest(brief, assets, notes), files
