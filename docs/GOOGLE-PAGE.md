# The client's Google page ("גוגל") — 2026-11-28

`core/google_page.py` · `core/google_page_actions.py` · `GET /api/client/google` · the `google` view in
`dashboard/client/index.html` · `tests/test_google_page.py`

Where Yoav stops being a black box. Every line on it obeys `knowledge/client-governance.md` §9: no platform
vocabulary in any section, reports included. Every sentence is built on the server (one five-language `TEXT`
table); the dashboard only lays it out.

## The five sections

1. **איך המודעות ייראו** — the full asset set of every campaign we prepared (media orders that were not rejected,
   and the search campaigns Yoav drafted): each image with its ratio, every headline, every description, the logo.
   Next to it up to three EXAMPLE combinations, each labelled "דוגמה N לשילוב אפשרי. זו לא המודעה." and, once,
   "גוגל מחליטה בכל הצגה איזה שילוב להראות, לפי מה שעובד." No rendering is ever presented as "the ad". A video
   campaign is not assembled: the video is shown as the ad it is, with no examples.
2. **הנתונים** — the budget (agreed, spent this month, left, 7-day daily pace, days the budget lasts at that pace,
   days left in the month) and the results in the client's own action word (`client_speech`), with the SHORT forms
   of the terms table. Results are shown PER LANE (governance §2), from the lane RECORDED when a campaign was
   created (`campaign_created.details.lane`); a campaign with no recorded lane is its own block and is never
   guessed into a lane. There is no total across lanes, and no single trend when more than one lane was active.
   The budget is the account's money and is one figure.
   A live read is remembered (at most hourly). When the read fails, the remembered figures are shown WITH the date
   they were read and said to be old; with nothing remembered the section says what is missing and what it takes.
3. **הביטויים** — the search phrases of a SEARCH campaign only, grouped by theme with why each was chosen (written
   by `write_google_strategy`; until then the phrases are listed and the page says the reasons are not written).
   A campaign Google places by itself has no phrase list: it shows search themes, labelled as a hint to Google and
   not targeting. The client FLAGS a phrase with a reason (`POST /api/client/google/flag`); it changes nothing and
   waits for Yoav, who decides in the next strategy run. Phrases we deliberately do not pay for are listed with the
   reason — and with whether the change was actually made in the account.
4. **דוחות** — `build_google_report` (asked for in Yoav's chat) keeps the day's figures as a report: readable on the
   page, downloadable as a Word file, printable. Only from a LIVE read. Up to 36 are kept.
5. **האסטרטגיה** — `write_google_strategy`: what we do with the money and why, what it assumes, the two or three
   numbers we watch, and what would make us change course AND BY WHEN. A version without a dated abandon condition
   (a real date, after today, within 180 days) is refused. A rewrite must say what changed. Every version is kept
   (24) and visible under "גרסאות קודמות".

## Storage

One key per client: `app_settings` `google_page:{client_id}` = `{strategy, terms, flags, excluded, reports,
numbers}`. Registered for deletion in `client_deletion.py`, `deletion_guard.py`, `admin_service.py`.

## Known gaps (2026-11-28)

- Nothing here has run against a live Google account, a live model, or a browser.
- Lanes are recorded only for campaigns created from now on; every existing campaign shows as "unrecorded".
- An exclusion Yoav decides is NOT applied in the Google account by the system: the owner is alerted and the page
  says "the change in the account has not been made yet". Nothing marks it applied yet.
- No campaign carries search themes yet (`order["search_themes"]`), and the system defines no excluded phrases of
  its own, so both lists are empty until that exists.
- The trend is account-level, so it is shown only when exactly one lane was active.
- A media order is shown as "not live yet": nothing launches a campaign from an order today.
- fr / ar / ru were written without a native speaker (`client_speech.UNREVIEWED_LANGS`).
