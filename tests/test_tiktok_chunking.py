"""TikTok FILE_UPLOAD chunking at the real boundaries (2026-11-17).

Production: client 27, job d4e8304966d2, test3.mp4 (18 MB) — init refused three
times with "400 invalid_params: The chunk size is invalid": we sent chunk_size
16 MiB with total_chunk_count 1 for an 18 MB file.

TikTok's Media Transfer Guide (developers.tiktok.com/doc/content-posting-api-
media-transfer-guide): chunks 5-64 MB, the final one may exceed chunk_size (up
to 128 MB) for the trailing bytes; total_chunk_count = floor(video_size /
chunk_size), 1-1000; a file under 5 MB goes whole (chunk_size = its size);
over 64 MB must be several chunks; chunks sequential, Content-Range per PUT.
What we send: ONE chunk (chunk_size == video_size) for every file up to 64 MB;
above it 16 MiB chunks (grown only past 1000 chunks), the last absorbing the rest.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from core import video_limits as vl   # noqa: E402

KB, MB, MiB, GB = 1000, 1000 * 1000, 1024 * 1024, 1000 * 1000 * 1000
SINGLE_MAX = vl.TIKTOK_SINGLE_CHUNK_MAX_BYTES


def sizes():
    """Just under, at and just over every threshold, plus even / uneven / large files."""
    out = [1, 1000, 5 * MB - 1, 5 * MB, 5 * MB + 1, 5 * MiB, 16 * MiB - 1, 16 * MiB, 16 * MiB + 1,
           18 * MB, 18 * MB + 123, 32 * MiB - 1, 32 * MiB, 32 * MiB + 1, 48 * MiB,
           SINGLE_MAX - 1, SINGLE_MAX, SINGLE_MAX + 1, 64 * MiB, 64 * MiB + 1,
           2 * 16 * MiB + 1, 80 * MiB, 100 * MB, 412 * MB, int(412.8 * MiB), 1536 * MiB,
           4 * GB, 16 * MiB * 1000, 16 * MiB * 1000 + 1, 16 * MiB * 1001, 20 * GB, 60 * GB]
    return sorted(set(out))


class TikTokChunkRules(unittest.TestCase):
    def check(self, size):
        params = vl.tiktok_init_params(size)
        chunk, count = params["chunk_size"], params["total_chunk_count"]
        plan = vl.tiktok_chunk_plan(size, chunk)
        self.assertEqual(params["video_size"], size)
        self.assertEqual(len(plan), count, size)
        # every byte once, in order: no gap, no overlap
        at = 0
        for start, length in plan:
            self.assertEqual(start, at, size)
            self.assertGreater(length, 0, size)
            at += length
        self.assertEqual(at, size, size)
        if count == 1:
            self.assertEqual(chunk, size, size)                     # one chunk ⇔ chunk_size == file
            self.assertLessEqual(size, SINGLE_MAX, size)
        else:
            self.assertGreater(size, SINGLE_MAX, size)             # only above 64 MB
            self.assertTrue(vl.TIKTOK_MIN_CHUNK_BYTES <= chunk <= vl.TIKTOK_MAX_CHUNK_BYTES, size)
            self.assertEqual(count, size // chunk, size)            # floor(video_size / chunk_size)
            self.assertLessEqual(count, vl.TIKTOK_MAX_CHUNKS, size)
            for _, length in plan[:-1]:
                self.assertEqual(length, chunk, size)
            last = plan[-1][1]
            self.assertTrue(chunk <= last < 2 * chunk, size)        # the final chunk absorbs the rest
            self.assertLessEqual(last, vl.TIKTOK_MAX_FINAL_CHUNK_BYTES, size)
        return params, plan

    def test_every_boundary(self):
        for size in sizes():
            with self.subTest(size=size):
                self.check(size)

    def test_the_production_file(self):
        size = 18 * MB + 123                       # test3.mp4 was ~18 MB
        params, plan = self.check(size)
        self.assertEqual(params, {"video_size": size, "chunk_size": size, "total_chunk_count": 1})
        self.assertNotEqual(params["chunk_size"], 16 * MiB)

    def test_between_32mib_and_64mb_is_still_one_chunk(self):
        for size in (32 * MiB + 1, 40 * MB, 60 * MB, SINGLE_MAX):
            self.assertEqual(vl.tiktok_init_params(size)["total_chunk_count"], 1, size)

    def test_just_over_64mb_is_several(self):
        params, plan = self.check(SINGLE_MAX + 1)
        self.assertEqual(params["chunk_size"], 16 * MiB)
        self.assertEqual(params["total_chunk_count"], (SINGLE_MAX + 1) // (16 * MiB))   # 3
        self.assertEqual(len(plan), 3)

    def test_even_and_uneven(self):
        even, plan = self.check(80 * MiB)
        self.assertEqual([n for _, n in plan], [16 * MiB] * 5)
        uneven, plan = self.check(80 * MiB + 777)
        self.assertEqual(plan[-1][1], 16 * MiB + 777)

    def test_412mb(self):
        params, plan = self.check(412 * MB)
        self.assertEqual(params["total_chunk_count"], (412 * MB) // (16 * MiB))   # 24
        self.assertEqual(plan[-1][1], 412 * MB - 23 * 16 * MiB)

    def test_over_1000_chunks_grows_the_chunk(self):
        params, _ = self.check(16 * MiB * 1001)
        self.assertGreater(params["chunk_size"], 16 * MiB)
        self.check(16 * MiB * 1000 + 1)               # exactly 1000 chunks: still 16 MiB

    def test_a_bad_combination_is_refused_before_the_call(self):
        with self.assertRaises(ValueError):
            vl.tiktok_init_params(0)
        with self.assertRaises(ValueError):
            vl.tiktok_init_params(65 * GB)            # > 1000 chunks of 64 MB (TikTok's own cap is far below)
        saved = vl.tiktok_chunk_size
        try:
            vl.tiktok_chunk_size = lambda size: 16 * MiB             # the old behaviour
            with self.assertRaises(ValueError):
                vl.tiktok_init_params(18 * MB)
        finally:
            vl.tiktok_chunk_size = saved

    def test_limits_match_the_docs(self):
        self.assertEqual(vl.TIKTOK_MIN_CHUNK_BYTES, 5 * MB)
        self.assertEqual(vl.TIKTOK_MAX_CHUNK_BYTES, 64 * MB)
        self.assertEqual(vl.TIKTOK_MAX_FINAL_CHUNK_BYTES, 128 * MB)
        self.assertEqual(vl.TIKTOK_MAX_CHUNKS, 1000)
        self.assertEqual(SINGLE_MAX, 64 * MB)


if __name__ == "__main__":
    unittest.main()
