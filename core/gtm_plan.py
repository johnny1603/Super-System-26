"""The Tag Manager plan's shape and the one naming rule — no I/O, no deps.

Shared by core/gtm_service (the write path), agents/website_agent (the dry
run, the revert) and core/agent_actions (the approval cards check a stored
plan before showing or running it). docs/GTM-SAFETY.md.
"""
import re

# Every trigger / tag / variable this system writes carries this prefix, and
# nothing without it is ever created, changed, deleted or published by us.
OUR_PREFIX = "uallak — "
CONTAINER_PATH_RE = r"accounts/\d+/containers/\d+"
PLAN_ACTIONS = ("create", "update", "reuse", "enable")
REVERT_METHODS = ("republish", "remove")


def is_ours(name) -> bool:
    return str(name or "").startswith(OUR_PREFIX)


def clean_plan(plan, revert: bool = False) -> dict:
    """A stored plan (a card's params) checked for shape: our container path,
    every named entity "uallak — ...", known actions, a sha256 signature.
    ValueError otherwise. The click re-computes the plan anyway — this keeps
    a malformed or edited row from ever being shown or run."""
    if not isinstance(plan, dict):
        raise ValueError("plan missing")
    if not re.fullmatch(CONTAINER_PATH_RE, str(plan.get("container_path") or "")):
        raise ValueError("plan container path malformed")
    if not re.fullmatch(r"[0-9a-f]{64}", str(plan.get("signature") or "")):
        raise ValueError("plan signature malformed")
    if revert:
        if plan.get("method") not in REVERT_METHODS:
            raise ValueError("revert method unknown")
        entities = list(plan.get("remove") or []) + list(plan.get("restore") or [])
    else:
        entities = plan.get("entities")
        if not isinstance(entities, list) or not entities:
            raise ValueError("plan has no entities")
    for e in entities:
        if not isinstance(e, dict):
            raise ValueError("plan entity malformed")
        if e.get("kind") == "built_in_variable":
            if revert or e.get("name") != "Click URL":
                raise ValueError("unexpected built-in variable")
        elif not is_ours(e.get("name")):
            raise ValueError("plan names an entity that is not ours")
        if not revert and e.get("action") not in PLAN_ACTIONS:
            raise ValueError("plan action unknown")
    return plan
