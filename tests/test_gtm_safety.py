"""Tag Manager safety (docs/GTM-SAFETY.md): dry-run plan, card-only publish,
rollback point, "undo my tracking", the foreign-entity guard.

Runs core.gtm_service + agents.website_agent for real against the in-memory
Tag Manager API of tests/test_google_scope_flows.FakeGtm (the only seams are
gtm_service._get/_post/_put/_delete and the activity table).

Stdlib only. Run from the repo root:  python tests/test_gtm_safety.py -v
"""
import copy
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from test_google_scope_flows import CONTAINER, FakeGtm, GtmFlow  # noqa: E402
from test_agent_actions import SAMPLE_PLAN, SAMPLE_REVERT_PLAN  # noqa: E402
from core import agent_actions as aa  # noqa: E402

GA4 = "G-ABC1234"
HEBREW = re.compile(r"[֐-׿]")
FB_PIXEL = {"name": "Facebook Pixel", "type": "html", "tagId": "66", "fingerprint": "p1",
            "path": f"{CONTAINER}/workspaces/1/tags/66", "firingTriggerId": ["2147479553"]}


def writes(api):
    return [(m, p) for m, p in api.calls if m != "GET"]


class SafetyBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        GtmFlow.setUpClass.__func__(cls)
        cls.rows = []

        def log(cid, kind, details, result=None):
            cls.rows.append({"id": len(cls.rows) + 1, "action_type": kind, "details": copy.deepcopy(details),
                             "result": result or {}, "created_at": f"2026-11-11T10:{len(cls.rows):02d}:00"})

        cls.website._log_activity = log
        cls.website._activity_rows = lambda cid, kind, limit=20: [
            r for r in reversed(cls.rows) if r["action_type"] == kind][:limit]
        cls.website.get_conversion_tracking_status = lambda cid: cls.status

    @classmethod
    def tearDownClass(cls):
        GtmFlow.tearDownClass.__func__(cls)

    def setUp(self):
        self.alerts.clear()
        self.rows.clear()
        type(self).status = {"gtm_api_connected": True, "configured_live": False}
        self.api = FakeGtm(self.gtm)
        self.gtm._get, self.gtm._post, self.gtm._delete = self.api.get, self.api.post, self.api.delete
        self.gtm._put = self.api.put
        self.api.seed_live("11", "site 2023", tag=[FB_PIXEL])

    def plan(self, **kw):
        result = self.website.plan_lead_conversion(27, GA4, **kw)
        self.assertTrue(result.get("success"), result)
        return result["plan"]

    def publish(self, via="card"):
        plan = self.plan()
        result = self.website.configure_lead_conversion(27, GA4, expected_plan=plan, via=via)
        self.assertTrue(result["success"], result)
        return result

    def gtm_drops_used_workspace(self):
        """Tag Manager deletes a workspace once a version is created from it."""
        ws = f"{CONTAINER}/workspaces/9"
        self.api.workspaces = [w for w in self.api.workspaces if w["path"] != ws]
        self.api.entities.pop(ws, None)

    def someone_publishes(self, version_id, *tags):
        snap = copy.deepcopy(self.api.versions[self.api.published["containerVersionId"]])
        snap["tag"] += [dict(t) for t in tags]
        self.api.versions[version_id] = snap
        self.api.version_names[version_id] = "their change"
        self.api.published = {"name": "their change", "containerVersionId": version_id, **copy.deepcopy(snap)}
        self.api.live = copy.deepcopy(snap)


