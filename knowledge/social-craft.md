# Social & Design Craft Library — v0.2
Discipline: social content + visual design
Shared by the social agent and the media agent. Both load this file in full.
Last updated: 2026-11-23

---

## 0. How to use this file

This is craft knowledge, not a task list. Load it before producing any social
content. It tells you how work in this discipline is judged.

Two kinds of content live here:

- **Stable craft** (sections 1-10, 12-14). Changes slowly. Trust it.
- **Platform mechanics** (section 11). Dated and sourced. Re-verify quarterly.
  Never state a platform behaviour from memory — only from this section, and
  only if the date is current. If section 11 is stale, say so instead of
  guessing.

This file is discipline-level. It does not contain client-specific facts.
Those live in the client's brand brief, which you read alongside this.

Both agents read the whole file, including the parts they do not execute. The
media agent needs to know why slide 1 is a hook in order to shoot it. The
social agent needs to know the visual constraints in order to write a brief
that can be built.

---

## 1. Roles and accountability

Two agents share this discipline. Each owns its half and is accountable for
its quality.

**Social agent — owns the message.**
Objective, format choice, slide structure, all copy, caption, CTA, posting
and scheduling. Writes the brief. Assembles the final post. Submits it for
approval. **Accountable for the post as a whole.**

**Media agent — owns the execution.**
Every visual and audio asset: template rendering, image generation, video,
asset selection from the client library, dimensions, legibility, visual
consistency across a set. **Accountable for whether the assets are
professional and for whether they match the brief.**

Neither owns: client priorities, budget, or the relationship with the
business owner. That is the manager's. The business owner never speaks to
either of you.

**Both of you are expected to disagree.** If the brief is wrong for the
objective, or the visual direction cannot work, say so in one line and
propose the alternative. A specialist who silently executes a bad brief is
not a specialist. But disagreement is bounded — see 2.4.

**Cross-check, not self-approval.** The media agent checks the incoming
brief against the rubric in section 14. The social agent checks the returned
assets against the same rubric. An agent that reviews its own work always
passes it.

---

## 2. Procedure — request to approved post

### 2.1 Social agent, before any asset exists

1. **Objective.** awareness / consideration / conversion / retention. If it
   is not stated and cannot be inferred, ask the manager one short question.
2. **Brand brief.** Read it. Category, positioning, audience, tone, visual
   identity, forbidden list, available assets.
3. **Format.** Choose from section 3 and state the reason in one line. "A
   carousel about why X" is almost always a text-led carousel.
4. **Copy first.** Write every slide and the caption, in full. The post
   exists as text before it exists as pixels.
5. **Self-check** against sections 9 and 14. Fix what fails.
6. **Emit the brief** (2.2). Do not write image prompts. You do not own them.

### 2.2 The brief — the contract between the two agents

A structured object, one entry per asset. Never prose.

```
asset_id
post_format
objective
client_id                 -> media agent loads the same brand brief
role                      hook | point | proof | cta
copy_headline             CONTEXT ONLY
copy_body                 CONTEXT ONLY
asset_type                text_slide | real_photo | generated_image |
                          generated_video
visual_direction          what should be seen. written by the social agent
                          as intent, not as a model prompt
must_include
must_avoid
aspect_ratio
set_id                    assets sharing a set_id must look like one system
budget_credits
```

**Hard rule.** `copy_headline` and `copy_body` are context. They are never
the source of an image prompt. The only case where copy is rendered is
`asset_type = text_slide`, and then it is rendered from the brand template,
never by an image model. Any pipeline that lets copy reach a generation
prompt is broken and must be reported, not worked around.

### 2.3 Media agent, on receiving the brief

1. **Validate** against section 14 and section 10. If the brief fails, raise
   an objection (2.4) instead of producing something weak.
2. **Choose the source** per section 10: real asset, template, or generated.
   Priority order is not optional.
3. **Produce**, respecting `set_id` consistency and `budget_credits`.
4. **Return** per asset: the file, which source was used, any flag that
   affects approval (concept rather than real product, AI person requiring a
   label), and credits spent.

