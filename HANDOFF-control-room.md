# HANDOFF — Admin Control Room (חדר בקרה) (2026-10-13)

## What existed before (investigated first)

| Data | Where it lived | Surfaced in admin? | Reused how |
|---|---|---|---|
| Agent activity | `client_activity` | client drawer, last 8 rows, raw `agent · action` | `get_activity` (40 rows) |
| Our fee / cost / margin | `admin_service.get_client_admin` | drawer | as-is |
| Paid budget vs spend | `budget_agent.get_financial_picture` (stated budget parsed from onboarding answers + live 30-day Google/Meta spend) | drawer "תמונה פיננסית" | `control_room.budget_split` reshapes it |
| Organic budget | only `forecast.stated_organic_budget_*` (onboarding text) — **no organic spend line exists anywhere** | not shown | shown as stated budget + organic agents' work count (30 days) |
| Alerts | `alerts` table — **no client_id column**; agents write "client {id}: …" in the issue text | global alerts tab only | matched per client by that text convention |
| Client feedback | `client_feedback` (engagement_agent) | **never shown** — `/api/admin/feedback*` are X-Admin-Key only, which the browser doesn't have | `list_feedback(client_id=…)` + a session-gated "mark reviewed" |
| Connection gaps | `client_journey.connection_status` | not in admin | "needs your attention" item |
| SEO strategy awaiting approval | `seo_agent.get_pending_strategy` | drawer | "needs your attention" item |
| Persona data | `support_agent.PERSONAS[..]["reads"]` | client chat only | reused as the admin chat's grounding |

**Nav placement**: the admin sidebar is a flat list (`showTab`). "🛰 חדר בקרה" sits
second, right after סקירה כללית and before לקוחות — it's the daily operating
screen, and keeping it apart from לקוחות keeps the existing drawer untouched. Its
badge counts clients needing attention.

**Why a new table for the chat**: `client_communications` feeds the client's chat
history AND `_build_client_export` (the client's own data archive);
`client_activity` is in that export too and in their dashboard feed. Anything
written there can reach the client. `admin_agent_chat` is read by nothing
client-facing, and no WhatsApp code touches it.

## What was built

| Piece | Where |
|---|---|
| List / client view / budget split / alert matching / admin chat + grounding | `core/control_room.py` |
| `GET /api/admin/control-room`, `GET …/{id}`, `GET …/{id}/budget`, `GET+POST …/{id}/chat`, `POST …/feedback/{fid}/reviewed` (all admin session) | `core/api_server.py` |
| Table | `migrations/2026-10-13-admin-agent-chat.sql` (purged on offboarding) |
| UI | `dashboard/admin/index.html` — tab `control` |
| `list_feedback(client_id=)` | `agents/engagement_agent.py` |

- **List** = triage queue: `attention` (open client-matched alerts or unreviewed
  feedback) → `stalled` (active, no agent activity 7+ days) → `ok` → `inactive`.
  One alerts read + one feedback read for the whole list.
- **Client view**: needs-your-attention (alerts resolvable, feedback markable,
  missing connections, SEO pending, stalled), organic vs paid (lazy — live ad
  APIs), activity, alerts, feedback, and the chat panel. Any section that
  failed to load is named in a "לא נטען" line rather than looking empty.

## Grounding — how the agent avoids guessing

Per question, `_grounding()` fetches a fresh data block: client + our numbers,
proposal, last 30 activity rows, open client alerts, connection status,
unreviewed feedback, the persona's own `reads` (general = all four), and for
general/google/meta the organic-vs-paid split. Stamped `data_fetched_at`. Each
source is tried separately; a failure — or a read that returned an `error` —
becomes a line in `data_gaps`. The prompt: every number must be in DATA, name
gaps as failures (not zeros), say "not connected" when absent, quote time
windows. The UI shows the gaps under the answer. Johnny-facing voice: direct,
technical, recommends HIS next actions; read-only (no action paths reachable).
Failures show Johnny the real reason — he's the operator.

## Required before it works fully

1. Run `migrations/2026-10-13-admin-agent-chat.sql` — until then the chat
   answers but doesn't persist (the panel says so).
2. Deploy.

## Limits

- Alert↔client matching depends on the "client {id}" wording convention.
- `last_activity` on the list comes from the newest 1000 activity rows overall
  (existing admin_service behaviour); a client quiet beyond that reads "stalled",
  which is the right call anyway.
- Each admin question costs one Claude call plus the platform reads
  (`cost_category='claude_admin_chat'`).
- Not run: no Python here. Admin JS parsed and smoke-run against a stubbed DOM
  (list, client view, budget errors, gaps, persona switch, send).
  First real test: Shin Sekai → "מה קורה עם קמפיין הגוגל?" on יואב.
