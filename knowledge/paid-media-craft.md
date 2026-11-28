# Paid Media Craft Library — v0.1
Discipline: creative for Google Ads (display, Performance Max, Demand Gen,
YouTube, search)
Shared by the paid-media agent (Yoav) and the media agent (Lior). Both load
this file in full.
Last updated: 2026-11-28

---

## 0. How to use this file

This is the judgment layer: what a table cannot encode. It sits next to
`knowledge/social-craft.md` and follows the same rules of use.

Two kinds of content live here:

- **Stable craft** (sections 1-3, 5). Changes slowly. Trust it.
- **Platform mechanics** (section 4). Dated. Re-verify quarterly.

**This file holds no sizes, ratios, character limits or file caps.** Every
such number is in `core/google_ads_formats` and only there. If you need one,
it is in your order row or in the table — never write one from memory, and
never copy one into a brief, a prompt or this file. A number written twice
goes stale in one of the two places.

Section 1 of `knowledge/social-craft.md` (roles, accountability, cross-check)
applies here unchanged, with Yoav in the place of the social agent.

---

## 1. Roles and accountability

**Paid-media agent (Yoav) — owns the campaign.**
Campaign type, objective, audience segment, landing page, what is being
advertised, the deadline. Issues the media order. Verifies the delivered set
against the platform's minimum before anything is uploaded. **Accountable for
whether the campaign can serve and for whether it is the right campaign.**

**Media agent (Lior) — owns the execution.**
Every image, logo file and video in the order, in every ratio the order
lists. **Accountable for whether the set is complete, within its caps, safe
under Google's crops and on-brand.**

**Copy — owns the words.**
Headlines, long headlines and descriptions are TEXT ASSETS. They are written
in their own step against Google's character limits and never by the step
that makes images. Image copy and ad copy coming out of one step is how the
carousel bug happened (social-craft §2.2, the hard rule).

Lior does not decide the campaign type. Yoav does not decide how the creative
looks. Neither writes the other's half, and neither approves their own.

Neither owns: the client's budget, priorities, or the relationship with the
business owner. That is the manager's.

---

## 2. The five rules

### 2.1 Crop survival

Google does not show the image you made. It shows crops of it — one landscape
image is cut into dozens of placements, down to a square and down to a wide
strip, always from the centre. A subject sitting off-centre is not "less
prominent" in those placements. It is gone.

Every display image must survive the centre crops the table lists for its
slot, with its subject and the product still in frame and readable.

This is not advice. `core/crop_survival` takes the PRODUCED file, makes those
crops and measures what is left of the subject in each one. A file that fails
is not delivered. The same principle as the render manifest: what is said
about a file comes from the file, not from the brief that asked for it.

What follows for the person composing the image:

- Put the subject in the middle of the frame, both ways.
- Keep it smaller than feels natural. A subject that fills the height of a
  landscape image cannot fit the wide strip, however well it is centred.
- Treat everything outside the central band as disposable: atmosphere,
  backdrop, breathing room. Never the product, never a face, never the offer.
- The check finds the subject from the pixels (what stands out from the
  border). It cannot tell the product from a brighter object next to it. When
  you know where the product is, say so (the product box) and the check holds
  the image to that as well.

### 2.2 Text belongs in text fields, not in the image

Google's old limit on text in images is gone. The reason to keep text out is
not. A headline baked into a large landscape image is unreadable when the
same image is served as a small rectangle, and it is cropped away entirely in
the strip.

The message goes in the headline and description assets, where Google
renders it at the right size for every placement. An image may carry:

- the logo, and
- one short product or price badge.

Nothing more. No headline, no body copy, no call to action, no phone number.
If an image "needs" text to make sense, it is the wrong image.

Hebrew is never rendered by an image model (social-craft §12). A badge is
drawn in code, in the brand font, like a text slide.

### 2.3 The 150 KB problem

Uploaded static banners, Demand Gen logos and companion banners have a small
file cap — a small fraction of what a normal image is allowed. A wide banner
at that weight is a real compression job, not an export setting.

`core/ad_compress` compresses to the cap, measures the damage against the
original, and keeps the least damaged version that fits. If a file cannot get
under its cap without visible degradation, it FAILS by name and says how
close it came. Nothing soft is shipped to make a deadline.

What follows for the designer: a small-cap file is designed for its cap. Flat
colour, few gradients, no photographic noise, no fine texture. If a banner
keeps failing, the design is wrong for the format — simplify it; do not ask
for a lower quality floor.

A banner's pixel size is its spec and is never changed to make it lighter. A
logo has a minimum size, not an exact one, and may be stepped down toward it.

### 2.4 Coverage beats polish

Filling every ratio and every text slot gives the algorithm more combinations
to test and more placements to serve in. One beautiful landscape image with
three headlines will underperform a complete asset group every time.