### 2.4 Objection protocol

Either agent may object once per asset. An objection is one line: what is
wrong and what to do instead.

- Round 1: the counterpart revises.
- Round 2: if still disputed, escalate to the manager with both positions in
  one line each. Do not negotiate further.
- Never more than two rounds. Ping-pong costs money and time, and a third
  round has never resolved anything a human could not resolve in ten seconds.

### 2.5 Approval

The social agent assembles the post and submits it to the manager with:
format and reason, the full copy, the assets, the visual sourcing per asset,
any flags, and total credits spent. Copy is approved before generation
spends credits on a full set.

---

## 3. Format repertoire

Each entry: what it is, when to use it, what it needs.

**text_carousel** — typography-led slides, 3-8. Use when the value is an
argument, a list, a comparison, or an explanation. The text IS the content;
visuals are background. Needs: brand slide template, headline per slide.

**product_carousel** — real product or work images, 3-10. Use when showing
what you make or proving quality. Needs: real assets. Never fabricate the
product (section 10).

**before_after** — 2+ paired frames. Use when transformation is the proof.
The strongest format for anything custom or made-to-order. Needs: matched
framing, honest pairing.

**process / behind_the_scenes** — sequence or short video. Use to build
trust in craftsmanship and justify price. High save and share rates in
maker categories.

**customer_story** — real customer, real outcome, in their words where
possible. Use for consideration-stage trust. Needs: permission.

**educational** — teach one thing properly. Use to earn saves and position
the brand as the expert. Highest save rate of any format.

**comparison** — this vs that, ours vs the common alternative. Use to
reframe a buying decision. Must be fair or it reads as defensive.

**single_image** — one strong frame. Use for an announcement, a product
drop, or an image strong enough to stand alone. If it needs the carousel
format to make sense, it is not a single image.

**reel** — short video. Use for reach to non-followers. The most expensive
format to produce; do not default to it.

Selection heuristics:
- Reach to new people → reel, or a carousel built to be sent.
- Trust and consideration → process, customer_story, before_after.
- Saves and authority → educational, text_carousel.
- Conversion → product_carousel, single_image, with an explicit CTA.

---

## 4. Carousel craft

- **Slide 1 is a hook, not a title.** A title names the topic. A hook opens
  a gap the reader must close. "4 reasons a custom piece beats a gift card"
  is a title. "The gift they kept — and the three everyone forgot" is a hook.
- **Slide 2 must pay off slide 1 immediately.** Most drop-off happens here.
- **One idea per slide.** If a slide has two ideas, it is two slides.
- **Every slide ends with a reason to swipe.** Implicit (incomplete thought)
  or explicit (an arrow, "but that's not the main one →").
- **Last slide is a single clear CTA.** One action, not three.
- **Visual consistency across the set.** Same grid, same type scale, same
  palette, same margins. A carousel is one object, not N images. This is
  what separates professional from amateur more than any single slide.
- **Slide count:** sources disagree. Some data favours 7-10 for dwell time,
  other guidance favours 5-8 for completion. Treat 4-8 as the safe default
  and extend only when each extra slide earns its place. Do not pad.
- **Readability beats cleverness.** The headline must be legible at thumbnail
  size and understood in under two seconds.

---

## 5. Reel craft

- The first 1-2 seconds decide distribution. Open on motion or on the payoff,
  never on a logo or a slow pan.
- Design for sound-off: on-screen text carries the message.
- Earn the rewatch. A clean loop multiplies total watch time.
- Keep it as short as the idea allows. Watch time is total seconds AND
  completion; a tight 12 seconds often beats a loose 40.
- One idea per reel.

---

## 6. Static craft

- One subject, one message. Negative space is not wasted space.
- If text appears, it is a headline, not a paragraph.
- The caption does the explaining the image cannot.

---

## 7. Hook and copy frameworks

Hook types, in rough order of reliability:

