"""agents/youtube_metadata — the title/description/tags/hashtags/links a client
approves before a YouTube upload — and its path through core/agent_actions.

Stdlib only; the LLM, the page inventory and the YouTube search are seams
(tests/test_youtube_upload_action.install_metadata_seams).

Run from the repo root:  python tests/test_youtube_metadata.py -v
"""
import os
import sys
import types
import unittest
from urllib.parse import parse_qs, urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import test_youtube_upload_action as up            # noqa: E402  (stubs + seams + fixtures)
from test_youtube_upload_action import DRAFT, OWN_VIDEO, PAGES, SITE, UploadActionCase, ym   # noqa: E402
from core import agent_actions as aa               # noqa: E402
from core.video_limits import TAGS_TOTAL_MAX, tags_length   # noqa: E402

GROUND = {"request": "upload the autumn collection video", "about": "", "file_name": "promo-autumn.mp4",
          "business": up.KNOWLEDGE["business"], "cached_research": {}, "site_pages": PAGES}
INV = {"domain": "shop.co.il", "pages": PAGES}


def check(**overrides):
    return ym.validate({**DRAFT, **overrides}, GROUND, INV, "he")


class Limits(unittest.TestCase):
    def test_clean_draft_passes(self):
        clean, problems = check()
        self.assertEqual(problems, [])
        self.assertLessEqual(len(clean["title"]), 100)
        self.assertEqual(clean["default_language"], "he")

    def test_title_over_100_is_a_problem(self):
        _, problems = check(title="א" * 120)
        self.assertTrue(any("title" in p for p in problems))

    def test_all_caps_title_is_a_problem(self):
        _, problems = check(title="BEST HANDMADE SHOES EVER")
        self.assertTrue(any("capitals" in p for p in problems))

    def test_html_control_and_bidi_characters_are_stripped(self):
        clean, _ = check(title="<b>נעליים</b>‮ מעוצבות\x07")
        self.assertEqual(clean["title"], "נעליים מעוצבות")

    def test_body_over_its_limit_is_a_problem(self):
        _, problems = check(description_body="מילה " * 400)
        self.assertTrue(any("description_body" in p for p in problems))

    def test_tags_are_capped_by_count_and_youtube_total_length(self):
        many = [f"תגית ארוכה מספר {i} עם רווחים" for i in range(40)]
        clean, _ = check(tags=many)
        self.assertLessEqual(len(clean["tags"]), 15)
        self.assertLessEqual(tags_length(clean["tags"]), TAGS_TOTAL_MAX)

    def test_tag_length_counts_commas_and_quotes(self):
        self.assertEqual(tags_length(["ab", "c d"]), 2 + (3 + 2) + 1)

    def test_competitor_tag_is_dropped_and_flagged(self):
        clean, problems = check(tags=["נעליים", "BigShoeCo נעליים"])
        self.assertNotIn("BigShoeCo נעליים", clean["tags"])
        self.assertTrue(any("competitor" in p for p in problems))


class Hashtags(unittest.TestCase):
    def test_fewer_than_three_is_a_problem(self):
        _, problems = check(hashtags=["#אחד", "#שתיים"])
        self.assertTrue(any("hashtags" in p for p in problems))

    def test_more_than_five_are_trimmed_to_five(self):
        clean, _ = check(hashtags=[f"#tag{i}" for i in range(9)])
        self.assertEqual(len(clean["hashtags"]), 5)

    def test_hashtags_are_normalised_and_deduplicated(self):
        clean, _ = check(hashtags=["נעליים מעוצבות", "#נעליים מעוצבות", "#עיצוב", "#עיצוב", "#עבודת-יד"])
        self.assertEqual(clean["hashtags"], ["#נעלייםמעוצבות", "#עיצוב", "#עבודתיד"])
        self.assertTrue(clean["description"].endswith(" ".join(clean["hashtags"])))