class DryRun(SafetyBase):
    def test_the_plan_writes_nothing_and_names_everything(self):
        plan = self.plan()
        self.assertEqual(writes(self.api), [])
        self.assertEqual(plan["container_path"], CONTAINER)
        self.assertEqual(plan["container"]["account"], "1")
        self.assertEqual(plan["workspace"], {"name": "uallak lead tracking", "reused": False})
        self.assertEqual(plan["entities"], [
            {"kind": "trigger", "name": "uallak — form submission", "action": "create"},
            {"kind": "tag", "name": "uallak — GA4 generate_lead", "action": "create"}])
        self.assertEqual(plan["version_name"], "uallak conversion tracking")
        self.assertEqual(plan["rollback_point"], {"version_id": "11", "name": "site 2023"})
        self.assertRegex(plan["signature"], r"^[0-9a-f]{64}$")

    def test_container_name_and_public_id_come_from_the_connection_view(self):
        conn = sys.modules["core.gtm_connection"]
        conn.connection_view = lambda cid: {"container_path": CONTAINER, "container": {
            "name": "shin-se-kai.co.il 2023", "public_id": "GTM-NZRNDQN", "account_name": "Shin Sekai"}}
        try:
            box = self.plan()["container"]
        finally:
            del conn.connection_view
        self.assertEqual((box["name"], box["public_id"], box["account"]),
                         ("shin-se-kai.co.il 2023", "GTM-NZRNDQN", "Shin Sekai"))

    def test_a_type_already_live_is_skipped_and_its_tag_named(self):
        type(self).status = {"gtm_api_connected": True, "configured_live": True, "live_types": ["form_submit"],
                             "lead_event_tags": [{"tag_name": "GA4 - lead (agency)"}]}
        plan = self.plan(types=["form_submit", "whatsapp_click"])
        self.assertEqual(plan["types"], ["whatsapp_click"])
        self.assertEqual(plan["skipped"], [{"type": "form_submit", "tags": ["GA4 - lead (agency)"]}])
        self.assertEqual(plan["entities"], [
            {"kind": "built_in_variable", "name": "Click URL", "action": "enable"},
            {"kind": "trigger", "name": "uallak — WhatsApp click", "action": "create"},
            {"kind": "tag", "name": "uallak — GA4 click_whatsapp", "action": "create"}])

    def test_everything_live_is_refused_with_no_plan(self):
        type(self).status = {"gtm_api_connected": True, "configured_live": True, "live_types": ["form_submit"]}
        self.assertEqual(self.website.plan_lead_conversion(27, GA4)["reason"], "already_live")

    def test_our_own_entities_are_reused_or_updated_never_duplicated(self):
        trigger = {**self.gtm.form_submit_trigger_body(), "triggerId": "41", "fingerprint": "t1",
                   "path": f"{CONTAINER}/workspaces/1/triggers/41"}
        tag = {**self.gtm.ga4_lead_tag_body("G-OLD0000", "41"), "tagId": "42", "fingerprint": "g1",
               "path": f"{CONTAINER}/workspaces/1/tags/42"}
        self.api.seed_live("11", "site 2023", trigger=[trigger], tag=[FB_PIXEL, tag])
        self.assertEqual([e["action"] for e in self.plan()["entities"]], ["reuse", "update"])

    def test_the_store_plan_uses_the_same_names_as_the_write_path(self):
        url = "https://app.uallak.com/api/store/purchase/abc." + "0" * 64
        entities = self.gtm.plan_entities({}, ["store_sales"], GA4, "order-received", url)
        self.assertEqual([e["name"] for e in entities],
                         list(self.gtm.PURCHASE["vars"].values())
                         + [self.gtm.PURCHASE["trigger"], self.gtm.PURCHASE["tag"], self.gtm.PURCHASE_BEACON["tag"]])
        self.assertTrue(all(self.gtm.is_ours(e["name"]) and e["action"] == "create" for e in entities))