Lior's job on a paid order is a full set, not a hero image:

- every ratio the campaign type takes, not only the required ones;
- several distinct concepts, each delivered in every ratio;
- both logo shapes where the type takes both.

An incomplete set is never handed over as "most of it". An asset group below
the platform's minimum does not serve at all — and it is worse than no asset
group, because it looks like work was done.

Distinct means distinct. Five crops of one photograph are one concept, and
the platform treats near-duplicates as one asset.

### 2.5 The brand palette is a resource, not a cage

Brand colours are the language of the client's SITE. Paid creative is not
limited to them (the owner, 2026-11-29): the order carries the segment's
palette variant as material to draw on, and the picture uses it, part of it
or other colours — whatever serves the ad. No colour fails a delivery. What
the file really holds is measured from its pixels and reported as it is.

The same sourcing policy applies (social-craft §10): real client assets
first; never fabricate a specific product and present it as one the customer
can order. An ad makes a sharper promise than a post, and the landing page
has to keep it.

The logo is the client's logo file. It is never generated, redrawn or
"improved" by a model.

---

## 3. Failure list — reject the set if any apply

- A required slot of the campaign type is empty.
- A file is over its cap, in a type the slot does not take, or the wrong
  ratio — measured from the file, not from its name.
- An image whose subject does not survive the centre crops.
- A headline, a sentence or a call to action baked into an image.
- Hebrew text produced by an image model.
- A video delivered as a file instead of a YouTube URL on the client's
  channel.
- A logo that is not the client's file.
- Two "concepts" that are the same picture.
- Ad copy and image copy that came out of the same step.
- A product shown that the client does not sell (social-craft §10).
- The set was approved by the agent that made it.

---

## 4. Platform mechanics — Google Ads

**Verified: 2026-11-28. Re-verify by: 2027-02-28.**
The numbers themselves are in `core/google_ads_formats`, which carries the
same two dates and reports itself stale past the second one. If today is past
the re-verify date, treat this section and the table as unreliable and
re-verify before relying on either.

What is in motion (the reason the table is dated):

- **Display is migrating to Demand Gen.** New display work should be planned
  as Demand Gen unless there is a reason not to. Demand Gen adds the vertical
  ratio, which is what Shorts placements need.
- **Demand Gen's logo is the odd one**: it has the small file cap, not the
  cap every other image has. A logo file that is fine in a Performance Max
  asset group can be rejected here.
- **Performance Max takes more video per asset group than it did** (the
  ceiling was raised in early 2026). When a group has no video, Google may
  assemble one from the images — another reason the images must survive
  being cropped and recombined.
- **A Performance Max group below its minimum does not serve at all.** There
  is no partial delivery.
- **Responsive display ads do not take GIF.** Uploaded static banners and the
  companion banner do.
- **A video is never a file in a campaign.** It is uploaded to the client's
  YouTube channel first and referenced by URL. This is a step in the
  workflow, not a detail of the upload form.
- **Video length decides the format**, not the other way round: a bumper is a
  few seconds and cannot be skipped; a non-skippable ad over the standard
  length runs on connected TV only and must be horizontal; an in-feed video
  needs a minimum length before it can build a remarketing list.

### 4.1 Reading the account — the two search reports

Code: `core/search_reports.py`. These are rules of judgement, not of format.

- **Neither report means anything in an account that is not measuring
  conversions. Check that first, always** (`core/conversion_measurement`). A
  term that "produced nothing" in an account that counts nothing has not been
  shown to be waste, and more showings are not worth buying before we know
  what a showing brings.
- **Impression share is never reported without its split.** Share lost to
  budget and share lost to rank demand opposite responses: lost to budget
  means the campaign is too small and more money buys more; lost to rank
  means relevance, quality or bid is the problem and more money only burns
  faster. One blended number is a number nobody can act on. Recommending more
  budget where the loss is to rank is active harm.
- **A search terms report is read for waste first and opportunity second.**
  Most accounts lose more to terms that should never have matched than they
  gain from terms they have not added.
- **Performance Max is not Search.** It has no search terms report, only
  category insights (a separate, more limited resource), and impression share
  is not reported for it the way it is for Search. Say what cannot be seen;
  never show an insight category as a search term, or a missing share as zero.

---

## 5. Known gaps — to research

- Whether a video on the client's channel must be public or unlisted for each
  ad format (the system's own YouTube upload path publishes Private only).
- Israeli-market creative benchmarks per campaign type.
- Animated (HTML5 / GIF) uploaded banners — not built; static only.
- Demand Gen carousels as a deliverable of their own (the table has the
  rules; no order produces one yet).
- The crop check finds a subject from pixels. A check that understands what
  the product is would need a vision model per file — not built, not costed.
