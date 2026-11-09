"""The RX_VK_FORCE_LINEAR diagnostic switch is off unless set and does not change what a sampler
is cached as (gpu/vk/vk_draw.cpp and readme.md; no game, GPU or compiler needed).

#1117's blocky light halos come from a few NEAREST samplers reading a low-resolution light
buffer. The switch is for finding out which ones, so the two things that must never regress are
that it costs nothing when off and that it cannot give two sampler ids a shared cache entry.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DRAW = (ROOT / "gpu/vk/vk_draw.cpp").read_text(encoding="utf-8")
README = (ROOT / "readme.md").read_text(encoding="utf-8")
SHADER = (ROOT / "gpu/vk/vk_shader.cpp").read_text(encoding="utf-8")


def function_body(name, src=DRAW):
    """The body of a free function, by brace count (the file's comments hold no braces)."""
    start = src.index("static void " + name + "(") if name == "force_linear" else src.index(name + "(")
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if not depth:
                return src[i + 1:j]
    raise AssertionError("unterminated " + name)


BODY = function_body("force_linear")


class IdListTests(unittest.TestCase):
    """The spec of the id list: whole comma-separated tokens, never a prefix of another id."""

    @staticmethod
    def listed(ids, id_):
        text = str(id_)
        return any(part == text for part in ids.split(",") if ids)

    def test_exact_token_not_prefix(self):
        self.assertTrue(self.listed("258,268,318", 268))
        self.assertFalse(self.listed("258,268,318", 26))
        self.assertFalse(self.listed("258,268,318", 2680))
        self.assertFalse(self.listed("268", 26))

    def test_empty_and_trailing(self):
        self.assertFalse(self.listed("", 268))
        self.assertTrue(self.listed("268,", 268))
        self.assertTrue(self.listed("258, 268", 268) is False)  # no whitespace trimming by design

    def test_modes(self):
        apply_mode = lambda mode, listed: bool(mode) and (mode < 4 or (mode == 4 and listed) or (mode == 5 and not listed))
        self.assertFalse(apply_mode(0, True))
        for mode in (1, 2, 3):
            self.assertTrue(apply_mode(mode, False))
        self.assertTrue(apply_mode(4, True))
        self.assertFalse(apply_mode(4, False))
        self.assertFalse(apply_mode(5, True))
        self.assertTrue(apply_mode(5, False))


class SourceContractTests(unittest.TestCase):
    def test_off_unless_set_and_zero_cost(self):
        self.assertIn('getenv("RX_VK_FORCE_LINEAR") ? atoi(getenv("RX_VK_FORCE_LINEAR")) : 0', BODY)
        self.assertIn("if (!mode || compare) return;", BODY)
        # Read once into a function-local static, not once per sampler, and off by default.
        self.assertIn("static const int mode =", BODY)
        self.assertIn("static const char* ids =", BODY)
        self.assertEqual(BODY.count("static const int mode"), 1)

    def test_comparison_samplers_untouched(self):
        self.assertIn("if (!mode || compare) return;", BODY)

    def test_only_nearest_filters_change_and_mipmaps_are_kept(self):
        for nearest, linear in (
            ("NVN_MIN_FILTER_NEAREST", "NVN_MIN_FILTER_LINEAR"),
            ("NVN_MIN_FILTER_NEAREST_MIPMAP_NEAREST", "NVN_MIN_FILTER_LINEAR_MIPMAP_NEAREST"),
            ("NVN_MIN_FILTER_NEAREST_MIPMAP_LINEAR", "NVN_MIN_FILTER_LINEAR_MIPMAP_LINEAR"),
        ):
            self.assertRegex(BODY, r"p\.minf == %s\) p\.minf = %s;" % (nearest, linear))
        self.assertIn("p.magf == NVN_MAG_FILTER_NEAREST) p.magf = NVN_MAG_FILTER_LINEAR;", BODY)
        # No mipmapMode assignment: the mip filter of every mapping above is unchanged.
        self.assertNotIn("mipmapMode", BODY)

    def test_mag_and_min_modes_are_independent(self):
        self.assertIn("if (mode != 3 &&", BODY)
        self.assertIn("if (mode != 2) {", BODY)

    def test_id_match_compares_the_whole_token(self):
        self.assertIn("(size_t)(e - q) == len", BODY)
        self.assertIn("!strncmp(q, buf, len)", BODY)
        self.assertIn('getenv("RX_VK_FORCE_LINEAR_IDS")', BODY)

    def test_applied_before_the_sampler_cache_key(self):
        # p is mixed into `key` below. Changing p first keeps an id whitelist from handing one
        # sampler to another id with the same original parameters.
        make = DRAW[DRAW.index("VkSampler sampler_make(int id, bool compare) {"):]
        self.assertLess(make.index("force_linear(id, compare, p);"), make.index("uint64_t key = mix("))
        self.assertLess(make.index("force_linear(id, compare, p);"), make.index("const uint8_t* b ="))

    def test_not_part_of_the_program_cache_stamp(self):
        # A switch counted by disk_stamp() would throw away the multi-GB program cache on every
        # experiment. Neither name starts with one of the prefixes it hashes.
        prefixes = re.findall(r'"(RX_VK_[A-Z0-9_]+|EDEN_)"', SHADER[SHADER.index("uint64_t disk_stamp()"):])
        self.assertTrue(prefixes)
        for name in ("RX_VK_FORCE_LINEAR", "RX_VK_FORCE_LINEAR_IDS"):
            self.assertFalse(any(name.startswith(p) for p in prefixes), name + " would invalidate the cache")

    def test_documented_beside_the_other_test_switches(self):
        section = README[README.index("<summary>Switches for testing"):]
        section = section[:section.index("</details>")]
        self.assertIn("RX_VK_FORCE_LINEAR=<mode>", section)
        self.assertIn("RX_VK_FORCE_LINEAR_IDS", section)
        self.assertIn("#1117", section)
        self.assertIn("diagnostic switch, not a fix", section)
        self.assertIn("Off unless set", section)

    def test_no_game_files_added(self):
        for name in ("hookshot_env.txt", "hookshot_options.ini", "hookshot_vk.dll", "image.bin"):
            self.assertFalse((ROOT / name).exists(), name)


if __name__ == "__main__":
    unittest.main()