1. **Curiosity gap** — state an outcome, withhold the mechanism.
2. **Contrarian** — challenge a belief the audience holds.
3. **Specific number** — concrete beats vague. "3 things" beats "some things".
4. **Named pain** — say the problem in the reader's own words.
5. **Stakes** — what it costs to get this wrong.
6. **Before/after claim** — only when you can show the proof.

Caption structure that works for most posts:
first line is a second hook (feed truncates), then the context, then one
clear CTA. Keyword-aware phrasing helps discovery; keyword stuffing does not.

CTA types: save, send to someone, comment a word, DM, link in bio, visit.
Pick ONE. "Send this to the person who…" is also the strongest distribution
CTA, because sends are a top ranking signal (section 11).

Copy rules:
- Short sentences. One idea each.
- Concrete nouns over adjectives. Show, don't claim.
- No emoji in headlines. Sparingly in captions, if the brand voice allows.
- Never write marketing language the brand would not say out loud.

---

## 8. Visual craft

- **Hierarchy.** Every slide has exactly one element the eye lands on first.
- **Type scale.** Three sizes maximum: headline, body, caption. Fixed ratio
  across the whole set.
- **Grid and margins.** Identical on every slide. Nothing closer to the edge
  than the agreed margin.
- **Palette.** Taken from the brand tokens. Two to three colours plus
  neutrals. Accent colour used once per slide, not everywhere.
- **Contrast.** Text must pass a legibility check against its background. If
  the background is an image, it needs a scrim or an overlay.
- **One typeface family** per brand, two weights.
- **Consistency over variety.** A set that looks like one system reads as
  professional even when individual slides are plain.

---

## 9. Failure list — reject your own work if any apply

- Slide copy was used as the image prompt.
- Text slides generated by an image model (illegible or malformed letters).
- A product shown that the client does not actually sell.
- Different fonts, margins, or colour treatment between slides of one set.
- Headline longer than can be read in two seconds.
- More than one CTA.
- Slide 1 is a title instead of a hook.
- Two or more ideas on one slide.
- Stock-looking image with centred text and no brand system.
- Emoji carrying meaning the words should carry.
- Claims the brand cannot substantiate.
- Translated-sounding Hebrew (section 12).
- Text clipped or overflowing its box.

---

## 10. Visual sourcing policy

Three sources, in this priority order per slide:

1. **Real client assets** — actual products, actual work, actual customers,
   actual workspace. Required whenever the post makes a claim about what the
   business delivers, or shows a specific purchasable item.
2. **Brand template / typography** — for text slides. Rendered, not generated.
3. **Generated imagery** — allowed and encouraged for: atmosphere, studio and
   workspace scenes, hands at work, blank or undecorated items before
   customisation, textures, backgrounds, conceptual and abstract frames.

**The line:** never fabricate a specific item and present it as a real
product the customer can order. That is a promise the business cannot keep.
Generating the *world around* the product is honest; generating the product
and implying it exists is not.

When a generated image depicts a concept rather than a real item, the
approval card must say so.

**AI-person disclosure:** if an account is built around an AI-generated
person (a recurring synthetic presenter or model), that account must carry
the platform's AI label. See section 11 — this one has a written penalty.

---

## 11. Platform mechanics — Instagram

**Verified: 2026-11-23. Re-verify by: 2027-02-23.**
Everything below is sourced. If today is past the re-verify date, treat this
section as unreliable and research before relying on it.

**Not one algorithm.** Instagram runs separate ranking systems for Feed,
Reels, Stories, Explore and Search. The same post can behave very
differently on each surface.

**The three signals Mosseri has named as most important:**
- **Watch time** — total seconds plus replays. Strongest signal overall, and
  the one that decides whether a video is distributed at all.
- **Sends per reach** — DM shares divided by reach. The strongest signal for
  reaching people who do NOT already follow the account.
- **Likes per reach** — weakest of the three; carries more weight with
  existing followers.

Saves are widely reported as a strong signal for carousels and educational
content. Comments did not make the named top three.

**Practical consequence:** design posts to be *sent*, not just liked. A post
worth forwarding to one specific person outperforms a post that is broadly
pleasant.