class PublishGuards(SafetyBase):
    def test_the_card_path_publishes_and_saves_the_rollback_point(self):
        result = self.publish()
        self.assertEqual(result["previous_version_id"], "11")
        self.assertEqual([r["action_type"] for r in self.rows], ["website_conversion_configured"])
        d = self.rows[0]["details"]
        self.assertEqual((d["via"], d["previous_version_id"], d["previous_version_name"], d["new_version_id"]),
                         ("card", "11", "site 2023", "12"))
        self.assertEqual(d["container_path"], CONTAINER)
        self.assertEqual([e["name"] for e in d["entities"]], ["uallak — form submission", "uallak — GA4 generate_lead"])
        # Someone else's tag is still live, untouched
        self.assertIn("Facebook Pixel", [t["name"] for t in self.api.published["tag"]])

    def test_an_admin_route_logs_that_it_bypassed_the_card(self):
        result = self.website.configure_lead_conversion(27, GA4, via="admin_key")
        self.assertTrue(result["success"], result)
        self.assertEqual([r["action_type"] for r in self.rows],
                         ["gtm_publish_without_card", "website_conversion_configured"])
        self.assertEqual(self.rows[0]["details"]["via"], "admin_key")

    def test_a_stale_card_is_refused_before_any_write(self):
        result = self.website.configure_lead_conversion(27, GA4, expected_plan={"signature": "0" * 64}, via="card")
        self.assertEqual(result["reason"], "gtm_plan_changed")
        self.assertEqual(writes(self.api), [])
        self.assertEqual(self.rows, [])

    def test_a_changed_live_version_invalidates_the_card(self):
        plan = self.plan()
        self.someone_publishes("13", {"name": "Hotjar", "type": "html", "tagId": "70", "path": "x/tags/70"})
        result = self.website.configure_lead_conversion(27, GA4, expected_plan=plan, via="card")
        self.assertEqual(result["reason"], "gtm_plan_changed")
        self.assertEqual(writes(self.api), [])

    def test_a_foreign_change_in_our_leftover_workspace_is_refused_and_alerted(self):
        ws = f"{CONTAINER}/workspaces/8"
        self.api.workspaces.append({"path": ws, "name": "uallak lead tracking"})
        self.api.entities[ws] = {"trigger": [], "variable": [],
                                 "tag": [dict(FB_PIXEL), {"name": "Someone's draft", "type": "html", "path": f"{ws}/tags/3"}]}
        self.assertEqual(self.website.plan_lead_conversion(27, GA4)["reason"], "gtm_foreign_entity")
        self.assertEqual(len(self.alerts), 1)
        self.assertIn("Someone's draft", self.alerts[0][0])
        result = self.website.configure_lead_conversion(27, GA4, via="card")
        self.assertEqual(result["reason"], "gtm_foreign_entity")
        self.assertEqual(writes(self.api), [])

    def test_publish_refuses_a_workspace_holding_someone_elses_change(self):
        ws = f"{CONTAINER}/workspaces/9"
        self.api.entities[ws] = {"trigger": [], "variable": [],
                                 "tag": [dict(FB_PIXEL), {"name": "Draft by agency", "type": "html", "path": f"{ws}/tags/4"}]}
        with self.assertRaises(self.gtm.GtmForeignEntity):
            self.gtm.publish_workspace("rt", ws)
        self.assertFalse(any(p.endswith(":create_version") for _m, p in self.api.calls))

    def test_no_write_ever_names_an_entity_that_is_not_ours(self):
        with self.assertRaises(self.gtm.GtmForeignEntity):
            self.gtm.ensure_entity("rt", f"{CONTAINER}/workspaces/9", "tags", {"name": "Facebook Pixel"},
                                   lambda e: True)
        self.assertEqual(self.api.calls, [])

    def test_foreign_changes_reads_every_entity_kind(self):
        f = self.gtm.foreign_changes
        self.assertEqual(f([{"tag": {"name": "uallak — x"}}, {"builtInVariable": {"type": "clickUrl"}}]), [])
        self.assertEqual(f([{"folder": {"name": "Agency"}}]), ["folder: Agency"])
        self.assertEqual(f([{"builtInVariable": {"type": "pageUrl"}}]), ["built-in variable: pageUrl"])
        self.assertEqual(f([{}]), ["unreadable change"])