class Links(unittest.TestCase):
    def test_own_inventory_page_is_kept_and_tagged_for_youtube(self):
        clean, _ = check(links=[{"url": f"{SITE}/shoes", "label": "הקולקציה"}])
        (link,) = clean["links"]
        self.assertEqual(link["page"], f"{SITE}/shoes")
        query = parse_qs(urlsplit(link["url"]).query)
        self.assertEqual(query["utm_source"], ["youtube"])
        self.assertEqual(query["utm_medium"], ["video"])
        self.assertIn(link["url"], clean["description"])

    def test_everything_else_is_dropped(self):
        bad = ["http://www.shop.co.il/shoes",                 # not https
               "https://other-shop.co.il/shoes",              # another domain
               "https://www.shop.co.il.evil.com/shoes",       # lookalike
               "https://www.shop.co.il/invented-page",        # not in the inventory
               "javascript:alert(1)"]
        clean, _ = check(links=[{"url": u, "label": "x"} for u in bad])
        self.assertEqual(clean["links"], [])
        self.assertNotIn("evil", clean["description"])

    def test_at_most_three(self):
        pages = [{"title": f"p{i}", "url": f"{SITE}/p{i}"} for i in range(6)]
        clean, _ = ym.validate({**DRAFT, "links": [{"url": p["url"], "label": p["title"]} for p in pages]},
                               {**GROUND, "site_pages": pages}, {"domain": "shop.co.il", "pages": pages}, "he")
        self.assertEqual(len(clean["links"]), 3)

    def test_registered_domain(self):
        self.assertEqual(ym.registered_domain("www.shop.co.il"), "shop.co.il")
        self.assertEqual(ym.registered_domain("blog.example.com"), "example.com")
        self.assertNotEqual(ym.registered_domain("shop.co.il.evil.com"), "shop.co.il")

    def test_no_site_means_no_links(self):
        case = types.SimpleNamespace()
        up.install_metadata_seams(case)
        ym._site_pages = lambda cid: ("", [])
        try:
            self.assertEqual(ym.inventory(99), {"domain": "", "pages": []})
        finally:
            up.restore_metadata_seams(case)


class NoInvention(unittest.TestCase):
    def test_price_not_in_the_data(self):
        _, problems = check(description_body="נעליים מעוצבות ב-₪399 בלבד.\nקולקציית סתיו.")
        self.assertTrue(problems)

    def test_year_or_statistic_not_in_the_data(self):
        _, problems = check(title="נעליים מעוצבות מאז 1998")
        self.assertTrue(any("1998" in p for p in problems))

    def test_award_claim_not_in_the_data(self):
        _, problems = check(cta="הזוכה בפרס העיצוב")
        self.assertTrue(problems)

    def test_single_digit_structure_is_fine(self):
        _, problems = check(title="3 טיפים לבחירת נעליים מעוצבות")
        self.assertEqual(problems, [])

    def test_vendor_names_are_refused(self):
        _, problems = check(description_body="נוצר עם uallak ו-ChatGPT.\nנעליים מעוצבות.")
        self.assertTrue(any("uallak" in p for p in problems))


class Revalidate(UploadActionCase):
    def test_pipeline_output_revalidates(self):
        params = self.valid_params()
        self.assertEqual(ym.revalidate_stored(40, params), params)

    def test_tampering_with_the_stored_row_is_refused(self):
        params = self.valid_params()
        link = params["links"][0]
        tampered = [
            {**params, "description": params["description"] + "\nhttps://evil.example/x"},
            {**params, "title": "x" * 101},
            {**params, "hashtags": ["#אחר", "#עוד", "#שלישי"]},              # not in the description
            {**params, "links": [{**link, "page": "https://evil.example/shoes"}]},
            {**params, "links": [{**link, "url": link["page"]}]},           # untagged
            {**params, "body": params["body"] + " נוסף"},                   # description ≠ parts
            {**params, "tags": ["<script>"]},
            {**params, "category_id": "999"},
            {**params, "revision": 9},
            {**params, "privacy_status": "public"},
        ]
        for bad in tampered:
            with self.assertRaises(ValueError, msg=str(set(bad) ^ set(params))):
                ym.revalidate_stored(40, bad)


