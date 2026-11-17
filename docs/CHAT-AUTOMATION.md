# Chat automation: a sales talk, one card (2026-11-17)

The home page card "אוטומציית שיחות" (and the manager's chat) used to run a connect flow:
it named the tool, asked "do you already have an account there?" and showed a "חבר" button.
There is nothing for the client to connect. Now it is a short sales conversation that ends
in ONE approval card.

## What the client sees
- **The card:** a value line plus the button "ספרו לי עוד" (`automation_more`, 5 languages). It calls
  `openInvite('automation')`, which opens the manager's chat with the invite opener.
- **The manager** (ג'וני / ניסים / אלינור) sells it in their own voice. The rule is
  `chat_automation.SALES_RULE`, which `support_agent` reads as `_AUTOMATION_RULE`. It covers:
  - **the value:**
    - instant replies at any hour, so a lead never waits or goes to a competitor;
    - a responsive, professional impression;
    - leads warmed up and qualified (serious? need? budget?) before any human contact;
    - less time spent on irrelevant enquiries;
  - **one example from the client's industry**, taken from `client_knowledge`;
  - **questions as 2-3 ready guesses** the client can confirm or correct, never an empty form.
- **Transparency:**
  - our platform is mainly the bridge, and a team member sits with the client on every automation and
    conversation flow;
  - the cost is a one-off setup fee plus an ongoing subscription to software chosen for their needs;
  - no number is ever shown, and the price comes only after the call.
- **The only next step:** the card `book_automation_call` ("לקבוע שיחה עם נציג").
  - Approving it opens one tracked team request with topic `automation_call`. The Control Room shows it
    and `client_state.team_requests` reports it.
  - Asking again shows the same card.
  - Nothing is built before the call.

## What never happens in this flow
- **No vendor name** in any language. The vendor stays only in the admin code (`core/automation_service`).
- **No account questions, links or credentials:** never "do you have an account", never a sign-up link,
  never a credential.
- **No connect or upgrade path:** never a connect card or setup step, and never `upgrade_request`.
- **A handoff can't open it.** `automation_call` is in `agent_actions.CARD_TEAM_TOPICS`, which
  `team_topic()` ignores, so a model's handoff can never open one.
- **Old buttons.** Every step of the old connect flow (`connection_catalog.flow(..., "manychat", ...)`)
  returns the code-built pitch and `card_action`. `/api/client/connect-chat` proposes that card by code.
- **No automatic price in the upgrade panel.** Its automation entry no longer shows "starting from ₪".

## Unchanged
- The WhatsApp Business card stays "coming soon".
- The admin drawer for the vendor token is unchanged.
- The sales chat's proposal pricing (`PRICING["automation"]`) is a separate flow and was not changed.

Tests: `tests/test_chat_automation.py`, plus the vendor-name test (`tests/test_vendor_names.py`
`test_chat_automation_flow`).