class Rollback(SafetyBase):
    def test_undo_republishes_the_saved_version_when_nothing_changed_since(self):
        self.publish()
        result = self.website.plan_revert_conversion(27)
        self.assertTrue(result["success"], result)
        plan = result["plan"]
        self.assertEqual(plan["method"], "republish")
        self.assertEqual(plan["target_version"]["version_id"], "11")
        self.assertEqual(sorted(e["name"] for e in plan["remove"]),
                         ["uallak — GA4 generate_lead", "uallak — form submission"])
        self.api.calls.clear()
        done = self.website.revert_conversion(27, expected_plan=plan, via="card")
        self.assertTrue(done["success"], done)
        self.assertEqual(writes(self.api), [("POST", f"{CONTAINER}/versions/11:publish")])
        self.assertEqual(self.api.published["containerVersionId"], "11")
        self.assertEqual([t["name"] for t in self.api.published["tag"]], ["Facebook Pixel"])
        row = self.rows[-1]
        self.assertEqual((row["action_type"], row["details"]["method"], row["details"]["from_version_id"],
                          row["details"]["new_version_id"]), ("gtm_conversion_reverted", "republish", "12", "11"))
        # Nothing of ours is live any more: a second undo has nothing to do
        self.assertEqual(self.website.plan_revert_conversion(27)["reason"], "nothing_to_revert")

    def test_undo_after_later_changes_deletes_only_uallak_entities(self):
        self.publish()
        self.gtm_drops_used_workspace()
        hotjar = {"name": "Hotjar", "type": "html", "tagId": "70", "fingerprint": "h1",
                  "path": f"{CONTAINER}/workspaces/3/tags/70", "firingTriggerId": ["2147479553"]}
        self.someone_publishes("13", hotjar)
        plan = self.website.plan_revert_conversion(27)["plan"]
        self.assertEqual(plan["method"], "remove")
        ours = {e["path"] for e in self.api.versions["13"]["tag"] + self.api.versions["13"]["trigger"]
                if e["name"].startswith("uallak — ")}
        self.api.calls.clear()
        done = self.website.revert_conversion(27, expected_plan=plan, via="card")
        self.assertTrue(done["success"], done)
        deleted = [p for m, p in self.api.calls if m == "DELETE"]
        self.assertEqual(set(deleted), ours)
        self.assertEqual(self.api.workspaces[-1]["name"], "uallak tracking removal")
        live = self.api.published
        self.assertEqual(live["name"], "uallak tracking removed")
        self.assertEqual(sorted(t["name"] for t in live["tag"]), ["Facebook Pixel", "Hotjar"])
        self.assertEqual(live["trigger"], [])

    def test_undo_refuses_when_another_tag_depends_on_ours(self):
        self.publish()
        self.gtm_drops_used_workspace()
        our_trigger = self.api.published["trigger"][0]["triggerId"]
        self.someone_publishes("13", {"name": "Agency lead pixel", "type": "html", "tagId": "71",
                                      "path": "x/tags/71", "firingTriggerId": [our_trigger]})
        self.api.calls.clear()
        result = self.website.plan_revert_conversion(27)
        self.assertEqual(result["reason"], "gtm_revert_blocked")
        self.assertEqual(len(self.alerts), 1)
        self.assertEqual(writes(self.api), [])

    def test_a_stale_undo_card_changes_nothing(self):
        self.publish()
        plan = self.website.plan_revert_conversion(27)["plan"]
        self.api.calls.clear()
        result = self.website.revert_conversion(27, expected_plan={**plan, "signature": "0" * 64}, via="card")
        self.assertEqual(result["reason"], "gtm_plan_changed")
        self.assertEqual(writes(self.api), [])

    def test_nothing_of_ours_live_means_nothing_to_undo(self):
        self.assertEqual(self.website.plan_revert_conversion(27)["reason"], "nothing_to_revert")

    def test_remove_ours_never_deletes_another_name(self):
        ws = f"{CONTAINER}/workspaces/9"
        self.api.entities[ws] = {"trigger": [{"name": "uallak — phone click", "path": f"{ws}/triggers/5"}],
                                 "tag": [dict(FB_PIXEL), {"name": "uallak — GA4 click_to_call", "path": f"{ws}/tags/6"}],
                                 "variable": [{"name": "Agency var", "path": f"{ws}/variables/7"}]}
        removed = self.gtm.remove_ours("rt", ws)
        self.assertEqual([r["name"] for r in removed], ["uallak — GA4 click_to_call", "uallak — phone click"])
        self.assertEqual(self.api.deleted, [f"{ws}/tags/6", f"{ws}/triggers/5"])

    def test_history_lists_publish_and_undo_newest_first(self):
        self.publish(via="control_room")
        plan = self.website.plan_revert_conversion(27)["plan"]
        self.website.revert_conversion(27, expected_plan=plan, via="card")
        history = self.website.gtm_history(27)
        self.assertEqual([h["type"] for h in history],
                         ["gtm_conversion_reverted", "website_conversion_configured", "gtm_publish_without_card"])
        self.assertEqual(history[1]["previous_version_id"], "11")
        self.website._gtm_connection = lambda cid: {"access_token": "rt", "account_id": CONTAINER}
        point = self.website.gtm_rollback_point(27)
        self.assertEqual((point["new_version_id"], point["previous_version_id"]), ("12", "11"))