**Carousels.** Swipe depth is tracked. Reported behaviour: a viewer who does
not finish may be re-served the post later with an unviewed slide as the
cover. Completion rate matters, which makes the middle slides structurally
important, not just slide 1.

**Originality.** Since the April 2026 update, originality enforcement covers
all formats, not just video. Content that is largely unoriginal or reposted
may not be recommended to non-followers. Adding borders, watermarks or speed
changes does not make something original; new commentary, editing or voice
does.

**AI content.** Mosseri (October 2026): Instagram does not throttle reach for
content being AI-generated as such. But two caveats matter:
- Since 31 August 2026, a profile built around an AI-generated person that
  does not carry the AI label can be removed from recommendations. This is
  the one written AI penalty.
- Mosseri has separately suggested AI posts tend to rank lower in practice —
  not by penalty, but because they lose the engagement contest. Treat AI
  imagery as a production tool, not as a shortcut to reach.

**Reported not to hurt reach:** scheduling posts, hashtag count. Hashtags
matter less than they used to; keyword-aware captions matter more.

Sources (for refresh):
- https://www.zoomsphere.com/blog/instagram-algorithm-2026-what-actually-gets-reach
- https://www.eclincher.com/articles/how-the-instagram-algorithm-works-in-2026
- https://www.inro.social/guides/instagram-algorithm-guide
- https://ethicalsmm.com/blog/instagram-updates-november-2026
- https://getixgroup.com/blog/does-ai-content-hurt-social-reach-2026
- https://carouselli.com/blog/instagram-carousel-best-practices

---

## 12. Hebrew and RTL

- Write Hebrew that sounds spoken, not translated. If a sentence reads like
  it was written in English first, rewrite it.
- Avoid English marketing loanwords where a natural Hebrew phrase exists.
- Headlines are shorter in Hebrew than the English equivalent. Keep them
  tight; Hebrew words are visually denser.
- Mixed Hebrew/Latin strings (brand names, URLs) need explicit direction
  handling or they render in the wrong order.
- Numbers and punctuation at the end of an RTL line are a frequent rendering
  bug. Check them visually.
- Never render Hebrew text through an image model. Render it from a template.
- **Never translate or latinise the term עיצוב אישי. It appears in Hebrew,
  always, in every context — prompts, captions, UI strings, file names.**

---

## 13. Measurement

Match the metric to the objective:
- Awareness → reach, and share of reach from non-followers.
- Consideration → saves, profile visits, carousel completion.
- Conversion → link clicks, DMs started, actual enquiries.
- Distribution health → sends per reach.

Do not report raw likes as a success measure. Report per-reach rates so
posts of different sizes can be compared.

Read carousel drop-off per slide. A low swipe rate off slide 1 means the
hook failed. A cliff at slide 4 means slide 4 did not earn the next swipe.

---

## 14. Quality rubric — what the critic scores

Score each 0-2 (0 fail, 1 adequate, 2 strong). Below 80% total = return for
rework.

1. Format fits the stated objective, and the reason is stated.
2. Slide 1 opens a gap rather than naming a topic.
3. One idea per slide, and each slide earns the next swipe.
4. Copy is specific and concrete, not generic marketing language.
5. Exactly one CTA, and it matches the objective.
6. The set is visually one system (grid, type, palette, margins).
7. Visual sourcing follows section 10; nothing fabricated as a real product.
8. Hebrew reads natively; no rendering or direction errors.
9. On-brand per the brand brief; nothing on the forbidden list.
10. Nothing from the section 9 failure list is present.

Every failure recorded here should be written back into this file as a new
rule, with the date.

---

## 15. Known gaps — to research

- Israeli market specifics: posting rhythms, local reference points, what
  reads as premium vs cheap in Hebrew-language feeds.
- TikTok and YouTube Shorts mechanics (this file is Instagram-only).
- Category playbooks (custom/made-to-order, food, services, B2B) — separate
  files, referenced from the brand brief.
- Benchmarks by category for save and send rates.
