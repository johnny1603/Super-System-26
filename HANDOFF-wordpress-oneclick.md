# HANDOFF — WordPress one-click connect (Authorize Application) (2026-10-13)

## What it does

The site card's default is now **"חיבור בלחיצה אחת"**: the client types their
site address, we send them to their own wp-admin approval screen
(`/wp-admin/authorize-application.php`, WordPress core 5.6+), they log in if
needed and click Approve, and they land back on the dashboard connected.
Nothing to create or paste. The old manual Application Password form is still
there, one click away ("לא עובד? חיבור ידני").

| Piece | Where |
|---|---|
| Anonymous discovery (is the approval screen available?) + introspect helper | `core/wordpress_service.discover_authorization`, `introspect_application_password` |
| Start / complete / reject, credential rotation | `agents/website_agent.py` "One-click connect" section |
| Signed state pinning client + site + nonce | `core/session.create_wp_connect_state` / `verify_wp_connect_state` |
| `POST /api/website/authorize/start`, `GET .../callback`, `GET .../rejected` | `core/api_server.py` |
| Card UI, return handling, 5-language copy | `dashboard/client/index.html` |

Storage is unchanged: the approved credential goes through the same
`connect_site()` as a pasted one (same validation, same `client_accounts` row).

## Investigated before building (WordPress core source, 2026-10-13)

- On approval WP appends `site_url`, `user_login`, `password` to `success_url`.
- On "No" WP redirects to `reject_url` **as-is** — nothing appended — so our
  signed state is embedded in both URLs.
- success/reject URLs must be https; `app_id` must be a UUID.
- The screen only works when `wp_is_application_passwords_available()`:
  site on HTTPS (or `WP_ENVIRONMENT_TYPE=local`) and no plugin filtering it
  off. Core also refuses on sites protected by HTTP basic auth (staging).
- The anonymous REST index advertises
  `authentication.application-passwords.endpoints.authorization` exactly when
  available — the official discovery hook, used before redirecting anyone.

## What can block it

| Cause | What the client sees |
|---|---|
| Site not on HTTPS | `ERR_SITE_NOT_HTTPS` — neither path can work until the host enables SSL |
| Application passwords disabled (Wordfence's "Disable application passwords" option — one source says it's on by default; Solid Security; dedicated disable plugins; some hosts) | `ERR_WP_APP_PASSWORDS_DISABLED` — manual form NOT offered, it needs the same feature |
| REST index blocked for anonymous visitors (security plugin / WAF) | Unknown, not "no": we try the conventional wp-admin URL; manual form one click away |
| Not WordPress / can't reach the site | `ERR_NOT_WORDPRESS` / `ERR_SITE_UNREACHABLE` |
| Host strips the `Authorization` header (some CGI/FastCGI setups) | Approval succeeds but validation 401s → `?connect_error=wordpress`, manual form shown (it hits the same wall; real fix is on the host) |
| WP < 5.6, WordPress.com non-Business plans | Screen doesn't exist → manual path / support |
| Client denies or closes the window | Fixed Hebrew retry guidance in אורי's thread; dashboard reopens the card + that chat |

## Deviation from the handoff: the password is rotated

The handoff said "store exactly like the existing flow". Storage is identical,
but WordPress returns the password in a **URL query string**, which Cloud Run
and the proxy log and the browser keeps in history. So we use it once to mint
a fresh Application Password over REST, store that, and delete the URL-borne
one (identified via `/users/me/application-passwords/introspect`, WP 5.7+).
Only that one password is ever deleted. If rotation fails, the original is
stored and `website_authorize_completed.credential_rotated=false` records it.
On WP 5.6 exactly (no introspect) the original lingers alongside the new one.

## Security

- Callback requires: valid session + signed state (client_id + site, 30 min) +
  matching nonce cookie. A forged link carrying someone else's site/password fails.
- The callback's `site_url` param is ignored — the site comes from our state.
- Callback responses redirect with `Referrer-Policy: no-referrer`.

## Required before it works

Deploy. No migration, no env var, no third-party registration.

## Not verified

Not run against a live WordPress site, and no Python on this machine — the
Python was reviewed by reading; dashboard JS parsed with Node. First real test:
connect a throwaway InstaWP site via one-click, check `client_accounts` and that
the client's Application Passwords list shows exactly one "uallak" entry.