class CardText(unittest.TestCase):
    PLAN = {**SAMPLE_PLAN, "ga4_measurement_id": GA4,
            "entities": SAMPLE_PLAN["entities"] + [{"kind": "built_in_variable", "name": "Click URL", "action": "enable"},
                                                   {"kind": "tag", "name": "uallak — GA4 click_whatsapp",
                                                    "action": "update"}],
            "skipped": [{"type": "phone_click", "tags": ["Old call tag"]}]}

    def test_the_plan_reads_in_every_language(self):
        texts = aa._plan_what({"plan": self.PLAN})
        self.assertEqual(set(texts), set(aa.LANGS))
        for lang, text in texts.items():
            with self.subTest(lang=lang):
                for part in ("GTM-NZRNDQN", "shin-se-kai.co.il 2023", "Shin Sekai", "uallak lead tracking",
                             "uallak — form submission", "uallak — GA4 generate_lead",
                             "uallak — GA4 click_whatsapp", "Click URL", "Old call tag",
                             "uallak conversion tracking", "#11"):
                    self.assertIn(part, text)
                self.assertTrue(text.endswith(aa._pick(aa._PLAN_TXT["ask"], lang)))
                for word in ("configure_lead_conversion", "error", "exception", "scope", "token"):
                    self.assertNotIn(word, text.lower())
                if lang != "he":
                    self.assertIsNone(HEBREW.search(text), text)

    def test_the_undo_card_lists_exactly_what_is_removed(self):
        texts = aa._plan_what({"plan": SAMPLE_REVERT_PLAN}, revert=True)
        for lang, text in texts.items():
            with self.subTest(lang=lang):
                self.assertIn("uallak — GA4 generate_lead", text)
                self.assertIn("uallak — form submission", text)
                self.assertIn("#11", text)
                if lang != "he":
                    self.assertIsNone(HEBREW.search(text), text)
        remove = aa._plan_what({"plan": {**SAMPLE_REVERT_PLAN, "method": "remove"}}, revert=True)
        self.assertIn("uallak tracking removal", remove["en"])

    def test_the_card_shows_the_stored_plan(self):
        row = {"id": 1, "action": "configure_conversion", "client_id": 27, "status": "proposed",
               "params": {"plan": self.PLAN}, "approver": "client"}
        self.assertTrue(aa.card(row, "client", "en")["what"].startswith("Here is exactly what I'll do"))
        row = {**row, "action": "revert_conversion", "params": {"plan": SAMPLE_REVERT_PLAN}}
        self.assertIn("uallak — form submission", aa.card(row, "client", "fr")["what"])

    def test_every_refusal_exists_in_every_language(self):
        for action, codes in (("configure_conversion", aa._GTM_REFUSAL_CODES),
                              ("revert_conversion", aa._GTM_REVERT_REFUSAL_CODES)):
            refusals = aa.ACTIONS[action]["refusals"]
            self.assertEqual(set(codes.values()), set(refusals), action)
            for code, texts in refusals.items():
                self.assertEqual(set(texts), set(aa.LANGS), (action, code))

    def test_a_stored_plan_must_name_only_our_entities(self):
        from core import gtm_plan
        gtm_plan.clean_plan(self.PLAN)
        gtm_plan.clean_plan(SAMPLE_REVERT_PLAN, revert=True)
        bad = [{**self.PLAN, "entities": [{"kind": "tag", "name": "Facebook Pixel", "action": "update"}]},
               {**self.PLAN, "signature": "short"},
               {**self.PLAN, "container_path": "accounts/1"},
               {**self.PLAN, "entities": [{"kind": "tag", "name": "uallak — x", "action": "delete"}]},
               {**self.PLAN, "entities": []}]
        for plan in bad:
            with self.subTest(plan=plan.get("entities")), self.assertRaises(ValueError):
                gtm_plan.clean_plan(plan)
        with self.assertRaises(ValueError):
            gtm_plan.clean_plan({**SAMPLE_REVERT_PLAN, "remove": [{"kind": "tag", "name": "Hotjar"}]}, revert=True)

    def test_a_card_without_a_plan_never_publishes(self):
        with self.assertRaises(aa.ActionFailed) as e:
            aa._run_configure_conversion(27, {}, None)
        self.assertEqual(e.exception.code, "gtm_plan_changed")