class ThroughTheCard(UploadActionCase):
    def test_card_shows_everything_the_click_sends(self):
        card = self.propose_upload({"drive_file_id": OWN_VIDEO})["card"]
        d = card["details"]
        self.assertEqual(d["file"], "promo-autumn.mp4")
        self.assertTrue(d["title"] and d["description"])
        self.assertEqual(len(d["hashtags"]), 3)
        self.assertEqual(d["links"], [{"label": "הקולקציה", "url": f"{SITE}/shoes"}])
        self.assertEqual(self.llm_steps, ["draft", "critique"])

    def test_unclear_subject_asks_one_question_and_stores_nothing(self):
        self.draft = {"needs_clarification": True, "question": "מה רואים בסרטון?"}
        result = self.propose_upload({"drive_file_id": OWN_VIDEO}, message="תעלה את זה")
        self.assertEqual(result, {"clarify": "מה רואים בסרטון?"})
        self.assertEqual(self.rows(), [])

    def test_one_revision_pass_then_a_plain_refusal(self):
        self.draft = {**DRAFT, "title": "נעליים מעוצבות ב-₪399 בלבד"}
        self.revised = {**DRAFT, "title": "נעליים מעוצבות ב-₪299"}
        result = self.propose_upload({"drive_file_id": OWN_VIDEO})
        self.assertEqual(result, {"refuse": ym.pick(ym.REFUSED, "he")})
        self.assertEqual(self.llm_steps, ["draft", "critique", "revise"])   # exactly one revision
        self.assertEqual(self.rows(), [])

    def test_a_fixable_draft_is_fixed_by_the_one_revision(self):
        self.draft = {**DRAFT, "title": "נעליים מעוצבות ב-₪399 בלבד"}
        self.revised = dict(DRAFT)
        self.assertIn("card", self.propose_upload({"drive_file_id": OWN_VIDEO}))

    def test_same_request_again_shows_the_same_card(self):
        first = self.propose_upload({"drive_file_id": OWN_VIDEO})["card"]
        second = self.propose_upload({"drive_file_id": OWN_VIDEO})["card"]
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.llm_steps, ["draft", "critique"])            # no second generation

    def test_client_changes_rewrite_the_card_up_to_three_times(self):
        first = self.propose_upload({"drive_file_id": OWN_VIDEO})["card"]
        ids = [first["id"]]
        for n in range(1, 4):
            self.revised = {**DRAFT, "title": f"נעליים מעוצבות — גרסה {n}"}
            card = self.propose_upload({"drive_file_id": OWN_VIDEO, "change_request": "כותרת קצרה יותר"})["card"]
            self.assertEqual(card["details"]["revision"], n)
            ids.append(card["id"])
        superseded = [r for r in self.rows() if r["id"] in ids[:-1]]
        self.assertTrue(all(r["status"] == "cancelled" and r["outcome"] == "superseded" for r in superseded))
        fourth = self.propose_upload({"drive_file_id": OWN_VIDEO, "change_request": "עוד שינוי"})
        self.assertEqual(fourth, {"refuse": ym.pick(ym.REVISION_CAP, "he")})
        open_rows = [r for r in self.rows() if r["status"] == "proposed"]
        self.assertEqual([r["id"] for r in open_rows], [ids[-1]])           # only the last card is live

    def test_change_request_reaches_the_model(self):
        self.propose_upload({"drive_file_id": OWN_VIDEO})
        self.propose_upload({"drive_file_id": OWN_VIDEO, "change_request": "תוסיף את הביטוי נעלי כלה"})
        self.assertEqual(self.llm_kwargs[-2]["client_change_request"], "תוסיף את הביטוי נעלי כלה")

    def test_the_click_rechecks_the_stored_row(self):
        card = self.propose_upload({"drive_file_id": OWN_VIDEO})["card"]
        row = next(r for r in self.rows() if r["id"] == card["id"])
        row["params"]["description"] += "\nhttps://evil.example/x"
        self.assertEqual(aa.decide(card["id"], "client", 40, "approve")["code"], "invalid_params")
        self.assertEqual(self.uploads.published, [])


class GroundingAndCost(UploadActionCase):
    def test_target_phrases_are_found(self):
        self.assertEqual(ym.target_phrases(up.KNOWLEDGE), ["נעליים מעוצבות", "נעליים בעבודת יד"])

    def test_one_cached_search_and_skipped_on_any_error(self):
        calls = []
        ym._search_videos = lambda q, lang: calls.append(q) or [{"title": "t1"}]
        self.assertEqual(ym.niche_titles("נעליים מעוצבות", "he"), ["t1"])
        ym.niche_titles("נעליים מעוצבות", "he")
        self.assertEqual(calls, ["נעליים מעוצבות"])

        def brake(q, lang):
            raise RuntimeError("YouTube daily search brake reached")
        ym._search_videos = brake
        self.assertEqual(ym.niche_titles("other query", "he"), [])

    def test_llm_cost_is_the_clients_content_and_runs_on_the_default_model(self):
        recorded = []
        saved = sys.modules.get("core.claude_json")
        sys.modules["core.claude_json"] = types.SimpleNamespace(
            safe_claude_json_call=lambda system, user, **kw: recorded.append(kw) or {"issues": []})
        try:
            up.restore_metadata_seams(self)            # the REAL _llm
            ym._llm("SYSTEM", {"a": 1}, "critique", 40, 500)
        finally:
            up.install_metadata_seams(self)
            if saved is None:
                sys.modules.pop("core.claude_json", None)
            else:
                sys.modules["core.claude_json"] = saved
        (kwargs,) = recorded
        self.assertEqual(kwargs["client_id"], 40)
        self.assertEqual(kwargs["cost_category"], "content")
        self.assertNotIn("model", kwargs)               # → claude_json.DEFAULT_MODEL


class NeverWatchesTheVideo(unittest.TestCase):
    def test_no_video_analysis_on_this_path(self):
        with open(os.path.join(ROOT, "agents", "youtube_metadata.py"), encoding="utf-8") as f:
            code = f.read().split('"""', 2)[2]          # past the module docstring
        for banned in ("ffmpeg", "read_range", "download_file", "transcri", "frame"):
            self.assertNotIn(banned, code.lower(), banned)

    def test_claude_md_says_it_is_a_future_premium_feature(self):
        with open(os.path.join(ROOT, "CLAUDE.md"), encoding="utf-8") as f:
            self.assertIn('"Watching the video" is a deliberate future premium-tier feature', f.read())


if __name__ == "__main__":
    unittest.main()
