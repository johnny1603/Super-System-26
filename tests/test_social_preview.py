"""The Social tab's preview (2026-11-25, docs/SOCIAL-FORMATS.md §Preview).

The post is shown as the platform shows it: a frame at the format's real
ratio (from THE table), the client's actual account name, a carousel as one
card, the caption folded where the platform folds it, safe-zone bands for a
vertical post. And the card's layout no longer depends on a thumbnail being
its first child (the cause of the old narrow strip).
"""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import test_agent_characters  # noqa: F401,E402  (stubs httpx / supabase)
from core import social_formats as sf  # noqa: E402
from core import social_preview as sp  # noqa: E402


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


HTML = _read("dashboard", "client", "index.html")


def _fn(name):
    a = HTML.index(name)
    return HTML[a:HTML.index("\n}\n", a) + 3]


class Frame(unittest.TestCase):
    def test_a_feed_item_is_the_feed_default(self):
        f = sp.frame({"kind": "image", "channels": ["instagram"]})
        self.assertEqual((f["format"], f["aspect"], f["safe_zone"], f["placement"]),
                         (sf.DEFAULT_FEED, sf.FEED_ASPECT, "", "feed"))
        self.assertEqual((f["width"], f["height"]), sf.FORMATS[sf.DEFAULT_FEED].size)

    def test_a_reel_copy_card_is_vertical_with_its_channels_zone(self):
        f = sp.frame({"kind": "post", "channels": ["tiktok"], "post": {"post_format": "reel"}})
        self.assertEqual((f["format"], f["safe_zone"], f["placement"]), (sf.VERTICAL, "tiktok", "reel"))
        f = sp.frame({"kind": "post", "channels": ["instagram", "tiktok"],
                      "post": {"post_format": "single_image", "placement": "story"}})
        self.assertEqual((f["format"], f["safe_zone"], f["placement"]), (sf.VERTICAL, "cross_platform", "story"))

    def test_a_produced_slide_keeps_its_posts_format(self):
        f = sp.frame({"kind": "image", "channels": ["instagram"],
                      "post_ref": {"format": sf.VERTICAL, "placement": "story"}})
        self.assertEqual((f["format"], f["safe_zone"], f["placement"]), (sf.VERTICAL, "meta_unified", "story"))

    def test_a_design_is_its_canvas_and_a_video_is_vertical(self):
        w, h = sf.FORMATS["feed_square"].size
        f = sp.frame({"kind": "design", "design": {"spec": {"canvas": {"width": w, "height": h}}}})
        self.assertEqual(f["format"], "feed_square")
        self.assertEqual(sp.frame({"kind": "video", "channels": ["tiktok"]})["format"], sf.VERTICAL)

    def test_the_queue_view_and_the_pipeline_carry_it(self):
        self.assertIn('extra["frame"] = social_preview.frame(item)', _read("core", "social_queue.py"))
        pipe = _read("core", "post_pipeline.py")
        self.assertIn('"format": social_formats.for_post(body["post_format"], body.get("placement", "")).name', pipe)
        api = _read("core", "api_server.py")
        self.assertIn('data["profile"] = social_preview.profile(client_id)', api)
        self.assertIn('data["formats"] = social_formats.table()', api)


class Profile(unittest.TestCase):
    def test_only_names_that_were_really_read(self):
        def boom(cid):
            raise RuntimeError("graph down")
        got = sp.profile(27, _readers={"instagram": lambda cid: {"handle": "shin.sekai", "name": "Shin Sekai"},
                                       "facebook": boom, "tiktok": lambda cid: {}})
        self.assertEqual(got["accounts"], {"instagram": {"handle": "shin.sekai", "name": "Shin Sekai"}})
        self.assertIn("name", got)

    def test_the_page_never_invents_a_handle(self):
        stage = _fn("function socialStage(item)")
        self.assertIn("const handle = acct ? acct.handle : (prof.name || t('soc_prev_your_account'));", stage)
        self.assertIn("t('soc_prev_not_connected', { channel: socialChannelName(st.ch) })", stage)


class Page(unittest.TestCase):
    def test_the_card_is_named_areas_not_a_thumbnail_column(self):
        self.assertNotIn("grid-template-columns: 132px", HTML)          # the narrow strip's cause
        self.assertNotIn(".soc-prev", HTML)
        self.assertIn('grid-template-areas: "stage body";', HTML)
        self.assertIn(".soc-body { grid-area: body;", HTML)
        self.assertIn('grid-template-areas: "stage" "body";', HTML)     # a phone: preview, then the decision
        for row in ("function socialRow(item)", "function socialPostRow(item)", "function socialDesignRow(item)"):
            self.assertIn("${socialStageBox(item)}", _fn(row))

    def test_sizes_come_from_the_table_not_the_page(self):
        a = HTML.index("const socialPrev = {};")
        js = HTML[a:HTML.index("function socialCritique(id, row) {", a)]
        for literal in ("1080", "1350", "1920", "4:5", "9:16", "269", "672"):
            self.assertNotIn(literal, js, literal)
        self.assertIn('style="aspect-ratio:${Number(f.width) || 4} / ${Number(f.height) || 5}"', js)
        self.assertIn("(fm.safe_zones || {})[f.safe_zone]", js)
        self.assertIn(".caption_fold || {})[st.ch]", js)

    def test_the_frame_is_ltr_inside_the_rtl_page(self):
        self.assertIn('<div class="sp-phone${vertical ? \' sp-vertical\' : \'\'}" dir="ltr">', HTML)
        self.assertIn(".sp-phone { direction: ltr;", HTML)
        self.assertIn('<span class="sp-cap-text" dir="auto">', HTML)

    def test_caption_fold_hashtags_and_line_breaks(self):
        cap = _fn("function socialCaptionHtml(item, st, handle)")
        self.assertIn("data-sp-more=", cap)
        self.assertIn("t('soc_prev_more')", cap)
        self.assertIn('<span class="sp-tagl">$2</span>', cap)
        self.assertIn(".sp-cap .sp-cap-text { white-space: pre-wrap; }", HTML)
        self.assertEqual(sf.table()["caption_fold"], sf.CAPTION_FOLD)

    def test_a_carousel_is_one_card_decided_together(self):
        self.assertIn("social.items.filter(i => socialGroup(i)[0] === i).map(socialRow)", HTML)
        wire = _fn("function wireSocial(list)")
        self.assertIn("socialOpenBatch(socialGroupIds(id, 'waiting')", wire)
        self.assertIn("socialReject((socialCurrent(id) || {}).id || id", wire)
        self.assertIn("scroll-snap-type: x mandatory", HTML)
        self.assertIn("data-sp-dots", _fn("function socialStage(item)"))

    def test_every_new_line_is_in_five_languages(self):
        keys = set(re.findall(r"t\('(soc_prev_[a-z_]+)'", HTML)) | {"soc_prev_place_feed", "soc_prev_place_story",
                                                                    "soc_prev_place_reel", "soc_approve_all", "soc_reject_slide"}
        keys.discard("soc_prev_place_")
        self.assertGreaterEqual(len(keys), 14)
        for key in keys:
            m = re.search(r"^\s*" + key + r": \{(.*)\},?\s*$", HTML, re.M)
            self.assertTrue(m, key)
            for lang in ("he:", "en:", "fr:", "ar:", "ru:"):
                self.assertIn(lang, m.group(1), key + " " + lang)


if __name__ == "__main__":
    unittest.main()