from test_agent_actions import ActionTestCase  # noqa: E402


class Cards(ActionTestCase):
    def test_the_setup_card_carries_the_plan_and_runs_it_once(self):
        result = self.propose(40, "client_chat", "configure_conversion")
        self.assertEqual(result["status"], "proposed")
        self.assertTrue(result["card"]["what"].startswith("זה בדיוק מה שאעשה"))
        self.assertEqual(self.rows()[0]["params"]["plan"]["signature"], SAMPLE_PLAN["signature"])
        # The same plan again shows the same card, never a second one
        self.assertEqual(self.propose(40, "client_chat", "configure_conversion")["card"]["id"], result["card"]["id"])
        aa.decide(result["card"]["id"], "client", 40, "approve")
        (args, kwargs), = self.website.configure_lead_conversion.calls
        self.assertEqual((kwargs["via"], kwargs["expected_plan"]["signature"]), ("card", SAMPLE_PLAN["signature"]))

    def test_a_refused_dry_run_shows_one_plain_line_and_no_card(self):
        self.website.plan_lead_conversion = lambda cid, ga4="", types=None: {
            "success": False, "reason": "gtm_foreign_entity", "errors": ["tag: Facebook Pixel"]}
        result = self.propose(40, "client_chat", "configure_conversion")
        self.assertEqual(result["refuse"], aa.ACTIONS["configure_conversion"]["refusals"]["gtm_foreign_entity"]["he"])
        self.assertNotIn("Facebook", result["refuse"])
        self.assertEqual(self.rows(), [])

    def test_a_grant_problem_in_the_dry_run_offers_the_reconnect_card(self):
        self.website.plan_lead_conversion = lambda cid, ga4="", types=None: {
            "success": False, "reason": "gtm_reconnect_needed", "errors": ["x"]}
        result = self.propose(40, "client_chat", "configure_conversion")
        self.assertEqual(result["card"]["action"], "connect_tag_manager")

    def test_the_undo_card_is_one_approval_and_runs_the_plan_it_showed(self):
        result = self.propose(40, "client_chat", "revert_conversion")
        self.assertEqual(result["status"], "proposed")
        self.assertIn("uallak — form submission", result["card"]["what"])
        aa.decide(result["card"]["id"], "client", 40, "approve")
        (args, kwargs), = self.website.revert_conversion.calls
        self.assertEqual((kwargs["via"], kwargs["expected_plan"]["method"]), ("card", "republish"))

    def test_nothing_to_undo_is_a_plain_line(self):
        self.website.plan_revert_conversion = lambda cid: {"success": False, "reason": "nothing_to_revert"}
        result = self.propose(40, "client_chat", "revert_conversion")
        self.assertEqual(result["refuse"], aa.ACTIONS["revert_conversion"]["refusals"]["nothing_to_revert"]["he"])


if __name__ == "__main__":
    unittest.main()
